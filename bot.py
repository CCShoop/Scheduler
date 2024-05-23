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
from discord import app_commands, Interaction, Intents, Client, ButtonStyle, EventStatus, EntityType, TextChannel, VoiceChannel, Message, SelectOption, ScheduledEvent, Guild, PrivacyLevel, User, utils, File, NotFound, HTTPException
from discord.ui import View, Button, Modal, TextInput, Select
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
file_handler.setLevel(logging.DEBUG)
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
def mins_to_hrs_mins_string(minutes: int) -> str:
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
def double_digit_string(digit_string: str) -> str:
    if int(digit_string) < 10 and len(digit_string) == 1:
        digit_string = '0' + digit_string
    return digit_string


def main():
    class SchedulerClient(Client):
        def __init__(self, intents) -> None:
            super(SchedulerClient, self).__init__(intents=intents)
            self.tree = app_commands.CommandTree(self)
            self.loaded_json = False
            self.events = []

        # Load all events from the data file
        async def retrieve_events(self) -> None:
            if not self.loaded_json:
                self.loaded_json = True
                events_data = persist.read()
                if events_data:
                    for event_data in events_data['events']:
                        try:
                            event = await Event.from_dict(event_data)
                            if not event:
                                raise Exception(f'Failed to create event object')
                            if event.availability_message:
                                event.avail_buttons = AvailabilityButtons(event)
                                await event.availability_message.edit(view=event.avail_buttons)
                            if event.event_buttons_message:
                                event.event_buttons = EventButtons(event)
                                await event.event_buttons_message.edit(view=event.event_buttons)
                            self.events.append(event)
                            logger.info(f'{event.name}: event loaded and added to client event list')
                        except Exception as e:
                            logger.error(f'Could not add event to client event list: {e}')
                else:
                    logger.info(f'No json data found')

        # Return all events as a dict
        def get_events_dict(self) -> dict:
            events_data = {}
            events_data['events'] = [event.to_dict() for event in self.events]
            return events_data

    OWNER_ID = int(os.getenv('OWNER_ID'))
    DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
    client = SchedulerClient(intents=Intents.all())

    class Event:
        def __init__(self, 
            name: str,
            voice_channel: VoiceChannel,
            guild: Guild,
            text_channel: TextChannel,
            image_url: str = '',
            participants: list = None,
            duration = timedelta(minutes=30),
            start_time: datetime = None,
            availability_message= None,
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
        ) -> None:
            self.name = name
            self.guild = guild
            self.entity_type = EntityType.voice
            self.text_channel = text_channel
            self.availability_message = availability_message 
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
            self.unavailable = unavailable

        # Return all timeblocks that intersect each other
        def intersect_time_blocks(self, timeblocks1: list, timeblocks2: list) -> list:
            intersected_time_blocks = []
            for block1 in timeblocks1:
                for block2 in timeblocks2:
                    start_time = max(block1.start_time, block2.start_time)
                    end_time = min(block1.end_time, block2.end_time)
                    if start_time < end_time:
                        logger.debug(f'{self.name}: \tFound intersected timeblocks:')
                        logger.debug(f'{self.name}: \t\t{block1}')
                        logger.debug(f'{self.name}: \t\t{block2}')
                        intersected_time_blocks.append(TimeBlock(start_time, end_time))
            return intersected_time_blocks

        # Compare availabilities of all subscribed participants
        def compare_availabilities(self) -> None:
            logger.debug(f'{self.name}: Comparing availabilities')
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
                self.five_minute_warning_flag = True
                return

            # Otherwise, find the earliest common availability
            available_timeblocks = []
            for participant in subbed_participants:
                available_timeblocks.append(participant.availability)
            intersected_timeblocks = available_timeblocks[0]
            for timeblocks in available_timeblocks[1:]:
                intersected_timeblocks = self.intersect_time_blocks(intersected_timeblocks, timeblocks)
                logger.debug(f'{self.name}: \tIntersected timeblocks:')
                for timeblock in intersected_timeblocks:
                    logger.debug(f'{self.name}: \t\t{timeblock}')

            for timeblock in intersected_timeblocks:
                logger.debug(f'{self.name}: \tChecking timeblock: "{timeblock}"')
                if timeblock.duration >= self.duration:
                    logger.debug(f'{self.name}: \t\tDuration is sufficient, using for event start time')
                    self.start_time = timeblock.start_time
                    self.ready_to_create = True
                    return
            logger.debug(f'{self.name}: compare_availabilities: No common availability found between all participants, cancelling event')

        # Check before everyone has responded to see if two people have answered and are not available
        def check_availabilities(self) -> None:
            # Make sure it's been at least one update cycle since the last response
            if event.changed:
                return
            lacks_availability = True
            subbed_participants = []
            for participant in self.participants:
                if participant.subscribed and participant.answered:
                    subbed_participants.append(participant)

            current_time = datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(minutes=5)

            # Find common availability
            available_timeblocks = []
            for participant in subbed_participants:
                available_timeblocks.append(participant.availability)
            intersected_timeblocks = available_timeblocks[0]
            for timeblocks in available_timeblocks[1:]:
                intersected_timeblocks = self.intersect_time_blocks(intersected_timeblocks, timeblocks)

            for timeblock in intersected_timeblocks:
                if timeblock.duration >= self.duration:
                    return
            logger.debug(f'{self.name}: check_availabilities: No common availability found between currently responded participants, cancelling event')
            self.unavailable = True

        # Return the number of participants who have responded
        def number_of_responded(self) -> int:
            responded = 0
            for participant in self.participants:
                if participant.subscribed and participant.answered:
                    responded += 1
            return responded

        # Make the guild scheduled event object, set an image if there is a url
        async def make_scheduled_event(self) -> None:
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
        def get_names_string(self, subscribed_only: bool = False, unsubscribed_only: bool = False, unanswered_only: bool = False, mention: bool = False) -> str:
            names = []
            mentions = ''

            if subscribed_only and unsubscribed_only:
                subscribed_only = False
                unsubscribed_only = False

            for participant in self.participants:
                if mention:
                    name_string = f'{participant.member.mention} '
                elif participant.member.nick:
                    name_string = f'{participant.member.nick}'
                else:
                    name_string = f'{participant.member.name}'

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
        def get_participant(self, username: str) -> Participant:
            for participant in self.participants:
                if participant.member.name == username:
                    return participant
            return None

        # Shares participants with another event
        def shares_participants(self, event) -> list:
            other_participants = []
            for self_participant in self.participants:
                for other_participant in event.participants:
                    if self_participant.member.id == other_participant.member.id:
                        other_participants.append(other_participant)
                        logger.info(f'Found shared participant {self_participant.member.name} in {self.name} and {event.name}')
            return other_participants

        # Get availability for participant from another event
        def get_other_availability(self, participant: Participant) -> list:
            availabilities = []
            for other_event in client.events:
                if other_event != self:
                    for other_participant in other_event.participants:
                        if other_participant.member.id == participant.member.id:
                            availabilities.append(other_participant.availability)
            return availabilities

        # Set event_buttons_msg_content parts and get response message
        def get_event_buttons_message_string(self) -> str:
            # List subscribed people and list unsubscribed people
            unsubbed = self.get_names_string(unsubscribed_only=True)
            if unsubbed:
                unsubbed = f'**Unsubscribed:** {unsubbed}'
            response = ''
            time_until_start: timedelta = self.start_time - datetime.now().astimezone()
            self.mins_until_start = int(time_until_start.total_seconds()//60) + 1
            self.event_buttons_msg_content_pt1 = f'{self.get_names_string(subscribed_only=True, mention=True)}'
            self.event_buttons_msg_content_pt1 += f'\n**Event name:** {self.name}'
            self.event_buttons_msg_content_pt1 += f'\n**Duration:** {self.get_duration_minutes()} minutes'
            self.event_buttons_msg_content_pt1 += f'\n**Scheduled:** {self.get_start_time_string()}'
            self.event_buttons_msg_content_pt2 = f'\n**Starts in:** {mins_to_hrs_mins_string(self.mins_until_start)}'
            self.event_buttons_msg_content_pt4 = f'\n{unsubbed}'
            response = f'{self.event_buttons_msg_content_pt1} {self.event_buttons_msg_content_pt2} {self.event_buttons_msg_content_pt4}'
            return response

        # Get duration value
        def get_duration_minutes(self) -> int:
            return int(self.duration.total_seconds()//60)

        # Get start time string
        def get_start_time_string(self) -> str:
            return f'{self.start_time.strftime("%m/%d at %H:%M")} ET'

        # All participants have responded
        def has_everyone_answered(self) -> bool:
            for participant in self.participants:
                if participant.subscribed and not participant.answered:
                    return False
            return True

        # Request availability from all participants
        async def request_availability(self, interaction: Interaction, reschedule: bool = False) -> None:
            self.avail_buttons = AvailabilityButtons(event=self)
            if not reschedule:
                await interaction.response.send_message(f'Event scheduling started for {self.name}.', ephemeral=True)
                response =  f'**Event name:** {self.name}'
                response += f'\n**Duration:** {self.get_duration_minutes()} minutes'
                response += f'\n\nSelect **Respond** to enter your availability.'
                response += f'\n**Full** will mark you as available from now until midnight tonight.'
                response += f'\n**Use Existing** will attempt to grab your availability from another event.'
                response += f'\n**Unsubscribe** will allow the event to occur without you; however, you can still respond and participate.'
                response += f'\n**Cancel** will cancel scheduling.'
                response += f'\n\nThe event will be either created or cancelled within a minute after the last person responds.️'
                self.availability_message = await self.text_channel.send(content=response, view=self.avail_buttons)
            else:
                await interaction.followup.send(f'Event rescheduling started for {self.name}.', ephemeral=True)
                response =  f'**Event name:** {self.name}'
                response += f'\n**Duration:** {self.get_duration_minutes()} minutes'
                response += f'\n\nSelect **Respond** to enter your new availability.'
                response += f'\n**Full** will mark you as available from now until midnight tonight.'
                response += f'\n**Use Existing** will attempt to grab your availability from another event.'
                response += f'\n**Unsubscribe** will allow the event to occur without you; however, you can still respond and participate.'
                response += f'\n**Cancel** will cancel scheduling.'
                participant = self.get_participant(interaction.user.name)
                participant.set_no_availability()
                participant.answered = False
                self.availability_message = await self.text_channel.send(content=response, view=self.avail_buttons)
            await self.update_responded_message()

        # Create or edit the responded message to show who still needs to respond to the availability request
        async def update_responded_message(self) -> None:
            if self.created:
                return
            if not self.responded_message:
                try:
                    mentions = self.get_names_string(unanswered_only=True, mention=True)
                    self.responded_message = await self.text_channel.send(content=f'Waiting for a response from:\n{mentions}')
                except Exception as e:
                    logger.exception(f'{self.name}: Error sending responded message: {e}')
                return
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
            event_availability_message = None
            try:
                event_availability_message = await event_text_channel.fetch_message(data["availability_message_id"])
                logger.info(f'{event_name}: found availability_message: {event_availability_message.id}')
            except NotFound:
                logger.info(f'{event_name}: no availability_message found')
            except HTTPException as e:
                logger.error(f'{event_name}: error getting availability_message: {e}')

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
                availability_message = event_availability_message,
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

        def to_dict(self) -> dict:
            try:
                availability_message_id = self.availability_message.id
            except:
                availability_message_id = 0
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
                'availability_message_id': availability_message_id,
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
        def __init__(self, event, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.event = event
            date = datetime.now().astimezone().strftime('%m/%d/%Y')
            self.timeslot1 = TextInput(label='Timeslot 1', placeholder='8-11, 1pm-3pm (i.e. Available 0800-1100, 1300-1500)', default='')
            self.timeslot2 = TextInput(label='Timeslot 2', placeholder='15:30-17 (i.e. Available 1530-1700)', default='', required=False)
            self.timeslot3 = TextInput(label='Timeslot 3', placeholder='-2030, 22- (i.e. Available now-2030, 2200-0000)', default='', required=False)
            self.date = TextInput(label='Date', placeholder='MM/DD/YYYY', default=date)
            self.timezone = TextInput(label='Timezone', placeholder='ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|PT|PST|PDT', default='ET')
            self.add_item(self.timeslot1)
            self.add_item(self.timeslot2)
            self.add_item(self.timeslot3)
            self.add_item(self.date)
            self.add_item(self.timezone)

        async def on_submit(self, interaction: Interaction) -> None:
            # Participant availability
            participant = self.event.get_participant(interaction.user.name)
            avail_string = f'{self.timeslot1.value}, {self.timeslot2.value}, {self.timeslot3.value} {self.timezone.value}'
            try:
                logger.info(f'{self.event.name}: Received availability from {participant.member.name}')
                participant.set_specific_availability(avail_string, self.date.value)
                participant.answered = True
                response = f'**__Availability received for {self.event.name}!__**\n' + participant.get_availability_string()
                await interaction.response.send_message(response, ephemeral=True)
                self.event.changed = True
                await self.event.update_responded_message()
                for timeblock in participant.availability:
                    logger.info(f'{timeblock}')
            except Exception as e:
                try:
                    await interaction.response.send_message(f'Error setting your availability: {e}')
                except:
                    pass
                logger.exception(f'{self.event.name}: Error setting specific availability: {e}')

        async def on_error(self, interaction: Interaction, error: Exception) -> None:
            await interaction.response.send_message(f'Error getting availability: {error}', ephemeral=True)
            logger.exception(f'{self.event.name}: Error getting availability from {interaction.user.name} (AvailabilityModal): {error}')

    class AvailabilityButtons(View):
        def __init__(self, event: Event) -> None:
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

        # Submit complex availability
        def add_respond_button(self) -> Button:
            button = Button(label=self.respond_label, style=ButtonStyle.blurple)
            async def respond_button_callback(interaction: Interaction):
                try:
                    await interaction.response.send_modal(AvailabilityModal(event=self.event, title=f'Availability for {self.event.name}'))
                except Exception as e:
                    logger.exception(f'Error sending availability modal: {e}')
                persist.write(client.get_events_dict())
            button.callback=respond_button_callback
            self.add_item(button)
            return button

        # Set yourself as available for the rest of the day
        def add_full_button(self) -> Button:
            button = Button(label=self.full_label, style=ButtonStyle.blurple)
            async def full_button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                participant = self.event.get_participant(interaction.user.name)
                if not participant.full_availability_flag:
                    logger.info(f'{self.event.name}: {participant.member.name} selected full availability button')
                    participant.set_full_availability()
                    for timeblock in participant.availability:
                        logger.info(f'{self.event.name}: \t{timeblock}')
                    participant.full_availability_flag = True
                    participant.answered = True
                    response = participant.get_availability_string()
                    await interaction.response.send_message(response, ephemeral=True)
                else:
                    logger.info(f'{self.event.name}: {participant.member.name} deselected full availability')
                    participant.set_no_availability()
                    for timeblock in participant.availability:
                        logger.info(f'{self.event.name}: \t{timeblock}')
                    participant.full_availability_flag = False
                    if participant.subscribed:
                        participant.answered = False
                    await interaction.response.send_message(f'Your availability has been cleared.', ephemeral=True)
                await self.event.update_responded_message()
                persist.write(client.get_events_dict())
            button.callback = full_button_callback
            self.add_item(button)
            return button

        # Reuse availability from another event
        def add_reuse_button(self) -> Button:
            button = Button(label=self.reuse_label, style=ButtonStyle.blurple)
            async def reuse_button_callback(interaction: Interaction):
                logger.info(f'{self.event.name}: Reuse button pressed by {interaction.user.name}')
                participant = self.event.get_participant(interaction.user.name)
                found_availability = self.event.get_other_availability(participant)
                if not found_availability:
                    logger.info(f'{self.event.name}: \tNo existing availability found for {interaction.user.name}')
                    await interaction.response.send_message(f'No existing availability found.', ephemeral=True)
                    return
                logger.info(f'{self.event.name}: \tFound existing availability for {interaction.user.name}')
                # TODO pass into select here
                participant.availability = found_availability[0]
                for timeblock in participant.availability:
                    logger.debug(f'{timeblock}')
                participant.answered = True
                response = participant.get_availability_string()
                await interaction.response.send_message(response, ephemeral=True)
                await self.event.update_responded_message()
                persist.write(client.get_events_dict())
            button.callback = reuse_button_callback
            self.add_item(button)
            return button

        # Unsubscribe from the event
        def add_unsub_button(self) -> Button:
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
                await self.event.update_responded_message()
                persist.write(client.get_events_dict())
            button.callback = unsub_button_callback
            self.add_item(button)
            return button

        # Cancel event scheduling
        def add_cancel_button(self) -> Button:
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
                await self.event.update_responded_message()
                persist.write(client.get_events_dict())
            button.callback = cancel_button_callback
            self.add_item(button)
            return button

        # Disable all buttons in the view (usually done when an event is created or cancelled)
        async def disable_buttons(self):
            self.respond_button.disabled = True
            self.full_button.disabled = True
            self.reuse_button.disabled = True
            self.unsub_button.disabled = True
            self.cancel_button.disabled = True
            await self.event.availability_message.edit(view=self.event.avail_buttons)

    class EventButtons(View):
        def __init__(self, event: Event) -> None:
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

        # Start the event
        def add_start_button(self) -> None:
            async def start_button_callback(interaction: Interaction):
                logger.info(f'{self.event.name}: {interaction.user} started by button press')
                try:
                    await self.event.scheduled_event.start(reason=f'Start button pressed by {interaction.user.name}.')
                except Exception as e:
                    await interaction.response.edit_message(view=self)
                    return
                participant_names = [participant.member.name for participant in self.event.participants]
                if interaction.user.name not in participant_names:
                    self.event.participants.append(interaction.user)
                try:
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
                # Interaction response
                try:
                    await interaction.response.edit_message(view=self)
                except Exception as e:
                    logger.exception(f'{self.event.name}: Error responding to START button interaction: {e}')
                # Disable start buttons of events scheduled for the same channel
                for event in client.events:
                    if event == self.event or not event.created or event.voice_channel != self.event.voice_channel:
                        continue
                    event.event_buttons.start_button.disabled = True
                    try:
                        await event.event_buttons_message.edit(view=event.event_buttons)
                        logger.info(f'{self.event.name}: Disabled start button for event with same location: {event.name}')
                    except Exception as e:
                        logger.error(f'{self.event.name}: Failed to disable start button for {event.name}: {e}')
                persist.write(client.get_events_dict())
            self.start_button.callback = start_button_callback
            self.add_item(self.start_button)

        # End the event
        def add_end_button(self) -> None:
            self.end_button.disabled = True
            async def end_button_callback(interaction: Interaction):
                logger.info(f'{self.event.name}: {interaction.user} ended by button press')
                try:
                    await self.event.scheduled_event.delete(reason=f'End button pressed by {interaction.user.name}.')
                    self.event.event_buttons_msg_content_pt3 = f'\n**Ended at:** {datetime.now().astimezone().strftime("%H:%M")} ET'
                    await self.event.event_buttons_message.edit(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt3} {self.event.event_buttons_msg_content_pt4}', view=self.event.event_buttons)
                except Exception as e:
                    logger.error(f'Error ending event or manipulating event control message: {e}')
                logger.info(f'{self.event.name}: removed from memory')
                client.events.remove(self.event)
                self.end_button.style = ButtonStyle.blurple
                self.end_button.disabled = True
                self.reschedule_button.disabled = True
                self.cancel_button.disabled = True
                # Interaction response, remove buttons
                try:
                    await interaction.response.edit_message(view=None)
                except Exception as e:
                    logger.exception(f'Error responding to END button interaction: {e}')
                # Re-enable start buttons of appropriate events
                for event in client.events:
                    if event == self.event or not event.created or event.voice_channel != self.event.voice_channel:
                        continue
                    event.event_buttons.start_button.disabled = False
                    try:
                        logger.info(f'{self.event.name}: Re-enabled start button for event with same location: {event.name}')
                        await event.event_buttons_message.edit(view=event.event_buttons)
                    except Exception as e:
                        logger.error(f'{self.event.name}: Failed to re-enable start button for {event.name}: {e}')
                self.event = None
                persist.write(client.get_events_dict())
            self.end_button.callback = end_button_callback
            self.add_item(self.end_button)

        # Reschedule the event
        def add_reschedule_button(self) -> None:
            async def reschedule_button_callback(interaction: Interaction):
                logger.info(f'{self.event.name}: {interaction.user} rescheduled by button press')
                await interaction.response.defer(ephemeral=True)
                if interaction.user.id not in [participant.member.id for participant in self.event.participants]:
                    member = self.guild.get_member(interaction.user.id)
                    participant = Participant(member=member)
                    self.event.participants.append(participant)
                new_event = Event(name=self.event.name, voice_channel=self.event.voice_channel, participants=self.event.participants, guild=self.event.guild, text_channel=interaction.channel, image_url=self.event.image_url, duration=self.event.duration)
                try:
                    await self.event.scheduled_event.delete(reason=f'Reschedule button pressed by {interaction.user.name}.')
                    self.event.event_buttons_msg_content_pt2 = f'\n**Rescheduled at:** {datetime.now().astimezone().strftime("%H:%M")} ET'
                    self.start_button.style = ButtonStyle.blurple
                    self.start_button.disabled = True
                    self.end_button.style = ButtonStyle.blurple
                    self.end_button.disabled = True
                    self.reschedule_button.disabled = True
                    self.cancel_button.disabled = True
                    await self.event.event_buttons_message.edit(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt4}', view=self.event.event_buttons)
                except Exception as e:
                    logger.exception(f'Error cancelling guild event to reschedule: {e}')
                client.events.remove(self.event)
                self.event = new_event
                client.events.append(self.event)
                try:
                    participant = self.event.get_participant(interaction.user.name)
                    participant.set_no_availability()
                    await self.event.request_availability(interaction, reschedule=True)
                except Exception as e:
                    logger.exception(f'Error with RESCHEDULE button requesting availability: {e}')
                persist.write(client.get_events_dict())
            self.reschedule_button.callback = reschedule_button_callback
            self.add_item(self.reschedule_button)

        # Cancel the event
        def add_cancel_button(self) -> None:
            async def cancel_button_callback(interaction: Interaction):
                try:
                    self.event.event_buttons_msg_content_pt2 = f'\n**Cancelled by:** {interaction.user.name} at {datetime.now().astimezone().strftime("%H:%M")} ET'
                    await self.event.event_buttons_message.edit(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt4}', view=self.event.event_buttons)
                    await self.event.text_channel.send(f'{self.event.get_names_string(subscribed_only=True, mention=True)}\n{interaction.user.name} cancelled {self.event.name}.')
                    if self.event.created:
                        await self.event.scheduled_event.delete(reason=f'Cancel button pressed by {interaction.user.name}.')
                except Exception as e:
                    logger.exception(f'Error in cancel button callback: {e}')
                await self.event.event_buttons_message.delete()
                logger.info(f'{self.event.name}: {interaction.user} cancelled by button press and removed from memory')
                client.events.remove(self.event)
                self.event = None
                self.cancel_button.style = ButtonStyle.gray
                self.start_button.disabled = True
                self.end_button.disabled = True
                self.reschedule_button.disabled = True
                self.cancel_button.disabled = True
                persist.write(client.get_events_dict())
            self.cancel_button.callback = cancel_button_callback
            self.add_item(self.cancel_button)

    class ExistingGuildEventsSelect(Select):
        def __init__(self, guild: Guild):
            self.guild = guild
            options = [
                SelectOption(label=guild_event.name, description=guild_event.description, value=str(guild_event.id))
                for guild_event in self.guild.scheduled_events
            ]
            super().__init__(placeholder='Guild Event', options=options)

        # Select a guild event to attach to
        async def callback(self, interaction: Interaction):
            logger.info(f'{interaction.user.name} selected a guild event to attach to')
            selected_guild_event_id = int(self.values[0])
            selected_guild_event: ScheduledEvent = await self.guild.get_scheduled_event(selected_guild_event_id)
            if selected_guild_event:
                if selected_guild_event.name in [event.name for event in client.events]:
                    logger.info(f'\tGuild event had the same name as an existing event object')
                    await interaction.response.send_message(f'I already have an event with this name!', ephemeral=True)
                    return
                await interaction.response.defer(ephemeral=True)
                participants = get_participants_from_interaction(interaction)
                event = Event(name=selected_guild_event.name, voice_channel=selected_guild_event.location, participants=participants, guild=self.guild, text_channel=interaction.channel, start_time=selected_guild_event.start_time)
                event.created = True
                response = event.get_event_buttons_message_string()
                event.event_buttons = EventButtons(event)
                event.event_buttons_message = await event.text_channel.send(content=response, view=event.event_buttons)
                await interaction.followup.send(f'Success!', ephemeral=True)
            else:
                await interaction.response.send_message(f'Error getting guild scheduled event.')
                logger.exception(f'Error getting guild scheduled event selected by {interaction.user.name}')

    class ExistingGuildEventsSelectView(View):
        def __init__(self, guild: Guild):
            super().__init__()
            self.add_item(ExistingGuildEventsSelect(guild))

    # Put participants into a list
    def get_participants_from_interaction(interaction: Interaction, include_exclude: INCLUDE_EXCLUDE = None, usernames: str = None, roles: str = None) -> list:
        participants = []
        # Add the scheduler/creator as a participant
        for member in interaction.channel.members:
            if member.name == interaction.user.name:
                participants.append(Participant(member=member))
                break

        # Add users meeting role criteria
        if roles and roles != '':
            try:
                roles = roles.split(',')
                roles = [role.strip() for role in roles]
                roles = [utils.find(lambda r: r.name.lower() == role.lower(), interaction.guild.roles) for role in roles]
                logger.debug(f'Roles: {roles}')
            except Exception as e:
                raise Exception(f'Failed to parse role(s): {e}')
            for member in interaction.channel.members:
                if member.bot or member.name == interaction.user.name:
                    continue
                logger.debug(f'Member roles: {member.roles}')
                found_role = False
                for role in roles:
                    if role in member.roles:
                        found_role = True
                        break
                if include_exclude == INCLUDE and found_role:
                    logger.debug(f'Added member {member.name}')
                    participants.append(Participant(member=member))
                elif include_exclude == EXCLUDE and not found_role:
                    logger.debug(f'Added member {member.name}')
                    participants.append(Participant(member=member))
            return participants

        # Add users meeting username criteria
        if usernames and usernames != '':
            try:
                usernames = usernames.split(',')
                usernames = [username.strip() for username in usernames]
                logger.debug(f'Usernames: {usernames}')
            except:
                raise Exception(f'Failed to parse username(s): {e}')
            for member in interaction.channel.members:
                if member.bot or member.name == interaction.user.name:
                    continue
                if include_exclude == INCLUDE and (member.name in usernames or str(member.id) in usernames):
                    logger.debug(f'Added member {member.name}')
                    participants.append(Participant(member=member))
                elif include_exclude == EXCLUDE and member.name not in usernames and str(member.id) not in usernames:
                    logger.debug(f'Added member {member.name}')
                    participants.append(Participant(member=member))
            return participants

        # Add all users in the channel
        for member in interaction.channel.members:
            if member.bot or member.name == interaction.user.name:
                continue
            participants.append(Participant(member=member))
        return participants


    @client.event
    async def on_ready():
        logger.info(f'{client.user} has connected to Discord!')
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
        # Owner syncs commands
        if 'sync' in message.content and message.author.id == OWNER_ID:
            await client.tree.sync()
            logger.info(f'User {message.author.name} synced commands')

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
        for participant in participants:
            participant.answered = True

        # Make event
        duration = timedelta(minutes=duration)
        event = Event(name=event_name, voice_channel=voice_channel, participants=participants, guild=interaction.guild, text_channel=interaction.channel, image_url=image_url, duration=duration, start_time=start_time_obj)
        client.events.append(event)
        await event.make_scheduled_event()
        response = event.get_event_buttons_message_string()

        try:
            await interaction.response.send_message(content=f'Event created!', ephemeral=True)
            event.event_buttons = EventButtons(event)
            event.event_buttons_message = await event.text_channel.send(content=response, view=event.event_buttons)
        except Exception as e:
            logger.exception(f'Error sending interaction response to create event command): {e}')
        persist.write(client.get_events_dict())

    @client.tree.command(name='schedule', description='Schedule an event.')
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
            event = Event(name=event_name, voice_channel=voice_channel, participants=participants, guild=interaction.guild, text_channel=interaction.channel, image_url=image_url, duration=duration)
            mentions = '\nWaiting for a response from:\n' + event.get_names_string(subscribed_only=True, mention=True)
            client.events.append(event)
        except Exception as e:
            await interaction.response.send_message(f'Failed to make event object: {e}')
            logger.exception(f'Error making event object: {e}')
            return

        # Request availability and make participant response tracker message
        try:
            await event.request_availability(interaction)
        except Exception as e:
            logger.exception(f'Error sending responded message or requesting availability: {e}')
        persist.write(client.get_events_dict())

    @client.tree.command(name='attach', description='Create an event message for an existing guild event.')
    async def attach_command(interaction: Interaction):
        logger.info(f'Received attach command request from {interaction.user.name}')
        await interaction.response.send_message(f'Select an existing guild event from the dropdown menu.', view=ExistingGuildEventsSelectView(), ephemeral=True)

    @tasks.loop(seconds=30)
    async def update():
        # Go through created events and remove availability during the event time of all shared participants
        for event in client.events:
            for participant in event.participants:
                participant.remove_past_availability()
                if not event.created and not participant.availability:
                    participant.answered = False
            if event.created:
                for other_event in client.events:
                    if other_event == event:
                        continue
                    for participant in other_event.participants:
                        participant.remove_availability_for_event(event)
            await event.update_responded_message()

        for event in client.events.copy():
            # Reset to ensure at least 30 seconds to finish answering
            if event.changed:
                event.changed = False
                continue

            # Remove events if a participant is unavailable
            if event.unavailable:
                try:
                    try:
                        await event.availability_message.delete()
                    except Exception as e:
                        logger.error(f'Error disabling availability buttons: {e}')
                    await event.responded_message.delete()
                    unavailable_names = []
                    for participant in event.participants:
                        if participant.unavailable:
                            if participant.member.nick:
                                unavailable_names.append(f'{participant.member.nick} ')
                            else:
                                unavailable_names.append(f'{participant.member.name} ')
                    if unavailable_names:
                        notification_message = f'{event.get_names_string(subscribed_only=True, mention=True)}\nScheduling for **{event.name}** has been cancelled by {", ".join(unavailable_names)}.\n'
                    else:
                        notification_message = f'{event.get_names_string(subscribed_only=True, mention=True)}\nScheduling for **{event.name}** has been cancelled; participants lack common availability.\n'
                    if event.text_channel:
                        await event.text_channel.send(notification_message)
                    else:
                        for participant in event.participants:
                            async with participant.msg_lock:
                                participant.member.send(notification_message)
                    logger.info(f'{event.name}: Participant(s) lacked (common) availability, removed event from memory')
                    client.events.remove(event)
                    del event
                except Exception as e:
                    logger.error(f'Error invalidating and deleting event: {e}')
                    continue
                continue

            # Countdown to start + 5 minute warning
            if event.created and not event.started:
                # Countdown
                try:
                    if datetime.now().astimezone() < event.start_time:
                        time_until_start: timedelta = event.start_time - datetime.now().astimezone()
                        event.mins_until_start = int(time_until_start.total_seconds()//60) + 1
                    elif datetime.now().astimezone().replace(second=0, microsecond=0) == event.start_time:
                        event.mins_until_start = 0
                    else:
                        time_until_start: timedelta = datetime.now().astimezone() - event.start_time
                        event.mins_until_start = int(time_until_start.total_seconds()//60) + 1
                    # Event start time is in the past
                    if event.mins_until_start < 0:
                        event.event_buttons_msg_content_pt2 = f'\n**Overdue by:**'
                        hrs_mins_overdue_start_string = mins_to_hrs_mins_string(event.mins_until_start)
                        response = f'{event.event_buttons_msg_content_pt1} {event.event_buttons_msg_content_pt2} {hrs_mins_overdue_start_string} {event.event_buttons_msg_content_pt4}'
                    # It is event start time
                    elif event.mins_until_start == 0:
                        event.event_buttons_msg_content_pt2 = f'\n**Starting now**'
                        response = f'{event.event_buttons_msg_content_pt1} {event.event_buttons_msg_content_pt2} {event.event_buttons_msg_content_pt4}'
                    # Event start time is in the future
                    else:
                        event.event_buttons_msg_content_pt2 = f'\n**Starts in:**'
                        hrs_mins_until_start_string = mins_to_hrs_mins_string(event.mins_until_start)
                        response = f'{event.event_buttons_msg_content_pt1} {event.event_buttons_msg_content_pt2} {hrs_mins_until_start_string} {event.event_buttons_msg_content_pt4}'
                    await event.event_buttons_message.edit(content=response, view=event.event_buttons)
                except Exception as e:
                    logger.error(f'{event.name}: Error counting down: {e}')
                    continue

                # Send 5 minute warning
                try:
                    if datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(minutes=5) == event.start_time and event.scheduled_event.status == EventStatus.scheduled and not event.started:
                        if not event.five_minute_warning_flag:
                            event.five_minute_warning_flag = True
                            if event.text_channel:
                                try:
                                    await event.text_channel.send(f'{event.get_names_string(subscribed_only=True, mention=True)}\n**5 minute warning!** {event.name} is scheduled to start in 5 minutes.')
                                except Exception as e:
                                    logger.error(f'Error sending 5 minute nudge: {e}')
                                    continue
                            else:
                                for participant in event.participants:
                                    async with participant.msg_lock:
                                        await participant.member.send(f'**5 minute warning!** {event.name} is scheduled to start in 5 minutes.')
                except Exception as e:
                    logger.error(f'{event.name}: Error sending 5 minute warning: {e}')
                continue

            # Skip the rest of update() for this event if we are waiting for answers
            if not event.has_everyone_answered():
                if event.number_of_responded() > 1:
                    event.check_availabilities()
                continue

            if event.created:
                continue

            # Delete availability request message
            try:
                await event.availability_message.delete()
            except Exception as e:
                logger.error(f'Error disabling availability buttons: {e}')

            # Compare availabilities
            try:
                event.compare_availabilities()
            except Exception as e:
                logger.error(f'Error comparing availabilities: {e}')
                continue

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
                    logger.error(f'{event.name}: Error messaging participants: {e}')
                continue
            # Create the event if it is ready to create
            else:
                # If start time is in the past, reset so it can recompare availabilities
                if event.start_time <= datetime.now().astimezone():
                    logger.warning(f'{event.name}: Tried to create event in the past! Resetting to recompare availabilities.')
                    event.start_time = None
                    event.ready_to_create = False
                    continue

                # Create event
                try:
                    await event.make_scheduled_event()
                except Exception as e:
                    logger.error(f'{event.name}: Error creating scheduled event: {e}')
                    continue

                # Go through created events and remove availability during the event time of all shared participants
                for other_event in client.events:
                    for participant in other_event.participants:
                        participant.remove_availability_for_event(event)

                # Calculate time until start
                try:
                    response = event.get_event_buttons_message_string()
                    if event.responded_message:
                        await event.responded_message.delete()
                    event.event_buttons = EventButtons(event)
                    event.event_buttons_message = await event.text_channel.send(content=response, view=event.event_buttons)
                except Exception as e:
                    logger.error(f'{event.name}: Error sending event created notification with buttons: {e}')
                    continue
        persist.write(client.get_events_dict())

    client.run(DISCORD_TOKEN)


if __name__ == '__main__':
    main()