'''Written by Cael Shoop.'''

import os
import random
import requests
from asyncio import Lock
from typing import Literal
from dotenv import load_dotenv
from datetime import datetime, timedelta
from discord import app_commands, Interaction, Intents, Client, ButtonStyle, EventStatus, EntityType, TextChannel, VoiceChannel, Message, ScheduledEvent, Guild, PrivacyLevel, User, utils, File
from discord.ui import View, Button
from discord.ext import tasks

from participant import Participant
from logger import log_info, log_warn, log_error, log_debug

load_dotenv()

INCLUDE = 'INCLUDE'
EXCLUDE = 'EXCLUDE'
INCLUDE_EXCLUDE: Literal = Literal[INCLUDE, EXCLUDE]


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

def get_time():
    ct = str(datetime.now())
    hour = int(ct[11:13])
    minute = int(ct[14:16])
    return hour, minute

class Event:
    def __init__(self, name: str, entity_type: EntityType, voice_channel: VoiceChannel, participants: list, guild: Guild, text_channel: TextChannel, image_url: str, duration: int = 30, start_time: datetime = None):
        self.name = name
        self.guild = guild
        self.entity_type = entity_type
        self.og_message_text = f'Event name: {self.name}'
        self.responded_message = None
        self.text_channel = text_channel
        self.voice_channel = voice_channel
        self.privacy_level = PrivacyLevel.guild_only
        self.participants = participants
        self.image_url = image_url
        self.buttons = []
        self.ready_to_create = False
        self.created = False
        self.started = False
        self.scheduled_event: ScheduledEvent = None
        self.changed = False
        self.start_time = start_time
        self.unavailable = False

    def compare_availabilities(self):
        pass

    def shares_participants(self, event):
        for self_participant in self.participants:
            for other_participant in event.participants:
                if self_participant.member == other_participant.member:
                    return True
        return False

    def has_everyone_answered(self):
        for participant in self.participants:
            if participant.subscribed and not participant.answered:
                return False
        return True

    async def request_availability(self, interaction: Interaction, duration: int = 30, reschedule: bool = False):
        view = AvailabilityButtons(event=self)
        if not reschedule:
            await interaction.response.send_message(f'**Event name:** {self.name}'
                    f'\n**Duration:** {duration} minutes'
                    f'\n\nSelect **Respond** to enter your availability!'
                    f'\n**Full** will mark you as available at any time.'
                    f'\n**None** will cancel scheduling.'
                    f'\n**Unsubscribe** will allow the event to occur without you; however, you can still respond and participate.'
                    f'\n\nThe event will be either created or cancelled 1-2 minutes after the last person responds.️', view=view)
            log_info(f'{self.name}> Sent availability request message')
        else:
            await interaction.response.send_message(f'**Event name:** {self.name}'
                    f'\n**Duration:** {duration} minutes'
                    f'\n\nPlease select **Respond** to enter your new availability.'
                    f'\n**Full** will mark you as available at any time.'
                    f'\n**None** will cancel scheduling.'
                    f'\n**Unsubscribe** will allow the event to occur without you; however, you can still respond and participate.', view=view)
            log_info(f'{self.name}> Requested availability of {participant.name} for reschedule')

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

    def nudge_timer(self):
        self.nudge_unresponded_timer -= 1
        if self.nudge_unresponded_timer == 0:
            self.nudge_unresponded_timer = 30
            return True
        return False

    async def nudge_unresponded_participants(self):
        if not self.nudge_timer() or self.created or self.has_everyone_answered():
            return
        for participant in self.participants:
            if not participant.answered:
                await participant.member.send(random.choice(self.nudges))
                log_info(f'{self.name}> Nudged {participant.member.name}')

class AvailabilityModal(Modal):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_item(TextInput(label='Timeslot 1', placeholder='8-11, 1pm-3pm: Avail. 0800-1100, 1300-1500'))
        self.add_item(TextInput(label='Timeslot 2', placeholder='15:30-17: Avail. 1530-1700', required=False))
        self.add_item(TextInput(label='Timeslot 3', placeholder='-2030: Avail. until 2030', required=False))
        self.add_item(TextInput(label='Timeslot 4', placeholder='22-: Avail. after 2200', required=False))
        self.add_item(TextInput(label='Timezone', placeholder='ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|PT|PST|PDT', required=False, default='ET'))

    async def on_submit(self, interaction: Interaction):
        log_info(f'Received availability from {interaction.user.name}')
        await interaction.response.send_message(f'Availability received!', ephemeral=True)

    async def on_error(self, interaction: Interaction, error: Exception):
        log_error(f'Error getting availability from {interaction.user.name}: {error}')
        await interaction.response.send_message(f'Oops! Something went wrong: {error}', ephemeral=True)
        traceback.print_exception(type(error), error, error.__traceback__)

