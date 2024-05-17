'''Written by Cael Shoop.'''

import os
import json
import random
import logging
import requests
import traceback
from asyncio import Lock
from typing import Literal
from dotenv import load_dotenv
from datetime import datetime, timedelta
from discord import app_commands, Interaction, Intents, Client, ButtonStyle, EventStatus, EntityType, TextChannel, VoiceChannel, Message, ScheduledEvent, Guild, PrivacyLevel, User, utils, File
from discord.ui import View, Button, Modal, TextInput
from discord.ext import tasks

from persistence import Persistence
from participant import Participant, TimeBlock

# .env
load_dotenv()

# Logger setup
logger = logging.getLogger("Event Scheduler")
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')

file_handler = logging.FileHandler('scheduler.log')
file_handler.setLevel(logging.WARNING)
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Persistence
persist = Persistence('data.json')

# Literals
INCLUDE = 'INCLUDE'
EXCLUDE = 'EXCLUDE'
INCLUDE_EXCLUDE: Literal = Literal[INCLUDE, EXCLUDE]


# Return an HH:MM format string from the total minutes
def mins_to_hrs_mins_string(minutes: int):
    hours = minutes//60
    minutes %= 60
    if hours < 10:
        hours = f'0{hours}'
    else:
        hours = f'{hours}'
    if minutes < 10:
        minutes = f'0{minutes}'
    else:
        minutes = f'{minutes}'
    return f'{hours}:{minutes}'


# Add a 0 if the digit is < 10
def double_digit_string(digit_string: str):
    if int(digit_string) < 10 and len(digit_string) == 1:
        digit_string = '0' + digit_string
    return digit_string


