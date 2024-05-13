'''Written by Cael Shoop.'''

import os
import random
import requests
from asyncio import Lock
from typing import Literal
from dotenv import load_dotenv
from datetime import datetime, timedelta
from discord import app_commands, Interaction, Intents, Client, ButtonStyle, EventStatus, EntityType, TextChannel, VoiceChannel, Message, ScheduledEvent, Guild, PrivacyLevel, User, utils, File
from discord.ui import View, Button, Modal, TextInput
from discord.ext import tasks

from participant import Participant, TimeBlock
from logger import log_info, log_warn, log_error, log_debug

load_dotenv()

INCLUDE = 'INCLUDE'
EXCLUDE = 'EXCLUDE'
INCLUDE_EXCLUDE: Literal = Literal[INCLUDE, EXCLUDE]


def main():
    class SchedulerClient(Client):
        def __init__(self, intents):
            super(SchedulerClient, self).__init__(intents=intents)
            self.tree = app_commands.CommandTree(self)
            self.events = []

        async def make_scheduled_event(self, event):
            event.scheduled_event = await event.guild.create_scheduled_event(name=event.name, description='Bot-generated event', start_time=event.start_time, entity_type=event.entity_type, channel=event.voice_channel, privacy_level=event.privacy_level)
            if event.image_url:
                try:
                    response = requests.get(event.image_url)
                    if response.status_code == 200:
                        await event.scheduled_event.edit(image=response.content)
                        log_info(f'{event.name}> Processed image')
                    else:
                        event.image_url = ''
                        log_warn(f'{event.name}> Failed to get image')
                except Exception as e:
                    event.image_url = ''
                    log_error(f'{event.name}> Failed to process image: {e}')
                    raise(e)
            event.ready_to_create = False
            event.created = True
            log_info(f'{event.name}> Created event starting at {event.start_time.hour}:{event.start_time.minute} ET')
            return event.scheduled_event

        async def setup_hook(self):
            await self.tree.sync()

    discord_token = os.getenv('DISCORD_TOKEN')
    client = SchedulerClient(intents=Intents.all())

    class Event:
        def __init__(self, name: str, entity_type: EntityType, voice_channel: VoiceChannel, participants: list, guild: Guild, text_channel: TextChannel, image_url: str, duration: int = 30, start_time: datetime = None):
            self.name = name
            self.guild = guild
            self.entity_type = entity_type
            self.interaction_message = None
            self.responded_message = None
            self.text_channel = text_channel
            self.voice_channel = voice_channel
            self.privacy_level = PrivacyLevel.guild_only
            self.participants = participants
            self.image_url = image_url
            self.avail_buttons: AvailabilityButtons = None
            self.event_buttons: EventButtons = None
            self.event_buttons_message: Message = None
            self.event_buttons_msg_content_pt1: str = ''
            self.event_buttons_msg_content_pt2: str = ''
            self.event_buttons_msg_content_pt3: str = ''
            self.event_buttons_msg_content_pt4: str = ''
            self.ready_to_create = False
            self.created = False
            self.started = False
            self.scheduled_event: ScheduledEvent = None
            self.changed = False
            self.start_time: datetime = start_time
            self.duration = timedelta(minutes=float(duration))
            self.mins_until_start = 0
            self.alt_countdown_check = False
            self.unavailable = False

        async def intersect_time_blocks(timeblocks1, timeblocks2, duration):
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
            available_timeblocks = []

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
                        if other_participant.member.name == participant.name:
                            return participant.availability
            return None

        # All participants have responded
        def has_everyone_answered(self):
            for participant in self.participants:
                if participant.subscribed and not participant.answered:
                    return False
            return True

        # Request availability from all participants
        async def request_availability(self, interaction: Interaction, duration: int = 30, reschedule: bool = False):
            self.avail_buttons = AvailabilityButtons(event=self)
            if not reschedule:
                await interaction.response.send_message(f'**Event name:** {self.name}'
                        f'\n**Duration:** {duration} minutes'
                        f'\n\nSelect **Respond** to enter your availability.'
                        f'\n**Full** will mark you as available at any time.'
                        f'\n**None** will cancel scheduling.'
                        f'\n**Unsubscribe** will allow the event to occur without you; however, you can still respond and participate.'
                        f'\n\nThe event will be either created or cancelled within a minute after the last person responds.️', view=self.avail_buttons)
            else:
                await interaction.response.send_message(f'**Event name:** {self.name}'
                        f'\n**Duration:** {duration} minutes'
                        f'\n\nSelect **Respond** to enter your new availability.'
                        f'\n**Full** will mark you as available at any time.'
                        f'\n**None** will cancel scheduling.'
                        f'\n**Unsubscribe** will allow the event to occur without you; however, you can still respond and participate.', view=self.avail_buttons, ephemeral=True)
                participant = self.get_participant(interaction.user.name)
                participant.answered = False
            self.interaction_message = await interaction.original_response()

        async def update_message(self):
            if self.has_everyone_answered():
                try:
                    await self.responded_message.edit(content=f'Everyone has responded.')
                except Exception as e:
                    log_error(f'{self.name}> Error editing responded message with "everyone has responded": {e}')
                    raise(e)
                return
            try:
                mentions = self.get_names_string(subscribed_only=True, unanswered_only=True, mention=True)
                await self.responded_message.edit(content=f'Waiting for a response from these participants:\n{mentions}')
            except Exception as e:
                log_error(f'{self.name}> Error getting mentions string or editing responded message: {e}')
                raise(e)

    class AvailabilityModal(Modal):
        def __init__(self, event, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.event = event
            date = datetime.now().astimezone().strftime('%m/%d/%Y')
            self.timeslot1 = TextInput(label='Timeslot 1', placeholder='8-11, 1pm-3pm (i.e. Available 0800-1100, 1300-1500)')
            self.timeslot2 = TextInput(label='Timeslot 2', placeholder='15:30-17 (i.e. Available 1530-1700)', required=False)
            self.timeslot3 = TextInput(label='Timeslot 3', placeholder='-2030, 22- (i.e. Available now-2030, 2200-0000)', required=False)
            self.date = TextInput(label='Date', placeholder='MM/DD/YYYY', required=False, default=date)
            self.timezone = TextInput(label='Timezone', placeholder='ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|PT|PST|PDT', required=False, default='ET')
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
                availability = ''
                log_info(f'{self.event.name}> Received availability from {interaction.user.name}:')
                for timeblock in participant.availability:
                    availability += f'{timeblock.start_time.strftime("%m/%d/%Y: %H%M")} - {timeblock.end_time.strftime("%H%M")}\n'
                    log_info(f'{self.event.name}> \t{timeblock.start_time.strftime("%m/%d/%Y: %H%M")} - {timeblock.end_time.strftime("%H%M")}')
                await interaction.response.send_message(f'**__Availability received!__**\n{availability}', ephemeral=True)
                self.event.changed = True
                await self.event.update_message()
            except Exception as e:
                log_error(f'{self.event.name}> Error setting specific availability: {e}')
                raise(e)

        async def on_error(self, interaction: Interaction, error: Exception):
            await interaction.response.send_message(f'Oops! Something went wrong: {error}', ephemeral=True)
            log_error(f'{self.event.name}> Error getting availability from {interaction.user.name}: {error}')
            raise(error)

    class AvailabilityButtons(View):
        def __init__(self, event: Event):
            super().__init__(timeout=None)
            self.event = event
            self.respond_label = "Respond"
            self.full_label = "Full"
            self.none_label = "None"
            self.reuse_label = "Use Existing"
            self.unsub_label = "Unsubscribe"
            self.respond_button = self.add_respond_button()
            self.full_button = self.add_full_button()
            self.none_button = self.add_none_button()
            self.reuse_button = self.add_reuse_button()
            self.unsub_button = self.add_unsub_button()

        def add_respond_button(self):
            button = Button(label=self.respond_label, style=ButtonStyle.blurple)
            async def respond_button_callback(interaction: Interaction):
                try:
                    await interaction.response.send_modal(AvailabilityModal(event=self.event, title='Availability'))
                except Exception as e:
                    log_error(f'Error sending availability modal: {e}')
                    raise(e)
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
                    log_info(f'{self.event.name}> {interaction.user.name} selected full availability')
                    participant.set_full_availability()
                    participant.answered = True
                    await interaction.response.send_message(f'You have been marked as fully available.', ephemeral=True)
                else:
                    log_info(f'{self.event.name}> {interaction.user.name} deselected full availability')
                    participant.set_no_availability()
                    if participant.subscribed:
                        participant.answered = False
                    await interaction.response.send_message(f'Your availability has been cleared.', ephemeral=True)
                await self.event.update_message()
            button.callback = full_button_callback
            self.add_item(button)
            return button

        def add_none_button(self):
            button = Button(label=self.none_label, style=ButtonStyle.blurple)
            async def none_button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                participant = self.event.get_participant(interaction.user.name)
                if not participant.unavailable:
                    participant.set_no_availability()
                    participant.unavailable = True
                    participant.answered = True
                    self.event.unavailable = True
                    await interaction.response.send_message(f'You have been marked as unavailable for {self.event.name}.', ephemeral=True)
                    log_info(f'{self.event.name}> {interaction.user.name} selected no availability')
                else:
                    participant.unavailable = False
                    if participant.subscribed:
                        participant.answered = False
                    self.event.unavailable = False
                    await interaction.response.send_message(f'You are no longer marked as unavailable for {self.event.name}.', ephemeral=True)
                    log_info(f'{self.event.name}> {interaction.user.name} deselected no availability')
                await self.event.update_message()
            button.callback = none_button_callback
            self.add_item(button)
            return button

        def add_reuse_button(self):
            button = Button(label=self.reuse_label, style=ButtonStyle.blurple)
            async def reuse_button_callback(interaction: Interaction):
                log_info(f'{self.event.name}> Reuse button pressed by {interaction.user.name}')
                participant = self.event.get_participant(interaction.user.name)
                found_availability = self.event.get_other_availability(participant)
                if not found_availability:
                    log_info(f'{self.event.name}> \tNo existing availability found for {interaction.user.name}')
                    await interaction.response.send_message(f'No existing availability found.', ephemeral=True)
                    return
                log_info(f'{self.event.name}> Found existing availability for {interaction.user.name}')
                participant.availability = found_availability
                participant.answered = True
                response = 'Found existing availability!'
                for timeblock in found_availability:
                    response += f'\n{timeblock.start_time.strftime("%m/%d/%Y: %H:%M")} - {timeblock.end_time.strftime("%H:%M")}'
                await interaction.response.send_message(response, ephemeral=True)
                await self.event.update_message()
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
                    log_info(f'{self.event.name}> {interaction.user.name} unsubscribed')
                    participant.subscribed = False
                    participant.answered = True
                    await interaction.response.send_message(f'You have been unsubscribed from {self.event.name}.', ephemeral=True)
                else:
                    log_info(f'{self.event.name}> {interaction.user.name} resubscribed')
                    participant.subscribed = True
                    participant.answered = False
                    await interaction.response.send_message(f'You have been resubscribed to {self.event.name}.', ephemeral=True)
                await self.event.update_message()
            button.callback = unsub_button_callback
            self.add_item(button)
            return button

        def disable_all_buttons(self):
            self.respond_button.disabled = True
            self.full_button.disabled = True
            self.none_button.disabled = True
            self.reuse_button.disabled = True
            self.unsub_button.disabled = True

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
                self.event.text_channel = interaction.channel
                if not self.event.created or self.event.scheduled_event.status != EventStatus.scheduled:
                    await interaction.response.edit_message(view=self)
                    return
                log_info(f'{self.event.name}> {interaction.user} started by button press')
                participant_names = [participant.member.name for participant in self.event.participants]
                if interaction.user.name not in participant_names:
                    self.event.participants.append(interaction.user)
                try:
                    await self.event.scheduled_event.start(reason=f'Start button pressed by {interaction.user.name}.')
                    self.event.event_buttons_msg_content_pt2 = f'\n**Started at:** {datetime.now().astimezone().strftime("%H:%M")} ET'
                    await self.event.event_buttons_message.edit(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt4}', view=self.event.event_buttons)
                except Exception as e:
                    log_error(f'Error starting event or manipulating event control message: {e}')
                    raise(e)
                self.event.started = True
                self.start_button.style = ButtonStyle.green
                self.start_button.disabled = True
                self.end_button.disabled = False
                self.reschedule_button.disabled = True
                self.cancel_button.disabled = True
                try:
                    await interaction.response.edit_message(view=self)
                except Exception as e:
                    log_error(f'Error responding to START button interaction: {e}')
                    raise(e)
            self.start_button.callback = start_button_callback
            self.add_item(self.start_button)

        def add_end_button(self):
            self.end_button.disabled = True
            async def end_button_callback(interaction: Interaction):
                self.event.text_channel = interaction.channel
                if self.event.scheduled_event.status != EventStatus.active and self.event.scheduled_event.status != EventStatus.scheduled:
                    await interaction.response.edit_message(view=self)
                    return
                log_info(f'{self.event.name}> {interaction.user} ended by button press')
                try:
                    await self.event.scheduled_event.delete(reason=f'End button pressed by {interaction.user.name}.')
                    self.event.event_buttons_msg_content_pt3 = f'\n**Ended at:** {datetime.now().astimezone().strftime("%H:%M")} ET'
                    await self.event.event_buttons_message.edit(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt3} {self.event.event_buttons_msg_content_pt4}', view=self.event.event_buttons)
                except Exception as e:
                    log_error(f'Error ending event or manipulating event control message: {e}')
                    raise(e)
                client.events.remove(self.event)
                self.event = None
                self.end_button.style = ButtonStyle.blurple
                self.end_button.disabled = True
                self.reschedule_button.disabled = True
                self.cancel_button.disabled = True
                try:
                    await interaction.response.edit_message(view=self)
                except Exception as e:
                    log_error(f'Error responding to END button interaction: {e}')
                    raise(e)
            self.end_button.callback = end_button_callback
            self.add_item(self.end_button)

        def add_reschedule_button(self):
            async def reschedule_button_callback(interaction: Interaction):
                self.event.text_channel = interaction.channel
                if not self.event.created:
                    await interaction.response.edit_message(view=self)
                    return
                log_info(f'{self.event.name}> {interaction.user} rescheduled by button press')
                participant_names = [participant.member.name for participant in self.event.participants]
                if interaction.user.name not in participant_names:
                    self.event.participants.append(interaction.user)
                new_event = Event(self.event.name, self.event.entity_type, self.event.voice_channel, self.event.participants, self.event.guild, interaction.channel, self.event.image_url, self.event.duration) #, weekly
                try:
                    await self.event.scheduled_event.delete(reason=f'Reschedule button pressed by {interaction.user.name}.')
                    self.event.event_buttons_msg_content_pt2 = f'\n**RESCHEDULED**'
                    await self.event.event_buttons_message.edit(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt4}', view=self.event.event_buttons)
                    await self.event.text_channel.send(f'{self.event.get_names_string(subscribed_only=True, mention=True)}\n{interaction.user.mention} is rescheduling {self.event.name}.')
                except Exception as e:
                    log_error(f'Error cancelling guild event to reschedule: {e}')
                    raise(e)
                self.start_button.disabled = True
                self.start_button.style = ButtonStyle.blurple
                self.end_button.disabled = True
                self.end_button.style = ButtonStyle.blurple
                self.reschedule_button.disabled = True
                self.cancel_button.disabled = True
                await interaction.response.edit_message(view=self)
                client.events.remove(self.event)
                self.event = new_event
                client.events.append(self.event)
                try:
                    await self.event.request_availability(interaction, self.event.duration, reschedule=True)
                    await self.event.update_message()
                except Exception as e:
                    log_error(f'Error with RESCHEDULE button requesting availability: {e}')
                    raise(e)
            self.reschedule_button.callback = reschedule_button_callback
            self.add_item(self.reschedule_button)

        def add_cancel_button(self):
            async def cancel_button_callback(interaction: Interaction):
                self.event.text_channel = interaction.channel
                try:
                    self.event.event_buttons_msg_content_pt2 = f'\n**Cancelled by:** {interaction.user.name} at {datetime.now().astimezone().strftime("%H:%M")} ET'
                    await self.event.event_buttons_message.edit(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt4}', view=self.event.event_buttons)
                    await self.event.text_channel.send(f'{self.event.get_names_string(subscribed_only=True, mention=True)}\n{interaction.user.name} cancelled {self.event.name}.')
                    if self.event.created:
                        await self.event.scheduled_event.delete(reason=f'Cancel button pressed by {interaction.user.name}.')
                except Exception as e:
                    log_error(f'Error in cancel button callback: {e}')
                    raise(e)
                log_info(f'{self.event.name}> {interaction.user} cancelled by button press')
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
                    log_error(f'Error sending CANCEL button interaction response or cancelled message to text channel: {e}')
                    raise(e)
            self.cancel_button.callback = cancel_button_callback
            self.add_item(self.cancel_button)

    # Add a 0 if the digit is < 10
    def double_digit_string(digit_string: str):
        if int(digit_string) < 10 and len(digit_string) == 1:
            digit_string = '0' + digit_string
        return digit_string

    # Put participants into a list
    def get_participants_from_interaction(interaction: Interaction, include_exclude: INCLUDE_EXCLUDE, role: str):
        participants = []
        if role != None:
            role = utils.find(lambda r: r.name.lower() == role.lower(), interaction.guild.roles)
        for member in interaction.channel.members:
            if member.bot:
                continue
            if include_exclude == INCLUDE:
                if member.name != interaction.user.name and role != None and role not in member.roles:
                    continue
                participant = Participant(member)
                participants.append(participant)
        return participants


    @client.event
    async def on_ready():
        log_info(f'{client.user} has connected to Discord!')
        if not update.is_running():
            update.start()

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
                            log_info(f'{event.name}> {message.author.name} added an image')
                        except Exception as e:
                            await message.channel.send(f'Failed to add your image to {event.name}.\nError: {e}', ephemeral=True)
                            log_warn(f'{event.name}> Error adding image from {message.author.name}: {e}')
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
    @app_commands.describe(role='Only include/exclude users with this role as participants.')
    @app_commands.describe(duration='Event duration in minutes (30 minutes default).')
    async def create_command(interaction: Interaction, event_name: str, voice_channel: VoiceChannel, start_time: str, image_url: str = None, include_exclude: INCLUDE_EXCLUDE = INCLUDE, role: str = None, duration: int = 30):
        log_info(f'{event_name}> Received event creation request from {interaction.user.name}')

        # Parse start time
        start_time = start_time.strip()
        start_time.replace(':', '')
        if len(start_time) == 3:
            start_time = '0' + start_time
        elif len(start_time) != 4:
            await interaction.response.send_message(f'Invalid start time format. Examples: "1630" or "00:30"')
        hour = int(start_time[:2])
        minute = int(start_time[2:])
        start_time_obj = get_datetime_from_label(f"{hour}:{minute}")
        if start_time_obj <= datetime.now().astimezone():
            await interaction.response.send_message(f'Start time must be in the future!')

        participants = get_participants_from_interaction(interaction, include_exclude, role)

        # Make event
        event = Event(event_name, EntityType.voice, voice_channel, participants, interaction.guild, interaction.channel, image_url, duration, start_time_obj)
        event.start_time = start_time_obj
        client.events.append(event)
        event.scheduled_event = await client.make_scheduled_event(event)
        response = ''
        if event.start_time.hour < 10 and event.start_time.minute < 10:
            response = f'{interaction.user.name} created an event called {event.name} starting at 0{event.start_time.hour}:0{event.start_time.minute} ET.'
        elif event.start_time.hour >= 10 and event.start_time.minute < 10:
            response = f'{interaction.user.name} created an event called {event.name} starting at {event.start_time.hour}:0{event.start_time.minute} ET.'
        elif event.start_time.hour < 10 and event.start_time.minute >= 10:
            response = f'{interaction.user.name} created an event called {event.name} starting at 0{event.start_time.hour}:{event.start_time.minute} ET.'
        else:
            response = f'{interaction.user.name} created an event called {event.name} starting at {event.start_time.hour}:{event.start_time.minute} ET.'
        try:
            await interaction.response.send_message(response, view=EventButtons(event))
        except Exception as e:
            log_error(f'Error sending interaction response to create event command: {e}')
            raise(e)

    @client.tree.command(name='schedule', description='Create a scheduling event.')
    @app_commands.describe(event_name='Name for the event.')
    @app_commands.describe(voice_channel='Voice channel for the event.')
    @app_commands.describe(image_url="URL to an image for the event.")
    @app_commands.describe(include_exclude='Whether to include or exclude users with the designated role.')
    @app_commands.describe(role='Only include/exclude users with this role as participants.')
    @app_commands.describe(duration="Event duration in minutes (30 minutes default).")
    async def schedule_command(interaction: Interaction, event_name: str, voice_channel: VoiceChannel, image_url: str = None, include_exclude: INCLUDE_EXCLUDE = INCLUDE, role: str = None, duration: int = 30):
        log_info(f'{event_name}> Received event schedule request from {interaction.user.name}')

        # Generate participants list
        try:
            participants = get_participants_from_interaction(interaction, include_exclude, role)
        except Exception as e:
            await interaction.response.send_message(f'Failed to generate participants list: {e}')
            log_error(f'Error getting participants: {e}')
            raise(e)
            return

        # Make event object
        try:
            event = Event(event_name, EntityType.voice, voice_channel, participants, interaction.guild, interaction.channel, image_url, duration)
            mentions = '\nWaiting for a response from these participants:\n' + event.get_names_string(subscribed_only=True, mention=True)
            client.events.append(event)
        except Exception as e:
            await interaction.response.send_message(f'Failed to make event object: {e}')
            log_error(f'Error making event object: {e}')
            raise(e)
            return

        # Request availability and make participant response tracker message
        try:
            await event.request_availability(interaction, duration)
            event.responded_message = await interaction.channel.send(f'{mentions}')
        except Exception as e:
            log_error(f'Error sending responded message or requesting availability: {e}')
            raise(e)

    @client.tree.command(name='bind', description='Bind a text channel to an existing event.')
    @app_commands.describe(event_name='Name of the vent to set this text channel for.')
    async def bind_command(interaction: Interaction, event_name: str):
        event_name = event_name.lower()
        for event in client.events:
            if event_name == event.name.lower():
                # Bind the text channel to the interaction channel
                try:
                    event.text_channel = interaction.channel
                except Exception as e:
                    log_error(f'Error binding channel: {e}')
                    raise(e)
                    try:
                        await interaction.response.send_message(f'Failed to bind channel: {e}', ephemeral=True)
                    except:
                        log_error(f'Failed to respond to bind command: {e}')
                        raise(e)
                    return

                # Respond to interaction
                try:
                    await interaction.response.send_message(f'Bound this text channel to {event.name}.', ephemeral=True)
                    await interaction.channel.send(f'{event.name} is scheduled to start on {event.start_time.strftime("%m/%d")} at {event.start_time.strftime("%H:%M")} ET.\n', view=EventButtons(event))
                except Exception as e:
                    log_error(f'Error responding to bind command: {e}')
                    raise(e)
                return

        try:
            await interaction.response.send_message(f'Could not find event {event_name}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}', ephemeral=True)
        except Exception as e:
            log_error(f'Error responding to bind command: {e}')
            raise(e)

    @tasks.loop(seconds=30)
    async def update():
        for event in client.events.copy():
            # Remove events if a participant is unavailable
            if event.unavailable:
                try:
                    unavailable_names = ''
                    unavailable_counter = 0
                    for participant in event.participants:
                        if participant.unavailable:
                            unavailable_names += f'{participant.member.name} '
                            unavailable_counter += 1
                    notification_message = f'{event.get_names_string(subscribed_only=True, mention=True)}\nScheduling for **{event.name}** has been cancelled.\n'
                    notification_message += unavailable_names
                    if unavailable_counter == 1:
                        notification_message += 'is unavailable.'
                    elif unavailable_counter > 1:
                        notification_message += 'are unavailable.'
                    if event.text_channel:
                        await event.text_channel.send(notification_message)
                    else:
                        for participant in event.participants:
                            async with participant.msg_lock:
                                participant.member.send(notification_message)
                    log_info(f'{event.name}> Participants lacked common availability, removed event from memory')
                    client.events.remove(event)
                    del event
                except Exception as e:
                    log_error(f'Error invalidating and deleting event: {e}')
                    raise(e)
                continue

            # Countdown to start
            # Send 5 minute warning
            if event.created and not event.started:
                try:
                    event.alt_countdown_check = not event.alt_countdown_check
                    if event.alt_countdown_check:
                        event.mins_until_start -= 1
                        await event.event_buttons_message.edit(content=f'{event.event_buttons_msg_content_pt1} {event.event_buttons_msg_content_pt2} {event.mins_until_start} {event.event_buttons_msg_content_pt3}', view=event.event_buttons)

                    if datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(minutes=5) == event.start_time and event.scheduled_event.status == EventStatus.scheduled and not event.started:
                        if event.text_channel:
                            try:
                                await event.text_channel.send(f'{event.get_names_string(subscribed_only=True, mention=True)}\n**5 minute warning!** {event.name} is scheduled to start in 5 minutes.')
                            except Exception as e:
                                log_error(f'Error sending 5 minute nudge: {e}')
                                raise(e)
                        elif not event.text_channel:
                            for participant in event.participants:
                                async with participant.msg_lock:
                                    await participant.member.send(f'**5 minute warning!** {event.name} is scheduled to start in 5 minutes.')
                except Exception as e:
                    log_error(f'{event.name}> Error sending 5 minute warning: {e}')
                    raise(e)
                continue

            # Reset to ensure at least a minute to finish answering
            if event.changed or not event.has_everyone_answered():
                event.changed = False
                continue

            # Compare availabilities
            try:
                await event.compare_availabilities()
            except Exception as e:
                log_error(f'Error comparing availabilities: {e}')
                raise(e)

            # Cancel the event if no common availability was found
            if not event.ready_to_create:
                try:
                    log_info(f'{event.name}> No common availability found')
                    if event.text_channel:
                        await event.text_channel.send(f'No common availability was found. Scheduling for {event.name} has been cancelled.')
                    else:
                        for participant in event.participants:
                            async with participant.msg_lock:
                                await participant.member.send(f'No common availability was found. Scheduling for {event.name} has been cancelled.')
                    client.events.remove(event)
                    del event
                except Exception as e:
                    log_error(f'{event.name}> Error messaging participants: {e}')
                    raise(e)
                continue

            # Create the event if it is ready to create
            else:
                # Create guild scheduled event
                try:
                    event.scheduled_event = await client.make_scheduled_event(event)
                except Exception as e:
                    log_error(f'{event.name}> Error creating scheduled event: {e}')
                    raise(e)

                # List subscribed people and list unsubscribed people
                unsubbed = event.get_names_string(unsubscribed_only=True)
                if unsubbed:
                    unsubbed = f'\nUnsubscribed: {unsubbed}'

                # Calculate time until start
                try:
                    time_until_start: timedelta = event.start_time - datetime.now().astimezone()
                    event.mins_until_start = int(time_until_start.total_seconds() / 60)
                    event.event_buttons_msg_content_pt1 = f'{event.get_names_string(subscribed_only=True, mention=True)}'
                    event.event_buttons_msg_content_pt1 += f'\n**Event name:** {event.name}'
                    event.event_buttons_msg_content_pt1 += f'\n**Scheduled:** {event.start_time.strftime("%m/%d")} at {event.start_time.strftime("%H:%M")} ET'
                    event.event_buttons_msg_content_pt2 = f'\n**Starts in:**'
                    event.event_buttons_msg_content_pt3 = f'minutes'
                    event.event_buttons_msg_content_pt4 += f'\n{unsubbed}'
                    response = f'{event.event_buttons_msg_content_pt1} {event.event_buttons_msg_content_pt2} {event.mins_until_start} {event.event_buttons_msg_content_pt3} {event.event_buttons_msg_content_pt4}'
                    await event.responded_message.delete()
                    event.event_buttons = EventButtons(event)
                    event.event_buttons_message = await event.text_channel.send(content=response, view=event.event_buttons)
                except Exception as e:
                    log_error(f'{event.name}> Error sending event created notification with buttons: {e}')
                    raise(e)

    client.run(discord_token)


if __name__ == '__main__':
    main()

# TODO CORRECT
# Rescheduling functionality
# Create command (create event at time)

# TODO IMPLEMENT
# Attach command (connect to an existing guild scheduled event)
# ? Modal for schedule command/reschedule

# TODO TEST
# Compare availabilities
# Unsub logic (unsub vs. answered, esp. in buttons)
# Reuse availability functionality