class AvailabilityButtons(View):
    def __init__(self, event: Event):
        super().__init__(timeout=None)
        self.respond_label = "Respond"
        self.all_label = "All"
        self.none_label = "None"
        self.unsub_label = "Unsubscribe"
        self.event = event
        self.availability_modal = AvailabilityModal(title='Availability')
        self.add_respond_button()
        self.add_all_button()
        self.add_none_button()
        self.add_unsub_button()

    def add_respond_button(self):
        button = Button(label=self.respond_label, style=ButtonStyle.blurple)
        async def respond_button_callback(interaction: Interaction):
            await interaction.response.send_modal(self.availability_modal)
        button.callback=respond_button_callback
        self.add_item(button)

    def add_all_button(self):
        button = Button(label=self.all_label, style=ButtonStyle.blurple)
        async def all_button_callback(interaction: Interaction):
            self.event.changed = True
            self.event.ready_to_create = False
            if button.style == ButtonStyle.blurple:
                button.style = ButtonStyle.green
                # TODO: set all availability to true
                log_info(f'{self.event.name}> {interaction.author.name} selected full availability')
            else:
                button.style = ButtonStyle.blurple
                # TODO: set all availability to false
                log_info(f'{self.event.name}> {interaction.author.name} deselected full availability')
            try:
                await interaction.response.edit_message(view=self)
                await self.event.update_message()
            except Exception as e:
                log_error(f'{self.event.name}> Error with ALL button press by {interaction.author.name}: {e}')
        button.callback = all_button_callback
        self.add_item(button)

    def add_none_button(self):
        button = Button(label=self.none_label, style=ButtonStyle.blurple)
        async def none_button_callback(interaction: Interaction):
            self.event.changed = True
            self.event.ready_to_create = False
            if button.style == ButtonStyle.blurple:
                button.style = ButtonStyle.gray
                # TODO: set all availability to false, cancel event
                self.event.reason += f'{interaction.author.name} has no availability. '
                self.event.unavailable = True
                log_info(f'{self.event.name}> {interaction.author.name} selected no availability')
            else:
                button.style = ButtonStyle.blurple
                self.event.reason.replace(f'{interaction.author.name} has no availability. ', '')
                self.event.unavailable = False
                log_info(f'{self.event.name}> {interaction.author.name} deselected no availability')
            try:
                await interaction.response.edit_message(view=self)
                await self.event.update_message()
            except Exception as e:
                log_error(f'{self.event.name}> Error with NONE button press by {interaction.author.name}: {e}')
        button.callback = none_button_callback
        self.add_item(button)

    def add_unsub_button(self):
        button = Button(label=self.unsub_label, style=ButtonStyle.blurple)
        async def unsub_button_callback(interaction: Interaction):
            self.event.changed = True
            self.event.ready_to_create = False
            # TODO: self.participant.subscribed = not self.participant.subscribed
            if button.style == ButtonStyle.blurple:
                button.style = ButtonStyle.gray
                log_info(f'{self.event.name}> {interaction.author.name} unsubscribed')
            else:
                button.style = ButtonStyle.blurple
                log_info(f'{self.event.name}> {interaction.author.name} resubscribed')
            try:
                await interaction.response.edit_message(view=self)
                await self.event.update_message()
            except Exception as e:
                log_error(f'{self.event.name}> Error with UNSUB button press by {interaction.author.name}: {e}')
        button.callback = unsub_button_callback
        self.add_item(button)

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
            client.scheduled_events.remove(self.event.scheduled_event)
            await self.event.scheduled_event.delete(reason='End button pressed.')
            self.event.created = False
            client.events.remove(event)
            del event
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
            client.scheduled_events.remove(self.event.scheduled_event)
            await self.event.scheduled_event.delete(reason='Reschedule button pressed.')
            self.event.created = False
            self.start_button.disabled = True
            self.start_button.style = ButtonStyle.blurple
            self.end_button.disabled = True
            self.end_button.style = ButtonStyle.blurple
            self.reschedule_button.disabled = True
            self.cancel_button.disabled = True
            await interaction.response.edit_message(view=self)
            client.events.remove(event)
            del event
            client.events.append(new_event)
            mentions = ''
            for participant in self.event.participants:
                if participant.member != interaction.user:
                    mentions += participant.member.mention
            try:
                await self.event.text_channel.send(f'{mentions}\n{interaction.user.mention} is rescheduling {new_event.name}.')
            except Exception as e:
                log_error(f'Error sending RESCHEDULE button text channel message: {e}')
            try:
                await new_event.request_availability(interaction, self.event.duration, reschedule=True)
            except Exception as e:
                log_error(f'Error with RESCHEDULE button requesting availability: {e}')
        self.reschedule_button.callback = reschedule_button_callback
        self.add_item(self.reschedule_button)

    def add_cancel_button(self):
        async def cancel_button_callback(interaction: Interaction):
            self.event.text_channel = interaction.channel
            if self.event.created:
                client.scheduled_events.remove(self.event.scheduled_event)
                await self.event.scheduled_event.delete(reason='Cancel button pressed.')
                self.event.created = False
            await self.event.remove()
            client.events.remove(event)
            del event
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


