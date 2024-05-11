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

from participant import Participant
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
            self.ready_to_create = False
            self.created = False
            self.started = False
            self.scheduled_event: ScheduledEvent = None
            self.changed = False
            self.start_time: datetime = start_time
            self.duration = timedelta(minutes=float(duration))
            self.unavailable = False

        async def compare_availabilities(self):
            self.avail_buttons.disable_buttons()
            await self.interaction_message.edit(view=self.avail_buttons)
            # Clear out past blocks
            for participant in self.participants:
                for idx, timeblock in enumerate(participant.availability.copy()):
                    if (timeblock.end_time - self.duration) < datetime.now().astimezone():
                        participant.availability.remove(timeblock)
                    else:
                        participant.availability[idx].start_time = datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(minutes=5)

            # Find common time
            common_time_blocks = []
            for timeblock in self.participants[0].availability:
                available = True
                for participant in self.participants[1:]:
                    if not timeblock.overlaps_with(participant.availability):
                        available = False
                        break
                if available and timeblock.duration >= self.duration:
                    self.ready_to_create = True
                    self.start_time = timeblock.start_time
                    return

        def shares_participants(self, event):
            for self_participant in self.participants:
                for other_participant in event.participants:
                    if self_participant.member == other_participant.member:
                        return other_participant
            return None

        def has_everyone_answered(self):
            for participant in self.participants:
                if participant.subscribed and not participant.answered:
                    return False
            return True

        async def request_availability(self, interaction: Interaction, duration: int = 30, reschedule: bool = False):
            self.avail_buttons = AvailabilityButtons(event=self)
            if not reschedule:
                await interaction.response.send_message(f'**Event name:** {self.name}'
                        f'\n**Duration:** {duration} minutes'
                        f'\n\nSelect **Respond** to enter your availability.'
                        f'\n**Full** will mark you as available at any time.'
                        f'\n**None** will cancel scheduling.'
                        f'\n**Unsubscribe** will allow the event to occur without you; however, you can still respond and participate.'
                        f'\n\nThe event will be either created or cancelled 1-2 minutes after the last person responds.️', view=self.avail_buttons)
            else:
                await interaction.response.send_message(f'**Event name:** {self.name}'
                        f'\n**Duration:** {duration} minutes'
                        f'\n\nSelect **Respond** to enter your new availability.'
                        f'\n**Full** will mark you as available at any time.'
                        f'\n**None** will cancel scheduling.'
                        f'\n**Unsubscribe** will allow the event to occur without you; however, you can still respond and participate.', view=self.avail_buttons)
                participant = get_participant_from_event(self, interaction.user.name)
                participant.answered = False
            self.interaction_message = await interaction.original_response()

        async def update_message(self):
            if self.has_everyone_answered():
                try:
                    await self.responded_message.edit(content=f'Everyone has responded.')
                except Exception as e:
                    log_error(f'{self.name}> Error editing responded message with "everyone has responded": {e}')
                return
            try:
                mentions = ''
                for participant in self.participants:
                    if participant.subscribed and not participant.answered:
                        mentions += f'{participant.member.mention} '
                mentions = '\nWaiting for a response from these participants:\n' + mentions
            except Exception as e:
                log_error(f'{self.name}> Error generating mentions list for responded message: {e}')
            try:
                await self.responded_message.edit(content=f'{mentions}')
            except Exception as e:
                log_error(f'{self.name}> Error editing responded message: {e}')

    class AvailabilityModal(Modal):
        def __init__(self, event, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.event = event
            self.slot1 = TextInput(label='Timeslot 1', placeholder='8-11, 1pm-3pm (i.e. Avail. 0800-1100, 1300-1500)')
            self.slot2 = TextInput(label='Timeslot 2', placeholder='15:30-17 (i.e. Avail. 1530-1700)', required=False)
            self.slot3 = TextInput(label='Timeslot 3', placeholder='-2030 (i.e. Avail. until 2030)', required=False)
            self.slot4 = TextInput(label='Timeslot 4', placeholder='22- (i.e. Avail. after 2200)', required=False)
            self.slotzone = TextInput(label='Timezone', placeholder='ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|PT|PST|PDT', required=False, default='ET')
            self.add_item(self.slot1)
            self.add_item(self.slot2)
            self.add_item(self.slot3)
            self.add_item(self.slot4)
            self.add_item(self.slotzone)

        async def on_submit(self, interaction: Interaction):
            # Participant availability
            participant = get_participant_from_event(self.event, interaction.user.name)
            avail_string = f'{self.slot1}, {self.slot2}, {self.slot3}, {self.slot4} {self.slotzone}'
            try:
                participant.set_specific_availability(avail_string)
            except Exception as e:
                log_error(f'{self.event.name}> Error setting specific availability: {e}')
            participant.answered = True
            await interaction.response.send_message(f'Availability received!', ephemeral=True)
            # Event management
            self.event.changed = True
            await self.event.update_message()
            log_info(f'{self.event.name}> Received availability from {interaction.user.name}:')
            for timeblock in participant.availability:
                log_info(f'{self.event.name}> \t{timeblock.start_time.strftime('%H%M')} - {timeblock.end_time.strftime('%H%M')}')

        async def on_error(self, interaction: Interaction, error: Exception):
            await interaction.response.send_message(f'Oops! Something went wrong: {error}', ephemeral=True)
            log_error(f'{self.event.name}> Error getting availability from {interaction.user.name}: {error}')

    class AvailabilityButtons(View):
        def __init__(self, event: Event):
            super().__init__(timeout=None)
            self.event = event
            self.respond_label = "Respond"
            self.full_label = "Full"
            self.none_label = "None"
            self.unsub_label = "Unsubscribe"
            self.respond_button = self.add_respond_button()
            self.full_button = self.add_full_button()
            self.none_button = self.add_none_button()
            self.unsub_button = self.add_unsub_button()

        def add_respond_button(self):
            button = Button(label=self.respond_label, style=ButtonStyle.blurple)
            async def respond_button_callback(interaction: Interaction):
                try:
                    await interaction.response.send_modal(AvailabilityModal(event=self.event, title='Availability'))
                except Exception as e:
                    log_error(f'Error sending availability modal: {e}')
            button.callback=respond_button_callback
            self.add_item(button)
            return button

        def add_full_button(self):
            button = Button(label=self.full_label, style=ButtonStyle.blurple)
            async def full_button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                participant = get_participant_from_event(self.event, interaction.user.name)
                if button.style == ButtonStyle.blurple:
                    button.style = ButtonStyle.green
                    try:
                        participant.set_full_availability()
                        participant.answered = True
                    except Exception as e:
                        log_error(f'{self.event.name}> Failed to set availability for {participant.member.name} to full: {e}')
                    log_info(f'{self.event.name}> {interaction.user.name} selected full availability')
                else:
                    button.style = ButtonStyle.blurple
                    participant.set_no_availability()
                    log_info(f'{self.event.name}> {interaction.user.name} deselected full availability')
                try:
                    await interaction.response.edit_message(view=self)
                    await self.event.update_message()
                except Exception as e:
                    log_error(f'{self.event.name}> Error with ALL button press by {interaction.user.name}: {e}')
            button.callback = full_button_callback
            self.add_item(button)
            return button

        def add_none_button(self):
            button = Button(label=self.none_label, style=ButtonStyle.blurple)
            async def none_button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                participant = get_participant_from_event(self.event, interaction.user.name)
                if button.style == ButtonStyle.blurple:
                    button.style = ButtonStyle.gray
                    try:
                        participant.set_no_availability()
                        participant.answered = True
                    except Exception as e:
                        log_error(f'{self.event.name}> Failed to set availability for {participant.member.name} to none: {e}')
                    self.event.reason += f'{interaction.user.name} has no availability. '
                    self.event.unavailable = True
                    log_info(f'{self.event.name}> {interaction.user.name} selected no availability')
                else:
                    button.style = ButtonStyle.blurple
                    self.event.reason.replace(f'{interaction.user.name} has no availability. ', '')
                    self.event.unavailable = False
                    log_info(f'{self.event.name}> {interaction.user.name} deselected no availability')
                try:
                    await interaction.response.edit_message(view=self)
                    await self.event.update_message()
                except Exception as e:
                    log_error(f'{self.event.name}> Error with NONE button press by {interaction.user.name}: {e}')
            button.callback = none_button_callback
            self.add_item(button)
            return button

        def add_unsub_button(self):
            button = Button(label=self.unsub_label, style=ButtonStyle.blurple)
            async def unsub_button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                participant = get_participant_from_event(self.event, interaction.user.name)
                participant.subscribed = not participant.subscribed
                if button.style == ButtonStyle.blurple:
                    button.style = ButtonStyle.gray
                    log_info(f'{self.event.name}> {interaction.user.name} unsubscribed')
                else:
                    button.style = ButtonStyle.blurple
                    log_info(f'{self.event.name}> {interaction.user.name} resubscribed')
                try:
                    await interaction.response.edit_message(view=self)
                    await self.event.update_message()
                except Exception as e:
                    log_error(f'{self.event.name}> Error with UNSUB button press by {interaction.user.name}: {e}')
            button.callback = unsub_button_callback
            self.add_item(button)
            return button

        def disable_buttons(self):
            self.respond_button.disabled = True
            self.full_button.disabled = True
            self.none_button.disabled = True
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
                await self.event.scheduled_event.start(reason='Start button pressed.')
                self.event.started = True
                self.start_button.style = ButtonStyle.green
                self.start_button.disabled = True
                self.end_button.disabled = False
                try:
                    await interaction.response.edit_message(view=self)
                except Exception as e:
                    log_error(f'Error responding to START button interaction: {e}')
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
                await self.event.scheduled_event.delete(reason='End button pressed.')
                self.event.created = False
                client.events.remove(self.event)
                del self.event
                self.end_button.style = ButtonStyle.gray
                self.end_button.disabled = True
                self.reschedule_button.disabled = True
                self.cancel_button.disabled = True
                try:
                    await interaction.response.edit_message(view=self)
                except Exception as e:
                    log_error(f'Error responding to END button interaction: {e}')
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
                await self.event.scheduled_event.delete(reason='Reschedule button pressed.')
                self.event.created = False
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
                mentions = ''
                for participant in self.event.participants:
                    if participant.member != interaction.user:
                        mentions += participant.member.mention
                try:
                    await self.event.text_channel.send(f'{mentions}\n{interaction.user.mention} is rescheduling {self.event.name}.')
                except Exception as e:
                    log_error(f'Error sending RESCHEDULE button text channel message: {e}')
                try:
                    await self.event.request_availability(interaction, self.event.duration, reschedule=True)
                    await self.event.update_message()
                except Exception as e:
                    log_error(f'Error with RESCHEDULE button requesting availability: {e}')
            self.reschedule_button.callback = reschedule_button_callback
            self.add_item(self.reschedule_button)

        def add_cancel_button(self):
            async def cancel_button_callback(interaction: Interaction):
                self.event.text_channel = interaction.channel
                if self.event.created:
                    await self.event.scheduled_event.delete(reason='Cancel button pressed.')
                    self.event.created = False
                await self.event.remove()
                client.events.remove(self.event)
                del self.event
                mentions = ''
                for participant in self.event.participants:
                    if participant.member != interaction.user:
                        async with client.msg_lock:
                            await participant.member.send(f'{interaction.user.name} has cancelled {self.event.name}.')
                        mentions += participant.member.mention
                log_info(f'{self.event.name}> {interaction.user} cancelled by button press')
                self.cancel_button.style = ButtonStyle.gray
                self.start_button.disabled = True
                self.end_button.disabled = True
                self.reschedule_button.disabled = True
                self.cancel_button.disabled = True
                try:
                    await interaction.response.edit_message(view=self)
                    await self.event.text_channel.send(f'{mentions}\n{interaction.user.mention} cancelled {self.event.name}.')
                except Exception as e:
                    log_error(f'Error sending CANCEL button interaction response or cancelled message to text channel: {e}')
            self.cancel_button.callback = cancel_button_callback
            self.add_item(self.cancel_button)

    def double_digit_string(digit_string: str):
        if int(digit_string) < 10 and len(digit_string) == 1:
            digit_string = '0' + digit_string
        return digit_string

    def get_participants_from_interaction(interaction: Interaction, include_exclude: INCLUDE_EXCLUDE, role: str):
        # Put participants into a list
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

    def get_participant_from_event(event: Event, username: str):
        for participant in event.participants:
            if participant.member.name == username:
                return participant

    def get_time():
        ct = str(datetime.now())
        hour = int(ct[11:13])
        minute = int(ct[14:16])
        return hour, minute


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
            return

        # Make event object
        try:
            event = Event(event_name, EntityType.voice, voice_channel, participants, interaction.guild, interaction.channel, image_url, duration)
            mentions = ''
            for participant in event.participants:
                mentions += f'{participant.member.mention} '
            mentions = '\nWaiting for a response from these participants:\n' + mentions
            client.events.append(event)
        except Exception as e:
            await interaction.response.send_message(f'Failed to make event object: {e}')
            log_error(f'Error making event object: {e}')
            return

        try:
            await event.request_availability(interaction, duration)
            event.responded_message = await interaction.channel.send(f'{mentions}')
        except Exception as e:
            log_error(f'Error sending responded message or requesting availability: {e}')

    # @client.tree.command(name='reschedule', description='Reschedule an existing scheduled event.')
    # @app_commands.describe(event_name='Name of the event to reschedule.')
    # @app_commands.describe(image_url='URL to an image for the event.')
    # @app_commands.describe(duration='Event duration in minutes (default 30 minutes).')
    # async def reschedule_command(interaction: Interaction, event_name: str, image_url: str = None, duration: int = 30):
    #     event_name = event_name.lower()
    #     for event in client.events.copy():
    #         if event_name == event.name.lower():
    #             log_info(f'{event.name}> {interaction.user.name} requested reschedule')
    #             if event.created:
    #                 new_event = Event(event.name, event.entity_type, event.voice_channel, event.participants, event.guild, interaction.channel, image_url, duration) #, weekly
    #                 await event.scheduled_event.delete(reason='Reschedule command issued.')
    #                 client.events.remove(event)
    #                 del event
    #                 client.events.append(new_event)
    #                 await interaction.response.send_message(f'{interaction.user.mention} is rescheduling.')
    #                 await new_event.request_availability(interaction, duration, reschedule=True)
    #             else:
    #                 await interaction.response.send_message(f'{event.name} has not been created yet. Your buttons will work until it is created or cancelled.')
    #             return
    #     try:
    #         await interaction.response.send_message(f'Could not find event {event_name}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}', ephemeral=True)
    #     except Exception as e:
    #         log_error(f'Error responding to reschedule command: {e}')

    # @client.tree.command(name='cancel', description='Cancel an event.')
    # @app_commands.describe(event_name='Name of the event to cancel.')
    # async def cancel_command(interaction: Interaction, event_name: str):
    #     event_name = event_name.lower()
    #     for event in client.events.copy():
    #         if event_name == event.name.lower():
    #             log_info(f'{event.name}> {interaction.user.name} cancelled event')
    #             if event.created:
    #                 await event.scheduled_event.delete(reason='Cancel command issued.')
    #             client.events.remove(event)
    #             del event
    #             await interaction.response.send_message(f'{mentions}\n{interaction.user.name} has cancelled {event.name}.')
    #             mentions = ''
    #             for participant in event.participants:
    #                 if participant.member.name != interaction.user.name:
    #                     async with participant.msg_lock:
    #                         await participant.member.send(f'{interaction.user.name} has cancelled {event.name}.')
    #                     mentions += participant.member.mention
    #             return
    #     try:
    #         await interaction.response.send_message(f'Could not find event {event_name}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}', ephemeral=True)
    #     except Exception as e:
    #         log_error(f'Error responding to cancel command: {e}')

    @client.tree.command(name='bind', description='Bind a text channel to an existing event.')
    @app_commands.describe(event_name='Name of the vent to set this text channel for.')
    async def bind_command(interaction: Interaction, event_name: str):
        event_name = event_name.lower()
        for event in client.events:
            if event_name == event.name.lower():
                try:
                    event.text_channel = interaction.channel
                except Exception as e:
                    log_error(f'Error binding channel: {e}')
                    try:
                        await interaction.response.send_message(f'Failed to bind channel: {e}', ephemeral=True)
                    except:
                        log_error(f'Failed to respond to bind command: {e}')
                    return

                try:
                    await interaction.response.send_message(f'Bound this text channel to {event.name}.', ephemeral=True)
                    await interaction.channel.send(f'{event.name} is scheduled to start on {event.start_time.strftime('%m/%d')} at {event.start_time.strftime('%H:%M')} ET.\n', view=EventButtons(event))
                except Exception as e:
                    log_error(f'Error responding to bind command: {e}')
                return

        try:
            await interaction.response.send_message(f'Could not find event {event_name}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}', ephemeral=True)
        except Exception as e:
            log_error(f'Error responding to bind command: {e}')

    @tasks.loop(minutes=1)
    async def update():
        for event in client.events.copy():
            if event.unavailable:
                try:
                    unavailable_mentions = ''
                    unavailable_counter = 0
                    for participant in event.participants:
                        async with participant.msg_lock:
                            await participant.member.send(f'**!!!!! __Scheduling for {event.name} has been cancelled!__ !!!!!**')
                        if participant.unavailable:
                            unavailable_mentions += f'{participant.member.mention} '
                            unavailable_counter += 1
                    notification_message = f'Scheduling for {event.name} has been cancelled.\n'
                    notification_message += unavailable_mentions
                    if unavailable_counter == 1:
                        notification_message += 'is unavailable.'
                    else:
                        notification_message += 'are unavailable.'
                    await event.text_channel.send(notification_message)
                    client.events.remove(event)
                    del event
                    log_info(f'{event.name}> Participants lacked common availability, removed event from memory')
                except Exception as e:
                    log_error(f'Error invalidating and deleting event: {e}')

        curTime = datetime.now().astimezone().replace(second=0, microsecond=0)
        for event in client.events.copy():
            try:
                if event.created:
                    if curTime + timedelta(minutes=5) == event.start_time and event.scheduled_event.status == EventStatus.scheduled and not event.started:
                        if event.text_channel:
                            try:
                                await event.text_channel.send(f'**5 minute warning!** {event.name} is scheduled to start in 5 minutes.')
                            except Exception as e:
                                log_error(f'Error sending 5 minute nudge: {e}')
                        elif not event.text_channel:
                            for participant in event.participants:
                                async with participant.msg_lock:
                                    await participant.member.send(f'**5 minute warning!** {event.name} is scheduled to start in 5 minutes.')
                    continue
            except Exception as e:
                log_error(f'{event.name}> Error sending 5 minute warning: {e}')

                if event.changed or not event.has_everyone_answered():
                    event.changed = False
                    continue

            try:
                await event.compare_availabilities()
            except Exception as e:
                log_error(f'Error comparing availabilities: {e}')
            try:
                if not event.ready_to_create:
                    log_info(f'{event.name}> No common availability found')
                    if event.text_channel:
                        await event.text_channel.send(f'No common availability was found. Scheduling for {event.name} has been cancelled.')
                    else:
                        for participant in event.participants:
                            async with participant.msg_lock:
                                await participant.member.send(f'No common availability was found. Scheduling for {event.name} has been cancelled.')
                    client.events.remove(event)
                    del event
                    continue
            except Exception as e:
                log_error(f'{event.name}> Error messaging participants: {e}')

            if event.ready_to_create:
                try:
                    event.scheduled_event = await client.make_scheduled_event(event)
                except Exception as e:
                    log_error(f'{event.name}> Error creating scheduled event: {e}')
                try:
                    mentions = ''
                    unsubbed = ''
                    for participant in event.participants:
                        if participant.subscribed:
                            mentions += f'{participant.member.mention} '
                        else:
                            unsubbed += f'{participant.member.name} '
                    if unsubbed != '':
                        unsubbed = '\nUnsubscribed: ' + unsubbed
                except Exception as e:
                    log_error(f'{event.name}> Error generating mentions/unsubbed strings: {e}')
                try:
                    response = f'{mentions}\nHeads up! You are all available for {event.name} starting on {event.start_time.strftime('%m/%d')} at {event.start_time.strftime('%H:%M')} ET.\n' + unsubbed
                    await event.text_channel.send(content=response, view=EventButtons(event))
                except Exception as e:
                    log_error(f'{event.name}> Error sending event created notification with buttons: {e}')

    client.run(discord_token)


if __name__ == '__main__':
    main()

# TODO:
# Rescheduling
# Cancel