def main():
    class SchedulerClient(Client):
        def __init__(self, intents):
            super(SchedulerClient, self).__init__(intents=intents)
            self.tree = app_commands.CommandTree(self)
            self.loaded_json = False
            self.events = []

        async def retrieve_events(self):
            if not self.loaded_json:
                self.loaded_json = True
                events_data = persist.read()
                if events_data:
                    for event_data in events_data['events']:
                        try:
                            event = await Event.from_dict(event_data)
                            if not event:
                                raise Exception(f'Event failed to be created')
                            try:
                                event.avail_buttons = AvailabilityButtons(event)
                                await event.interaction_message.edit(view=event.avail_buttons)
                            except:
                                raise Exception(f'Event does not have an interaction message')
                            if event.event_buttons_message:
                                event.event_buttons = EventButtons(event)
                                await event.event_buttons_message.edit(view=event.event_buttons)
                            self.events.append(event)
                            logger.info(f'{event.name}: event loaded and added to client event list')
                        except Exception as e:
                            logger.error(f'Could not add event to client event list: {e}')
                else:
                    logger.info(f'No json data found')

        def get_events_dict(self):
            events_data = {}
            events_data['events'] = [event.to_dict() for event in self.events]
            return events_data

        async def sync_commands(self):
            for guild in self.guilds:
                await self.tree.sync(guild=guild)

    discord_token = os.getenv('DISCORD_TOKEN')
    client = SchedulerClient(intents=Intents.all())

    class Event:
        def __init__(self, 
            name: str,
            voice_channel: VoiceChannel,
            participants: list,
            guild: Guild,
            text_channel: TextChannel,
            image_url: str = '',
            duration = timedelta(minutes=30),
            start_time: datetime = None,
            interaction_message = None,
            avail_buttons = None,
            responded_message = None,
            event_buttons_message = None,
            event_buttons = None,
            event_buttons_msg_content_pt1: str = '',
            event_buttons_msg_content_pt2: str = '',
            event_buttons_msg_content_pt3: str = '',
            event_buttons_msg_content_pt4: str = '',
            ready_to_create: bool = False,
            created: bool = False,
            started: bool = False,
            scheduled_event = None,
            changed: bool = False,
            unavailable: bool = False
        ):
            self.name = name
            self.guild = guild
            self.entity_type = EntityType.voice
            self.text_channel = text_channel
            self.interaction_message = interaction_message
            self.responded_message = responded_message
            self.voice_channel = voice_channel
            self.privacy_level = PrivacyLevel.guild_only
            self.participants = participants
            self.image_url = image_url
            self.avail_buttons: AvailabilityButtons = avail_buttons
            self.event_buttons_message: Message = event_buttons_message
            self.event_buttons: EventButtons = event_buttons
            self.event_buttons_msg_content_pt1: str = event_buttons_msg_content_pt1
            self.event_buttons_msg_content_pt2: str = event_buttons_msg_content_pt2
            self.event_buttons_msg_content_pt3: str = event_buttons_msg_content_pt3
            self.event_buttons_msg_content_pt4: str = event_buttons_msg_content_pt4
            self.five_minute_warning_flag = False
            self.ready_to_create = ready_to_create
            self.created = created
            self.started = started
            self.scheduled_event: ScheduledEvent = scheduled_event
            self.changed = changed
            self.start_time: datetime = start_time
            self.duration = duration
            self.mins_until_start: int = 0
            self.countdown_check_flag = False
            self.unavailable = unavailable

        async def intersect_time_blocks(self, timeblocks1: list, timeblocks2: list):
            intersected_time_blocks = []
            for block1 in timeblocks1:
                for block2 in timeblocks2:
                    start_time = max(block1.start_time, block2.start_time)
                    end_time = max(block1.end_time, block2.end_time)
                    if start_time < end_time:
                        intersected_time_blocks.append(TimeBlock(start_time, end_time))
            return intersected_time_blocks

        # Compare availabilities of all subscribed participants
        async def compare_availabilities(self):
            subbed_participants = []
            for participant in self.participants:
                if participant.subscribed:
                    subbed_participants.append(participant)

            current_time = datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(minutes=5)

            # Check if all participants are available in 5 minutes
            all_participants_available = True
            for participant in subbed_participants:
                if not participant.is_available_at(current_time, self.duration):
                    all_participants_available = False
                    break

            if all_participants_available:
                self.start_time = current_time
                self.ready_to_create = True
                return

            # Otherwise, find the earliest common availability
            available_timeblocks = []
            for participant in subbed_participants:
                available_timeblocks.append(participant.availability)
            intersected_timeblocks = available_timeblocks[0]
            for timeblocks in available_timeblocks[1:]:
                intersected_timeblocks = await self.intersect_time_blocks(intersected_timeblocks, timeblocks)

            for timeblock in intersected_timeblocks:
                if timeblock.duration >= self.duration:
                    self.start_time = timeblock.start_time
                    self.ready_to_create = True
                    return

        async def make_scheduled_event(self):
            self.scheduled_event = await self.guild.create_scheduled_event(name=self.name, description='Bot-generated event', start_time=self.start_time, entity_type=self.entity_type, channel=self.voice_channel, privacy_level=self.privacy_level)
            self.ready_to_create = False
            self.created = True
            if self.image_url:
                try:
                    response = requests.get(self.image_url)
                    if response.status_code == 200:
                        await self.scheduled_event.edit(image=response.content)
                        logger.info(f'{self.name}: Processed image')
                    else:
                        self.image_url = ''
                        logger.warn(f'{self.name}: Failed to get image')
                except Exception as e:
                    self.image_url = ''
                    logger.exception(f'{self.name}: Failed to process image: {e}')
            logger.info(f'{self.name}: Created event starting {self.start_time.strftime("%m/%d/%Y: %H:%M")} ET')

        # Get a string of participant mentions/names
        def get_names_string(self, subscribed_only: bool = False, unsubscribed_only: bool = False, unanswered_only: bool = False, mention: bool = False):
            names = []
            mentions = ''

            if subscribed_only and unsubscribed_only:
                subscribed_only = False
                unsubscribed_only = False

            for participant in self.participants:
                if mention:
                    name_string = f"{participant.member.mention} "
                else:
                    name_string = f"{participant.member.name}"

                # No conditions are true
                if (not subscribed_only) and (not unsubscribed_only) and (not unanswered_only):
                    mentions += name_string
                    names.append(name_string)

                # One condition is true
                if (subscribed_only and participant.subscribed) and (not unsubscribed_only) and (not unanswered_only):
                    mentions += name_string
                    names.append(name_string)
                if (not subscribed_only) and (unsubscribed_only and not participant.subscribed) and (not unanswered_only):
                    mentions += name_string
                    names.append(name_string)
                if (not subscribed_only) and (not unsubscribed_only) and (unanswered_only and not participant.answered):
                    mentions += name_string
                    names.append(name_string)

                # Two conditions are true
                if (subscribed_only and participant.subscribed) and (unanswered_only and not participant.answered):
                    mentions += name_string
                    names.append(name_string)
                if (unsubscribed_only and not participant.subscribed) and (unanswered_only and not participant.answered):
                    mentions += name_string
                    names.append(name_string)

            if mention:
                return mentions
            return ", ".join(names)

        # Get a participant from the event with a username
        def get_participant(self, username: str):
            for participant in self.participants:
                if participant.member.name == username:
                    return participant

        # Has shares participants with another event
        def shares_participants(self, event):
            for self_participant in self.participants:
                for other_participant in event.participants:
                    if self_participant.member == other_participant.member:
                        return other_participant
            return None

        # Get availability for participant from another event
        def get_other_availability(self, participant: Participant):
            for other_event in client.events:
                if other_event != self:
                    for other_participant in other_event.participants:
                        if other_participant.member.name == participant.member.name:
                            return participant.availability
            return None

        # Get duration value
        def get_duration_minutes(self):
            return int(self.duration.total_seconds()//60)

        # Get start time string
        def get_start_time_string(self):
            return f'{self.start_time.strftime("%m/%d at %H:%M")} ET'

        # All participants have responded
        def has_everyone_answered(self):
            for participant in self.participants:
                if participant.subscribed and not participant.answered:
                    return False
            return True

        # Request availability from all participants
        async def request_availability(self, interaction: Interaction, reschedule: bool = False):
            self.avail_buttons = AvailabilityButtons(event=self)
            if not reschedule:
                await interaction.response.send_message(f'**Event name:** {self.name}'
                        f'\n**Duration:** {self.get_duration_minutes()} minutes'
                        f'\n\nSelect **Respond** to enter your availability.'
                        f'\n**Full** will mark you as available from now until midnight tonight.'
                        f'\n**Use Existing** will attempt to grab your availability from another event.'
                        f'\n**Unsubscribe** will allow the event to occur without you; however, you can still respond and participate.'
                        f'\n**Cancel** will cancel scheduling.'
                        f'\n\nThe event will be either created or cancelled within a minute after the last person responds.️', view=self.avail_buttons)
            else:
                await interaction.followup.send(f'**Event name:** {self.name}'
                        f'\n**Duration:** {self.get_duration_minutes()} minutes'
                        f'\n\nSelect **Respond** to enter your new availability.'
                        f'\n**Full** will mark you as available from now until midnight tonight.'
                        f'\n**Use Existing** will attempt to grab your availability from another event.'
                        f'\n**Unsubscribe** will allow the event to occur without you; however, you can still respond and participate.'
                        f'\n**Cancel** will cancel scheduling.', view=self.avail_buttons, ephemeral=True)
                participant = self.get_participant(interaction.user.name)
                participant.answered = False
            self.interaction_message = await interaction.original_response()

        async def update_message(self):
            if self.has_everyone_answered():
                try:
                    await self.responded_message.edit(content=f'Everyone has responded.')
                except Exception as e:
                    logger.exception(f'{self.name}: Error editing responded message with "everyone has responded": {e}')
                return
            try:
                mentions = self.get_names_string(subscribed_only=True, unanswered_only=True, mention=True)
                await self.responded_message.edit(content=f'Waiting for a response from:\n{mentions}')
            except Exception as e:
                logger.exception(f'{self.name}: Error getting mentions string or editing responded message: {e}')

        @classmethod
        async def from_dict(cls, data):
            # Name
            event_name = data["name"]
            if event_name == '':
                raise Exception(f'Event has no name, disregarding event')
            else:
                logger.info(f'{event_name}: loading event')

            # Guild
            event_guild = client.get_guild(data["guild_id"])
            if event_guild:
                logger.info(f'{event_name}: guild found: {event_guild.name}')
            else:
                raise Exception(f'Could not find guild for {event_name}, disregarding event')

            # Text channel
            event_text_channel = event_guild.get_channel(data["text_channel_id"])
            if not event_text_channel:
                logger.info(f'{event_name}: no text channel found')
            else:
                logger.info(f'{event_name}: text channel found: {event_text_channel.name}')

            # Voice channel
            event_voice_channel = utils.get(event_guild.voice_channels, id=data["voice_channel_id"])
            if event_voice_channel:
                logger.info(f'{event_name}: voice channel found: {event_voice_channel.name}')
            else:
                raise Exception(f'Could not find voice channel for {event_name}, disregarding event')

            # Participants
            event_participants = [Participant.from_dict(event_guild, participant) for participant in data["participants"]]
            for participant in event_participants.copy():
                if not participant:
                    event_participants.remove(participant)
            if event_participants:
                logger.info(f'{event_name}: found participant(s): {", ".join([p.member.name for p in event_participants])}')
            else:
                raise Exception(f'{event_name}: no participant(s) found, disregarding event')

            # Interaction (availability) message
            event_avail_buttons = None
            event_interaction_message = None
            try:
                event_interaction_message = await event_text_channel.fetch_message(data["interaction_message_id"])
                logger.info(f'{event_name}: found interaction_message: {event_interaction_message.id}')
            except NotFound:
                logger.info(f'{event_name}: no interaction_message found')
            except HTTPException as e:
                logger.error(f'{event_name}: error getting interaction_message: {e}')

            # Responded message
            event_responded_message = None
            try:
                event_responded_message = await event_text_channel.fetch_message(data["responded_message_id"])
                logger.info(f'{event_name}: found responded_message: {event_responded_message.id}')
            except NotFound:
                logger.info(f'{event_name}: no responded_message found')
            except HTTPException as e:
                logger.error(f'{event_name}: error getting responded_message: {e}')

            # Event buttons message
            event_event_buttons = None
            event_event_buttons_message = None
            try:
                event_event_buttons_message = await event_text_channel.fetch_message(data["event_buttons_message_id"])
                logger.info(f'{event_name}: found event_buttons_message: {event_event_buttons_message.id}')
            except NotFound:
                logger.info(f'{event_name}: no event_buttons_message found')
            except HTTPException as e:
                logger.error(f'{event_name}: error getting event_buttons_message: {e}')

            # Image url
            event_image_url = data["image_url"]
            logger.info(f'{event_name}: image url: {event_image_url}')

            # Event buttons msg content
            event_event_buttons_msg_content_pt1 = data["event_buttons_msg_content_pt1"]
            logger.info(f'{event_name}: event_buttons_msg_content_pt1: ' + r'{event_event_buttons_msg_content_pt1}')
            event_event_buttons_msg_content_pt2 = data["event_buttons_msg_content_pt2"]
            logger.info(f'{event_name}: event_buttons_msg_content_pt2: ' + r'{event_event_buttons_msg_content_pt2}')
            event_event_buttons_msg_content_pt3 = data["event_buttons_msg_content_pt3"]
            logger.info(f'{event_name}: event_buttons_msg_content_pt3: ' + r'{event_event_buttons_msg_content_pt3}')
            event_event_buttons_msg_content_pt4 = data["event_buttons_msg_content_pt4"]
            logger.info(f'{event_name}: event_buttons_msg_content_pt4: ' + r'{event_event_buttons_msg_content_pt4}')

            # Ready to create
            event_ready_to_create = data["ready_to_create"]
            logger.info(f'{event_name}: ready_to_create: {event_ready_to_create}')

            # Created
            event_created = data["created"]
            logger.info(f'{event_name}: created: {event_created}')

            # Started
            event_started = data["started"]
            logger.info(f'{event_name}: started: {event_started}')

            # Scheduled event
            event_scheduled_event = None
            for guild_scheduled_event in event_guild.scheduled_events:
                if guild_scheduled_event.id == data["scheduled_event_id"]:
                    event_scheduled_event = guild_scheduled_event
                    logger.info(f'{event_name}: found guild scheduled event: {event_scheduled_event.id}')
                    break
            if not event_scheduled_event:
                logger.info(f'{event_name}: no guild scheduled event found')

            # Changed
            event_changed = data["changed"]
            logger.info(f'{event_name}: changed: {event_changed}')

            # Start time
            try:
                event_start_time = datetime.fromisoformat(data["start_time"])
                logger.info(f'{event_name}: start time found: {event_start_time.strftime("%A, %m/%d/%Y %H%M")}')
            except Exception as e:
                event_start_time = None
                logger.info(f'{event_name}: no start time found: {e}')

            # Duration
            event_duration = timedelta(minutes=data["duration"])
            if event_duration:
                logger.info(f'{event_name}: duration found: {event_duration.total_seconds()//60}')
            else:
                logger.info(f'{event_name}: no duration found')

            # Unavailable
            event_unavailable = data["unavailable"]
            logger.info(f'{event_name}: unavailable: {event_unavailable}')

            return cls(
                name = event_name,
                guild = event_guild,
                text_channel = event_text_channel,
                interaction_message = event_interaction_message,
                avail_buttons = event_avail_buttons,
                responded_message = event_responded_message,
                voice_channel = event_voice_channel,
                participants = event_participants,
                image_url = event_image_url,
                event_buttons_message = event_event_buttons_message,
                event_buttons = event_event_buttons,
                event_buttons_msg_content_pt1 = event_event_buttons_msg_content_pt1,
                event_buttons_msg_content_pt2 = event_event_buttons_msg_content_pt2,
                event_buttons_msg_content_pt3 = event_event_buttons_msg_content_pt3,
                event_buttons_msg_content_pt4 = event_event_buttons_msg_content_pt4,
                ready_to_create = event_ready_to_create,
                created = event_created,
                started = event_started,
                scheduled_event = event_scheduled_event,
                changed = event_changed,
                start_time = event_start_time,
                duration = event_duration,
                unavailable = event_unavailable
            )

        def to_dict(self):
            try:
                interaction_message_id = self.interaction_message.id
            except:
                interaction_message_id = 0
            try:
                responded_message_id = self.responded_message.id
            except:
                responded_message_id = 0
            try:
                event_buttons_message_id = self.event_buttons_message.id
            except:
                event_buttons_message_id = 0
            try:
                scheduled_event_id = self.scheduled_event.id
            except:
                scheduled_event_id = 0
            try:
                start_time = self.start_time.isoformat()
            except:
                start_time = ''
            return {
                'name': self.name,
                'guild_id': self.guild.id,
                'text_channel_id': self.text_channel.id,
                'interaction_message_id': interaction_message_id,
                'responded_message_id': responded_message_id,
                'voice_channel_id': self.voice_channel.id,
                'participants': [participant.to_dict() for participant in self.participants],
                'image_url': self.image_url,
                'event_buttons_message_id': event_buttons_message_id,
                'event_buttons_msg_content_pt1': self.event_buttons_msg_content_pt1,
                'event_buttons_msg_content_pt2': self.event_buttons_msg_content_pt2,
                'event_buttons_msg_content_pt3': self.event_buttons_msg_content_pt3,
                'event_buttons_msg_content_pt4': self.event_buttons_msg_content_pt4,
                'ready_to_create': self.ready_to_create,
                'created': self.created,
                'started': self.started,
                'scheduled_event_id': scheduled_event_id,
                'changed': self.changed,
                'start_time': start_time,
                'duration': self.get_duration_minutes(),
                'unavailable': self.unavailable
            }

    class AvailabilityModal(Modal):
        def __init__(self, event, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.event = event
            date = datetime.now().astimezone().strftime('%m/%d/%Y')
            self.timeslot1 = TextInput(label='Timeslot 1', placeholder='8-11, 1pm-3pm (i.e. Available 0800-1100, 1300-1500)')
            self.timeslot2 = TextInput(label='Timeslot 2', placeholder='15:30-17 (i.e. Available 1530-1700)', required=False)
            self.timeslot3 = TextInput(label='Timeslot 3', placeholder='-2030, 22- (i.e. Available now-2030, 2200-0000)', required=False)
            self.date = TextInput(label='Date', placeholder='MM/DD/YYYY', default=date)
            self.timezone = TextInput(label='Timezone', placeholder='ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|PT|PST|PDT', default='ET')
            self.add_item(self.timeslot1)
            self.add_item(self.timeslot2)
            self.add_item(self.timeslot3)
            self.add_item(self.date)
            self.add_item(self.timezone)

        async def on_submit(self, interaction: Interaction):
            # Participant availability
            participant = self.event.get_participant(interaction.user.name)
            avail_string = f'{self.timeslot1.value}, {self.timeslot2.value}, {self.timeslot3.value} {self.timezone.value}'
            try:
                participant.set_specific_availability(avail_string, self.date.value)
                participant.answered = True
                response = participant.get_availability_string()
                logger.info(f'{self.event.name}: Received availability from {interaction.user.name}:\n{response}')
                await interaction.response.send_message(response, ephemeral=True)
                self.event.changed = True
                await self.event.update_message()
            except Exception as e:
                try:
                    await interaction.response.send_message(f'Error setting your availability: {e}')
                except:
                    pass
                logger.exception(f'{self.event.name}: Error setting specific availability: {e}')

        async def on_error(self, interaction: Interaction, error: Exception):
            await interaction.response.send_message(f'Error getting availability: {error}', ephemeral=True)
            logger.exception(f'{self.event.name}: Error getting availability from {interaction.user.name} (AvailabilityModal): {error}')

    class AvailabilityButtons(View):
        def __init__(self, event: Event):
            super().__init__(timeout=None)
            self.event = event
            self.respond_label = "Respond"
            self.full_label = "Full"
            self.reuse_label = "Use Existing"
            self.unsub_label = "Unsubscribe"
            self.cancel_label = "Cancel"
            self.respond_button = self.add_respond_button()
            self.full_button = self.add_full_button()
            self.reuse_button = self.add_reuse_button()
            self.unsub_button = self.add_unsub_button()
            self.cancel_button = self.add_cancel_button()

        def add_respond_button(self):
            button = Button(label=self.respond_label, style=ButtonStyle.blurple)
            async def respond_button_callback(interaction: Interaction):
                try:
                    await interaction.response.send_modal(AvailabilityModal(event=self.event, title='Availability'))
                except Exception as e:
                    logger.exception(f'Error sending availability modal: {e}')
                persist.write(client.get_events_dict())
            button.callback=respond_button_callback
            self.add_item(button)
            return button

        def add_full_button(self):
            button = Button(label=self.full_label, style=ButtonStyle.blurple)
            async def full_button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                participant = self.event.get_participant(interaction.user.name)
                if not participant.full_availability_flag:
                    logger.info(f'{self.event.name}: {interaction.user.name} selected full availability')
                    participant.set_full_availability()
                    participant.full_availability_flag = True
                    participant.answered = True
                    response = participant.get_availability_string()
                    await interaction.response.send_message(response, ephemeral=True)
                else:
                    logger.info(f'{self.event.name}: {interaction.user.name} deselected full availability')
                    participant.set_no_availability()
                    participant.full_availability_flag = False
                    if participant.subscribed:
                        participant.answered = False
                    await interaction.response.send_message(f'Your availability has been cleared.', ephemeral=True)
                await self.event.update_message()
                persist.write(client.get_events_dict())
            button.callback = full_button_callback
            self.add_item(button)
            return button

        def add_reuse_button(self):
            button = Button(label=self.reuse_label, style=ButtonStyle.blurple)
            async def reuse_button_callback(interaction: Interaction):
                logger.info(f'{self.event.name}: Reuse button pressed by {interaction.user.name}')
                participant = self.event.get_participant(interaction.user.name)
                found_availability = self.event.get_other_availability(participant)
                if not found_availability:
                    logger.info(f'{self.event.name}: \tNo existing availability found for {interaction.user.name}')
                    await interaction.response.send_message(f'No existing availability found.', ephemeral=True)
                    return
                logger.info(f'{self.event.name}: Found existing availability for {interaction.user.name}')
                participant.availability = found_availability
                participant.answered = True
                response = participant.get_availability_string()
                await interaction.response.send_message(response, ephemeral=True)
                await self.event.update_message()
                persist.write(client.get_events_dict())
            button.callback = reuse_button_callback
            self.add_item(button)
            return button

        def add_unsub_button(self):
            button = Button(label=self.unsub_label, style=ButtonStyle.blurple)
            async def unsub_button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                participant = self.event.get_participant(interaction.user.name)
                if participant.subscribed:
                    logger.info(f'{self.event.name}: {interaction.user.name} unsubscribed')
                    participant.subscribed = False
                    participant.answered = True
                    await interaction.response.send_message(f'You have been unsubscribed from {self.event.name}.', ephemeral=True)
                else:
                    logger.info(f'{self.event.name}: {interaction.user.name} resubscribed')
                    participant.subscribed = True
                    participant.answered = False
                    await interaction.response.send_message(f'You have been resubscribed to {self.event.name}.', ephemeral=True)
                await self.event.update_message()
                persist.write(client.get_events_dict())
            button.callback = unsub_button_callback
            self.add_item(button)
            return button

        def add_cancel_button(self):
            button = Button(label=self.cancel_label, style=ButtonStyle.blurple)
            async def cancel_button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                participant = self.event.get_participant(interaction.user.name)
                if not participant.unavailable:
                    participant.set_no_availability()
                    participant.unavailable = True
                    participant.answered = True
                    self.event.unavailable = True
                    await interaction.response.send_message(f'{self.event.name} will be cancelled shortly unless you click the **Cancel** button again.', ephemeral=True)
                    logger.info(f'{self.event.name}: {interaction.user.name} selected cancel')
                else:
                    participant.unavailable = False
                    if participant.subscribed:
                        participant.answered = False
                    self.event.unavailable = False
                    await interaction.response.send_message(f'{self.event.name} will not be cancelled.', ephemeral=True)
                    logger.info(f'{self.event.name}: {interaction.user.name} deselected cancel')
                await self.event.update_message()
                persist.write(client.get_events_dict())
            button.callback = cancel_button_callback
            self.add_item(button)
            return button

        def disable_all_buttons(self):
            self.respond_button.disabled = True
            self.full_button.disabled = True
            self.reuse_button.disabled = True
            self.unsub_button.disabled = True
            self.cancel_button.disabled = True

    class EventButtons(View):
        def __init__(self, event: Event):
            super().__init__(timeout=None)
            self.start_label = "Start Event"
            self.end_label = "End Event"
            self.reschedule_label = "Reschedule Event"
            self.cancel_label = "Cancel Event"
            self.event = event
            self.start_button = Button(label=self.start_label, style=ButtonStyle.blurple)
            self.end_button = Button(label=self.end_label, style=ButtonStyle.blurple)
            self.reschedule_button = Button(label=self.reschedule_label, style=ButtonStyle.red)
            self.cancel_button = Button(label=self.cancel_label, style=ButtonStyle.red)
            self.add_start_button()
            self.add_end_button()
            self.add_reschedule_button()
            self.add_cancel_button()

        def add_start_button(self):
            async def start_button_callback(interaction: Interaction):
                logger.info(f'{self.event.name}: {interaction.user} started by button press')
                participant_names = [participant.member.name for participant in self.event.participants]
                if interaction.user.name not in participant_names:
                    self.event.participants.append(interaction.user)
                try:
                    await self.event.scheduled_event.start(reason=f'Start button pressed by {interaction.user.name}.')
                    self.event.event_buttons_msg_content_pt2 = f'\n**Started at:** {datetime.now().astimezone().strftime("%H:%M")} ET'
                    await self.event.event_buttons_message.edit(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt4}', view=self.event.event_buttons)
                except Exception as e:
                    logger.exception(f'Error starting event or manipulating event control message: {e}')
                self.event.started = True
                self.start_button.style = ButtonStyle.green
                self.start_button.disabled = True
                self.end_button.disabled = False
                self.reschedule_button.disabled = True
                self.cancel_button.disabled = True
                try:
                    await interaction.response.edit_message(view=self)
                except Exception as e:
                    logger.exception(f'Error responding to START button interaction: {e}')
                persist.write(client.get_events_dict())
            self.start_button.callback = start_button_callback
            self.add_item(self.start_button)

        def add_end_button(self):
            self.end_button.disabled = True
            async def end_button_callback(interaction: Interaction):
                logger.info(f'{self.event.name}: {interaction.user} ended by button press')
                try:
                    await self.event.scheduled_event.delete(reason=f'End button pressed by {interaction.user.name}.')
                    self.event.event_buttons_msg_content_pt3 = f'\n**Ended at:** {datetime.now().astimezone().strftime("%H:%M")} ET'
                    await self.event.event_buttons_message.edit(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt3} {self.event.event_buttons_msg_content_pt4}', view=self.event.event_buttons)
                except Exception as e:
                    logger.exception(f'Error ending event or manipulating event control message: {e}')
                client.events.remove(self.event)
                self.event = None
                self.end_button.style = ButtonStyle.blurple
                self.end_button.disabled = True
                self.reschedule_button.disabled = True
                self.cancel_button.disabled = True
                try:
                    await interaction.response.edit_message(view=self)
                except Exception as e:
                    logger.exception(f'Error responding to END button interaction: {e}')
                persist.write(client.get_events_dict())
            self.end_button.callback = end_button_callback
            self.add_item(self.end_button)

        def add_reschedule_button(self):
            async def reschedule_button_callback(interaction: Interaction):
                interaction.response.defer()
                logger.info(f'{self.event.name}: {interaction.user} rescheduled by button press')
                participant_names = [participant.member.name for participant in self.event.participants]
                if interaction.user.name not in participant_names:
                    self.event.participants.append(interaction.user)
                new_event = Event(self.event.name, self.event.voice_channel, self.event.participants, self.event.guild, interaction.channel, self.event.image_url, self.event.duration)
                try:
                    await self.event.scheduled_event.delete(reason=f'Reschedule button pressed by {interaction.user.name}.')
                    self.event.event_buttons_msg_content_pt2 = f'\n**Rescheduled at:** {datetime.now().astimezone().strftime("%H:%M")} ET'
                    self.start_button.style = ButtonStyle.blurple
                    self.start_button.disabled = True
                    self.end_button.style = ButtonStyle.blurple
                    self.end_button.disabled = True
                    self.reschedule_button.disabled = True
                    self.cancel_button.disabled = True
                    await self.event.event_buttons_message.edit(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt4}', view=self)
                except Exception as e:
                    logger.exception(f'Error cancelling guild event to reschedule: {e}')
                client.events.remove(self.event)
                self.event = new_event
                client.events.append(self.event)
                try:
                    await self.event.request_availability(interaction, reschedule=True)
                    await self.event.update_message()
                except Exception as e:
                    logger.exception(f'Error with RESCHEDULE button requesting availability: {e}')
                persist.write(client.get_events_dict())
            self.reschedule_button.callback = reschedule_button_callback
            self.add_item(self.reschedule_button)

        def add_cancel_button(self):
            async def cancel_button_callback(interaction: Interaction):
                try:
                    self.event.event_buttons_msg_content_pt2 = f'\n**Cancelled by:** {interaction.user.name} at {datetime.now().astimezone().strftime("%H:%M")} ET'
                    await self.event.event_buttons_message.edit(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt4}', view=self.event.event_buttons)
                    await self.event.text_channel.send(f'{self.event.get_names_string(subscribed_only=True, mention=True)}\n{interaction.user.name} cancelled {self.event.name}.')
                    if self.event.created:
                        await self.event.scheduled_event.delete(reason=f'Cancel button pressed by {interaction.user.name}.')
                except Exception as e:
                    logger.exception(f'Error in cancel button callback: {e}')
                logger.info(f'{self.event.name}: {interaction.user} cancelled by button press')
                client.events.remove(self.event)
                self.event = None
                self.cancel_button.style = ButtonStyle.gray
                self.start_button.disabled = True
                self.end_button.disabled = True
                self.reschedule_button.disabled = True
                self.cancel_button.disabled = True
                try:
                    await interaction.response.edit_message(view=self)
                except Exception as e:
                    logger.exception(f'Error sending CANCEL button interaction response or cancelled message to text channel: {e}')
                persist.write(client.get_events_dict())
            self.cancel_button.callback = cancel_button_callback
            self.add_item(self.cancel_button)

    # Put participants into a list
    def get_participants_from_interaction(interaction: Interaction, include_exclude: INCLUDE_EXCLUDE, usernames: str, roles: str):
        participants = []
        # Add the scheduler/creator as a participant
        for member in interaction.channel.members:
            if member.name == interaction.user.name:
                participants.append(Participant(member))
                break

        # Add users meeting role criteria
        if roles:
            try:
                roles = roles.split(',')
                roles = [role.strip() for role in roles]
                roles = [utils.find(lambda r: r.name.lower() == role.lower(), interaction.guild.roles) for role in roles]
            except Exception as e:
                raise Exception(f'Failed to parse role(s): {e}')
            for member in interaction.channel.members:
                if member.bot or member.name == interaction.user.name:
                    continue
                found_role = False
                for role in roles:
                    if role in member.roles:
                        found_role = True
                        break
                if include_exclude == INCLUDE and found_role:
                    participants.append(Participant(member))
                elif include_exclude == EXCLUDE and not found_role:
                    participants.append(Participant(member))
            return participants

        # Add users meeting username criteria
        if usernames:
            try:
                usernames = usernames.split(',')
                usernames = [username.strip() for username in usernames]
            except:
                raise Exception(f'Failed to parse username(s): {e}')
            for member in interaction.channel.members:
                if member.bot or member.name == interaction.user.name:
                    continue
                if include_exclude == INCLUDE and (member.name in usernames or member.id in usernames):
                    participants.append(Participant(member))
                elif include_exclude == EXCLUDE and member.name not in usernames and member.id not in usernames:
                    participants.append(Participant(member))
            return participants

        # Add all users in the channel
        for member in interaction.channel.members:
            if member.bot or member.name == interaction.user.name:
                continue
            participants.append(Participant(member))
        return participants


    @client.event
    async def on_ready():
        logger.info(f'{client.user} has connected to Discord!')
        await client.sync_commands()
        await client.retrieve_events()
        if not update.is_running():
            update.start()
        logger.info(f'{client.user} is ready!')

    @client.event
    async def on_message(message):
        if message.author.bot or message.guild:
            return
        # Event image
        if message.attachments and message.content:
            msg_content = message.content.lower()
            for event in client.events:
                if event.name.lower() in msg_content:
                    if event.created:
                        try:
                            image_bytes = await message.attachments[0].read()
                            await event.scheduled_event.edit(image=image_bytes)
                            await message.channel.send(f'Added your image to {event.name}.')
                            logger.info(f'{event.name}: {message.author.name} added an image')
                        except Exception as e:
                            await message.channel.send(f'Failed to add your image to {event.name}.\nError: {e}', ephemeral=True)
                            logger.warn(f'{event.name}: Error adding image from {message.author.name}: {e}')
                    else:
                        event.image_url = message.attachments[0].url
                        await message.channel.send(f'Attached image url to event object. Will try setting it when the event is made.')
                    return
            await message.channel.send(f'Could not find event {msg_content}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}', ephemeral=True)
            return

    @client.tree.command(name='create', description='Create an event.')
    @app_commands.describe(event_name='Name for the event.')
    @app_commands.describe(voice_channel='Voice channel for the event.')
    @app_commands.describe(start_time='Start time (in Eastern Time) for the event.')
    @app_commands.describe(image_url='URL to an image for the event.')
    @app_commands.describe(include_exclude='Whether to include or exclude users with the designated role.')
    @app_commands.describe(usernames='Comma separated usernames of users to include/exclude.')
    @app_commands.describe(roles='Comma separated roles of users to include/exclude.')
    @app_commands.describe(duration='Event duration in minutes (30 minutes default).')
    async def create_command(interaction: Interaction, event_name: str, voice_channel: VoiceChannel, start_time: str, image_url: str = None, include_exclude: INCLUDE_EXCLUDE = INCLUDE, usernames: str = None, roles: str = None, duration: int = 30):
        logger.info(f'{event_name}: Received event creation request from {interaction.user.name}')
        if not interaction.guild.voice_channels:
            raise Exception(f'The server must have at least one voice channel to schedule an event.')

        if event_name in [event.name for event in client.events]:
            await interaction.response.send_message(f'Sorry, I already have an event called {event_name}. Please choose a different name.', ephemeral=True)
            return

        # Parse start time
        start_time = start_time.strip()
        start_time = start_time.replace(':', '')
        if len(start_time) == 3:
            start_time = '0' + start_time
        elif len(start_time) != 4:
            await interaction.response.send_message(f'Invalid start time format. Examples: "1630" or "00:30"')
        hour = int(start_time[:2])
        minute = int(start_time[2:])
        start_time_obj = datetime.now().astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0)
        if start_time_obj <= datetime.now().astimezone().replace(second=0, microsecond=0):
            start_time_obj += timedelta(days=1)

        participants = get_participants_from_interaction(interaction, include_exclude, usernames, roles)

        # Make event
        duration = timedelta(minutes=duration)
        event = Event(event_name, voice_channel, participants, interaction.guild, interaction.channel, image_url, duration, start_time_obj)
        client.events.append(event)
        await event.make_scheduled_event()
        response =  f'{event.get_names_string(mention=True)}'
        response += f'\n**Event name:** {event.name}'
        response += f'\n**Duration:** {event.get_duration_minutes()} minutes'
        response += f'\n**Starts at:** {event.get_start_time_string()}'
        try:
            event.event_buttons = EventButtons(event)
            event.event_buttons_message = await interaction.response.send_message(response, view=event.event_buttons)
        except Exception as e:
            logger.error(f'Error sending interaction response to create event command): {e}')
            logger.exception(e)
        persist.write(client.get_events_dict())

    @client.tree.command(name='schedule', description='Create a scheduling event.')
    @app_commands.describe(event_name='Name for the event.')
    @app_commands.describe(voice_channel='Voice channel for the event.')
    @app_commands.describe(image_url="URL to an image for the event.")
    @app_commands.describe(include_exclude='Whether to include or exclude users specified.')
    @app_commands.describe(usernames='Comma separated usernames of users to include/exclude.')
    @app_commands.describe(roles='Comma separated roles of users to include/exclude.')
    @app_commands.describe(duration="Event duration in minutes (30 minutes default).")
    async def schedule_command(interaction: Interaction, event_name: str, voice_channel: VoiceChannel, image_url: str = None, include_exclude: INCLUDE_EXCLUDE = INCLUDE, usernames: str = None, roles: str = None, duration: int = 30):
        logger.info(f'{event_name}: Received event schedule request from {interaction.user.name}')
        if not interaction.guild.voice_channels:
            raise Exception(f'The server must have at least one voice channel to schedule an event.')

        if event_name in [event.name for event in client.events]:
            await interaction.response.send_message(f'Sorry, I already have an event called {event_name}. Please choose a different name.', ephemeral=True)
            return

        # Generate participants list
        try:
            participants = get_participants_from_interaction(interaction, include_exclude, usernames, roles)
        except Exception as e:
            await interaction.response.send_message(f'Failed to generate participants list: {e}')
            logger.exception(f'Error getting participants: {e}')
            return

        # Make event object
        try:
            duration = timedelta(minutes=duration)
            event = Event(event_name, voice_channel, participants, interaction.guild, interaction.channel, image_url, duration)
            mentions = '\nWaiting for a response from:\n' + event.get_names_string(subscribed_only=True, mention=True)
            client.events.append(event)
        except Exception as e:
            await interaction.response.send_message(f'Failed to make event object: {e}')
            logger.exception(f'Error making event object: {e}')
            return

        # Request availability and make participant response tracker message
        try:
            await event.request_availability(interaction)
            event.responded_message = await interaction.channel.send(f'{mentions}')
        except Exception as e:
            logger.exception(f'Error sending responded message or requesting availability: {e}')
        persist.write(client.get_events_dict())

    @client.tree.command(name='bind', description='Bind a text channel to an existing event.')
    @app_commands.describe(event_name='Name of the vent to set this text channel for.')
    async def bind_command(interaction: Interaction, event_name: str):
        logger.info(f'{interaction.user.name} used bind command')
        event_name = event_name.lower()
        for event in client.events:
            if event_name == event.name.lower():
                if event.text_channel:
                    await interaction.response.send_message(f'Event {event.name} already has a text channel: <#{event.text_channel.id}>', ephemeral=True)
                    return
                # Bind the text channel to the interaction channel
                try:
                    event.text_channel = interaction.channel
                except Exception as e:
                    logger.exception(f'Error binding channel: {e}')
                    try:
                        await interaction.response.send_message(f'Failed to bind channel: {e}', ephemeral=True)
                    except:
                        logger.exception(f'Failed to respond to bind command: {e}')
                    return
                # Respond to interaction
                try:
                    await interaction.response.send_message(f'Bound this text channel to {event.name}.', ephemeral=True)
                    await interaction.channel.send(f'{event.name} is scheduled to start at {event.get_start_time_string()}.\n', view=EventButtons(event))
                except Exception as e:
                    logger.exception(f'Error responding to bind command: {e}')
                return
        try:
            await interaction.response.send_message(f'Could not find event {event_name}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}', ephemeral=True)
        except Exception as e:
            logger.exception(f'Error responding to bind command: {e}')

    @tasks.loop(seconds=30)
    async def update():
        for event in client.events.copy():
            # Remove events if a participant is unavailable
            if event.unavailable:
                try:
                    event.avail_buttons.disable_all_buttons()
                    unavailable_names = ''
                    unavailable_counter = 0
                    for participant in event.participants:
                        if participant.unavailable:
                            unavailable_names += f'{participant.member.name} '
                            unavailable_counter += 1
                    notification_message = f'{event.get_names_string(subscribed_only=True, mention=True)}\nScheduling for **{event.name}** has been cancelled.\n'
                    notification_message += unavailable_names
                    notification_message += 'cancelled the event.'
                    if event.text_channel:
                        await event.text_channel.send(notification_message)
                    else:
                        for participant in event.participants:
                            async with participant.msg_lock:
                                participant.member.send(notification_message)
                    logger.info(f'{event.name}: Participants lacked common availability, removed event from memory')
                    client.events.remove(event)
                    del event
                except Exception as e:
                    logger.exception(f'Error invalidating and deleting event: {e}')
                continue

            # Countdown to start + 5 minute warning
            if event.created and not event.started:
                try:
                    # Countdown (adjust every other update since it is on 30 second intervals)
                    event.countdown_check_flag = not event.countdown_check_flag
                    if event.countdown_check_flag:
                        time_until_start: timedelta = event.start_time - datetime.now().astimezone()
                        event.mins_until_start = int(round(time_until_start.seconds/60))
                        if event.mins_until_start < 0:
                            event.event_buttons_msg_content_pt2 = f'\n**Overdue by:**'
                            mins_until_start_string = f'{event.mins_until_start}'
                            mins_until_start_string = mins_until_start_string.replace('-', '')
                            response = f'{event.event_buttons_msg_content_pt1} {event.event_buttons_msg_content_pt2} {mins_until_start_string} {event.event_buttons_msg_content_pt4}'
                        elif event.mins_until_start == 0:
                            event.event_buttons_msg_content_pt2 = f'\n**Starting now**'
                            response = f'{event.event_buttons_msg_content_pt1} {event.event_buttons_msg_content_pt2} {event.event_buttons_msg_content_pt4}'
                        else:
                            event.event_buttons_msg_content_pt2 = f'\n**Starts in:**'
                            hrs_mins_until_start_string = mins_to_hrs_mins_string(event.mins_until_start)
                            response = f'{event.event_buttons_msg_content_pt1} {event.event_buttons_msg_content_pt2} {hrs_mins_until_start_string} {event.event_buttons_msg_content_pt4}'
                        await event.event_buttons_message.edit(content=response, view=event.event_buttons)
                except Exception as e:
                    logger.exception(f'{event.name}: Error counting down: {e}')

                try:
                    # Send 5 minute warning
                    if datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(minutes=5) == event.start_time and event.scheduled_event.status == EventStatus.scheduled and not event.started:
                        if not event.five_minute_warning_flag:
                            event.five_minute_warning_flag = True
                            if event.text_channel:
                                try:
                                    await event.text_channel.send(f'{event.get_names_string(subscribed_only=True, mention=True)}\n**5 minute warning!** {event.name} is scheduled to start in 5 minutes.')
                                except Exception as e:
                                    logger.exception(f'Error sending 5 minute nudge: {e}')
                            else:
                                for participant in event.participants:
                                    async with participant.msg_lock:
                                        await participant.member.send(f'**5 minute warning!** {event.name} is scheduled to start in 5 minutes.')
                except Exception as e:
                    logger.exception(f'{event.name}: Error sending 5 minute warning: {e}')
                continue

            # Reset to ensure at least a minute to finish answering
            if event.changed or not event.has_everyone_answered():
                event.changed = False
                continue

            # Disable availability message buttons
            event.avail_buttons.disable_all_buttons()

            if not event.created:
                # Compare availabilities
                try:
                    await event.compare_availabilities()
                except Exception as e:
                    logger.exception(f'Error comparing availabilities: {e}')

                # Cancel the event if no common availability was found
                if not event.ready_to_create:
                    try:
                        logger.info(f'{event.name}: No common availability found')
                        if event.text_channel:
                            await event.text_channel.send(f'No common availability was found. Scheduling for {event.name} has been cancelled.')
                        else:
                            for participant in event.participants:
                                async with participant.msg_lock:
                                    await participant.member.send(f'No common availability was found. Scheduling for {event.name} has been cancelled.')
                        client.events.remove(event)
                        del event
                    except Exception as e:
                        logger.exception(f'{event.name}: Error messaging participants: {e}')
                    continue
                # Create the event if it is ready to create
                else:
                    try:
                        await event.make_scheduled_event()
                    except Exception as e:
                        logger.exception(f'{event.name}: Error creating scheduled event: {e}')

                    # List subscribed people and list unsubscribed people
                    unsubbed = event.get_names_string(unsubscribed_only=True)
                    if unsubbed:
                        unsubbed = f'\nUnsubscribed: {unsubbed}'

                    # Calculate time until start
                    try:
                        time_until_start: timedelta = event.start_time - datetime.now().astimezone()
                        event.mins_until_start = int(round(time_until_start.seconds/60))
                        event.event_buttons_msg_content_pt1 = f'{event.get_names_string(subscribed_only=True, mention=True)}'
                        event.event_buttons_msg_content_pt1 += f'\n**Event name:** {event.name}'
                        event.event_buttons_msg_content_pt1 += f'\n**Scheduled:** {event.start_time.strftime("%m/%d")} at {event.start_time.strftime("%H:%M")} ET'
                        event.event_buttons_msg_content_pt2 = f'\n**Starts in:**'
                        event.event_buttons_msg_content_pt4 += f'\n{unsubbed}'
                        response = f'{event.event_buttons_msg_content_pt1} {event.event_buttons_msg_content_pt2} {mins_to_hrs_mins_string(event.mins_until_start)} {event.event_buttons_msg_content_pt4}'
                        await event.responded_message.delete()
                        event.event_buttons = EventButtons(event)
                        event.event_buttons_message = await event.text_channel.send(content=response, view=event.event_buttons)
                    except Exception as e:
                        logger.exception(f'{event.name}: Error sending event created notification with buttons: {e}')
        persist.write(client.get_events_dict())

    client.run(discord_token)


if __name__ == '__main__':
    main()