def main():
    class SchedulerClient(Client):
        def __init__(self, intents):
            super(SchedulerClient, self).__init__(intents=intents)
            self.tree = app_commands.CommandTree(self)
            self.events = []

        async def make_scheduled_event(self, event):
            event.scheduled_event = await event.guild.create_scheduled_event(name=event.name, description='Bot-generated event', start_time=event.start_time, end_time=event.end_time, entity_type=event.entity_type, channel=event.voice_channel, privacy_level=event.privacy_level)
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
                            await message.channel.send(f'Failed to add your image to {event.name}.\nError: {e}')
                            log_warn(f'{event.name}> Error adding image from {message.author.name}: {e}')
                    else:
                        event.image_url = message.attachments[0].url
                        await message.channel.send(f'Attached image url to event object. Will try setting it when I make the event.')
                    return
            await message.channel.send(f'Could not find event {msg_content}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}')
            return

        # Availability
        if message.content:
            found_event = None
            # Get event from reply
            if message.reference:
                bot_message = await message.channel.fetch_message(message.reference.message_id)
                breakout = False
                for event in client.events:
                    for participant in event.participants:
                        if participant.availability_message.id == bot_message.id:
                            found_event = event
                            breakout = True
                            break
                    if breakout:
                        break
            # Get event from message content
            if not found_event:
                for event in client.events:
                    if event.name in message.content:
                        message.content.replace(event.name, '')
                        found_event = event
                        break

            if not found_event:
                await message.channel.send(f'Could not find event.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}')
                return

            times = message.content.split(',')
            log_debug(f'times: {times}')
            for time in times:
                time = time.replace(' ', '')
                time = time.replace(':', '')
                time = time.replace(';', '')
                start_time, part, end_time = time.partition('-')
                log_debug(f'time: {time}')
                log_debug(f'start_time: {start_time}')
                log_debug(f'end_time: {end_time}')
                if int(start_time) < 10 and len(start_time) == 1:
                    start_time = '0' + start_time
                if int(start_time) < 24:
                    start_time = start_time + '00'
                if int(end_time) < 10 and len(end_time) == 1:
                    end_time = '0' + end_time
                if int(end_time) < 24:
                    end_time = end_time + '00'
                log_debug(f'POST 0 ADDITION:')
                log_debug(f'start_time: {start_time}')
                log_debug(f'end_time: {end_time}')
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

    @client.tree.command(name='reschedule', description='Reschedule an existing scheduled event.')
    @app_commands.describe(event_name='Name of the event to reschedule.')
    @app_commands.describe(image_url='URL to an image for the event.')
    @app_commands.describe(duration='Event duration in minutes (default 30 minutes).')
    async def reschedule_command(interaction: Interaction, event_name: str, image_url: str = None, duration: int = 30):
        event_name = event_name.lower()
        for event in client.events.copy():
            if event_name == event.name.lower():
                log_info(f'{event.name}> {interaction.user.name} requested reschedule')
                if event.created:
                    new_event = Event(event.name, event.entity_type, event.voice_channel, event.participants, event.guild, interaction.channel, image_url, duration) #, weekly
                    await event.scheduled_event.delete(reason='Reschedule command issued.')
                    client.events.remove(event)
                    del event
                    client.events.append(new_event)
                    await interaction.response.send_message(f'{interaction.user.mention} is rescheduling.')
                    await new_event.request_availability(interaction, duration, reschedule=True)
                else:
                    await interaction.response.send_message(f'{event.name} has not been created yet. Your buttons will work until it is created or cancelled.')
                return
        try:
            await interaction.response.send_message(f'Could not find event {event_name}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}', ephemeral=True)
        except Exception as e:
            log_error(f'Error responding to reschedule command: {e}')

    @client.tree.command(name='cancel', description='Cancel an event.')
    @app_commands.describe(event_name='Name of the event to cancel.')
    async def cancel_command(interaction: Interaction, event_name: str):
        event_name = event_name.lower()
        for event in client.events.copy():
            if event_name == event.name.lower():
                log_info(f'{event.name}> {interaction.user.name} cancelled event')
                if event.created:
                    await event.scheduled_event.delete(reason='Cancel command issued.')
                client.events.remove(event)
                del event
                await interaction.response.send_message(f'{mentions}\n{interaction.user.name} has cancelled {event.name}.')
                mentions = ''
                for participant in event.participants:
                    if participant.member.name != interaction.user.name:
                        async with participant.msg_lock:
                            await participant.member.send(f'{interaction.user.name} has cancelled {event.name}.')
                        mentions += participant.member.mention
                return
        try:
            await interaction.response.send_message(f'Could not find event {event_name}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}', ephemeral=True)
        except Exception as e:
            log_error(f'Error responding to cancel command: {e}')

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
        for event in client.events:
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

                if event.changed or not event.has_everyone_answered():
                    event.changed = False
                    continue

                # TODO: event.check_times()
            except Exception as e:
                log_error(f'{event.name}> Error nudging or sending 5 minute warning: {e}')

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
                    response = f'{mentions}\nHeads up! You are all available for {event.name} starting today at {double_digit_string(event.start_time.hour)}:{double_digit_string(event.start_time.minute)} ET.\n' + unsubbed
                    await event.text_channel.send(content=response, view=EventButtons(event))
                except Exception as e:
                    log_error(f'{event.name}> Error sending event created notification with buttons: {e}')

    client.run(discord_token)


if __name__ == '__main__':
    main()
