'''Written by Cael Shoop.'''

import os
import random
import requests
from event import Event
from participant import Participant
from typing import Literal
from dotenv import load_dotenv
from datetime import datetime, timedelta
from discord import app_commands, Interaction, Intents, Client, ButtonStyle, EventStatus, EntityType, TextChannel, VoiceChannel, Message, ScheduledEvent, Guild, PrivacyLevel, User, utils, File
from discord.ui import View, Button
from discord.ext import tasks

load_dotenv()

INCLUDE = 'INCLUDE'
EXCLUDE = 'EXCLUDE'
INCLUDE_EXCLUDE: Literal = Literal[INCLUDE, EXCLUDE]

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

def get_log_time():
    time = datetime.now().astimezone()
    output = ''
    if time.hour < 10:
        output += '0'
    output += f'{time.hour}:'
    if time.minute < 10:
        output += '0'
    output += f'{time.minute}:'
    if time.second < 10:
        output += '0'
    output += f'{time.second}'
    return output + '>'

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
                        print(f'{get_log_time()} {event.name}> Processed image')
                    else:
                        event.image_url = ''
                        print(f'{get_log_time()} {event.name}> Failed to get image')
                except Exception as e:
                    event.image_url = ''
                    print(f'{get_log_time()} {event.name}> Failed to process image: {e}')
            event.ready_to_create = False
            event.created = True
            print(f'{get_log_time()} {event.name}> Created event starting at {event.start_time.hour}:{event.start_time.minute} ET')
            return event.scheduled_event

        async def setup_hook(self):
            await self.tree.sync()


    discord_token = os.getenv('DISCORD_TOKEN')
    client = SchedulerClient(intents=Intents.all())

    @client.event
    async def on_ready():
        print(f'{get_log_time()} {client.user} has connected to Discord!')
        if not update.is_running():
            update.start()

    @client.event
    async def on_message(message):
        if message.author.bot or message.guild or not message.attachments or not message.content:
            return

        msg_content = message.content.lower()
        for event in client.events:
            if event.name.lower() in msg_content:
                if event.created:
                    try:
                        image_bytes = await message.attachments[0].read()
                        await event.scheduled_event.edit(image=image_bytes)
                        await message.channel.send(f'Added your image to {event.name}.')
                        print(f'{get_log_time()} {event.name}> {message.author.name} added an image')
                    except Exception as e:
                        await message.channel.send(f'Failed to add your image to {event.name}.\nError: {e}')
                        print(f'{get_log_time()} {event.name}> Error adding image from {message.author.name}: {e}')
                else:
                    event.image_url = message.attachments[0].url
                    await message.channel.send(f'Attached image url to event object. Will try setting it when I make the event.')
                return
        await message.channel.send(f'Could not find event {msg_content}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}')

    @client.tree.command(name='create', description='Create an event.')
    @app_commands.describe(event_name='Name for the event.')
    @app_commands.describe(voice_channel='Voice channel for the event.')
    @app_commands.describe(start_time='Start time for the event.')
    @app_commands.describe(image_url='URL to an image for the event.')
    @app_commands.describe(include_exclude='Whether to include or exclude users with the designated role.')
    @app_commands.describe(role='Only include/exclude users with this role as participants.')
    @app_commands.describe(duration='Event duration in minutes (30 minutes default).')
    async def create_command(interaction: Interaction, event_name: str, voice_channel: VoiceChannel, start_time: str, image_url: str = None, include_exclude: INCLUDE_EXCLUDE = INCLUDE, role: str = None, duration: int = 30):
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

        print(f'{get_log_time()} {event_name}> Received event creation request from {interaction.user.name}')

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
            print(f'{get_log_time()} Error sending interaction response to create event command: {e}')

    @client.tree.command(name='schedule', description='Create a scheduling event.')
    @app_commands.describe(event_name='Name for the event.')
    @app_commands.describe(voice_channel='Voice channel for the event.')
    @app_commands.describe(image_url="URL to an image for the event.")
    @app_commands.describe(include_exclude='Whether to include or exclude users with the designated role.')
    @app_commands.describe(role='Only include/exclude users with this role as participants.')
    @app_commands.describe(duration="Event duration in minutes (30 minutes default).")
    async def schedule_command(interaction: Interaction, event_name: str, voice_channel: VoiceChannel, image_url: str = None, include_exclude: INCLUDE_EXCLUDE = INCLUDE, role: str = None, duration: int = 30):
        print(f'{get_log_time()} {event_name}> Received event schedule request from {interaction.user.name}')
        participants = get_participants_from_interaction(interaction, include_exclude, role)

        # Make event object
        event = Event(event_name, EntityType.voice, voice_channel, participants, interaction.guild, interaction.channel, image_url, duration)
        event.og_message_text = f'{interaction.user.name}' + event.og_message_text
        mentions = ''
        for participant in event.participants:
            mentions += f'{participant.mention} '
        mentions = '\nWaiting for a response from these participants:\n' + mentions
        client.events.append(event)
        try:
            await interaction.response.send_message(f'{event.og_message_text}')
        except Exception as e:
            print(f'{get_log_time()} Error sending schedule command response: {e}')
        try:
            event.responded_message = await interaction.channel.send(f'{mentions}')
            await event.dm_all_participants(interaction, duration)
        except Exception as e:
            print(f'{get_log_time()} Error DMing all participants or sending responded message: {e}')

    @client.tree.command(name='reschedule', description='Reschedule an existing scheduled event.')
    @app_commands.describe(event_name='Name of the event to reschedule.')
    @app_commands.describe(image_url='URL to an image for the event.')
    @app_commands.describe(duration='Event duration in minutes (default 30 minutes).')
    async def reschedule_command(interaction: Interaction, event_name: str, image_url: str = None, duration: int = 30):
        event_name = event_name.lower()
        for event in client.events:
            if event_name == event.name.lower():
                print(f'{get_log_time()} {event.name}> {interaction.user.name} requested reschedule')
                if event.created:
                    new_event = Event(event.name, event.entity_type, event.voice_channel, event.participants, event.guild, interaction.channel, image_url, duration) #, weekly
                    await event.scheduled_event.delete(reason='Reschedule command issued.')
                    await event.remove()
                    client.events.append(new_event)
                    new_event.og_message_text = f'{interaction.user.name} wants to reschedule {new_event.name}. Check your DMs to share your availability!'
                    mentions = ''
                    for participant in event.participants:
                        mentions += f'{participant.mention} '
                    mentions = '\nWaiting for a response from these participants:\n' + mentions
                    await interaction.response.send_message(f'{new_event.og_message_text}')
                    new_event.responded_message = await interaction.channel.send(f'{mentions}')
                    await new_event.dm_all_participants(interaction, duration, reschedule=True)
                else:
                    await interaction.response.send_message(f'{event.name} has not been created yet. Your buttons will work until it is created or cancelled.')
                return
        try:
            await interaction.response.send_message(f'Could not find event {event_name}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}')
        except Exception as e:
            print(f'{get_log_time()} Error responding to reschedule command: {e}')

    @client.tree.command(name='cancel', description='Cancel an event.')
    @app_commands.describe(event_name='Name of the event to cancel.')
    async def cancel_command(interaction: Interaction, event_name: str):
        event_name = event_name.lower()
        for event in client.events:
            if event_name == event.name.lower():
                print(f'{get_log_time()} {event.name}> {interaction.user.name} cancelled event')
                if event.created:
                    await event.scheduled_event.delete(reason='Cancel command issued.')
                await event.remove()
                mentions = ''
                for participant in event.participants:
                    if participant.name != interaction.user.name:
                        async with participant.msg_lock:
                            await participant.send(f'{interaction.user.name} has cancelled {event.name}.')
                        mentions += participant.mention
                await interaction.response.send_message(f'{mentions}\n{interaction.user.name} has cancelled {event.name}.')
                return
        try:
            await interaction.response.send_message(f'Could not find event {event_name}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}')
        except Exception as e:
            print(f'{get_log_time()} Error responding to cancel command: {e}')

    @client.tree.command(name='bind', description='Bind a text channel to an existing event.')
    @app_commands.describe(event_name='Name of the vent to set this text channel for.')
    async def bind_command(interaction: Interaction, event_name: str):
        event_name = event_name.lower()
        for event in client.events:
            if event_name == event.name.lower():
                try:
                    event.text_channel = interaction.channel
                    await interaction.response.send_message(f'Bound this text channel to {event.name}.')
                except Exception as e:
                    print(f'{get_log_time()} Error binding text channel or responding to bind command: {e}')
                return
        try:
            await interaction.response.send_message(f'Could not find event {event_name}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}')
        except Exception as e:
            print(f'{get_log_time()} Error responding to bind command: {e}')

    @tasks.loop(minutes=1)
    async def update():
        for event in client.events.copy():
            if event.unavailable:
                try:
                    unavailable_mentions = ''
                    unavailable_counter = 0
                    for participant in event.participants:
                        async with participant.msg_lock:
                            await participant.send(f'**!!!!! __Scheduling for {event.name} has been cancelled!__ !!!!!**')
                        if participant.unavailable:
                            unavailable_mentions += f'{participant.mention} '
                            unavailable_counter += 1
                    notification_message = f'Scheduling for {event.name} has been cancelled.\n'
                    notification_message += unavailable_mentions
                    if unavailable_counter == 1:
                        notification_message += 'is unavailable.'
                    else:
                        notification_message += 'are unavailable.'
                    await event.text_channel.send(notification_message)
                    await event.remove()
                    print(f'{get_log_time()} {event.name}> Participants lacked common availability, removed event from memory')
                except Exception as e:
                    print(f'{get_log_time()} Error invalidating and deleting event: {e}')

        curTime = datetime.now().astimezone().replace(second=0, microsecond=0)
        for event in client.events:
            try:
                if event.created:
                    if curTime + timedelta(minutes=5) == event.start_time and event.scheduled_event.status == EventStatus.scheduled and not event.started:
                        if event.text_channel:
                            try:
                                await event.text_channel.send(f'**5 minute warning!** {event.name} is scheduled to start in 5 minutes.')
                            except Exception as e:
                                print(f'{get_log_time()} Error sending 5 minute nudge: {e}')
                        elif not event.text_channel:
                            for participant in event.participants:
                                async with participant.msg_lock:
                                    await participant.send(f'**5 minute warning!** {event.name} is scheduled to start in 5 minutes.')
                    continue

                if event.changed or not event.has_everyone_answered():
                    event.changed = False
                    continue

                event.check_times()
            except Exception as e:
                print(f'{get_log_time()} Error nudging or sending 5 minute warning: {e}')

            if event.ready_to_create:
                try:
                    event.scheduled_event = await client.make_scheduled_event(event)
                except Exception as e:
                    print(f'{get_log_time()} Error creating scheduled event: {e}')
                try:
                    mentions = ''
                    unsubbed = ''
                    for participant in event.participants:
                        if participant.subscribed:
                            mentions += f'{participant.mention} '
                        else:
                            unsubbed += f'{participant.name} '
                    if unsubbed != '':
                        unsubbed = '\nUnsubscribed: ' + unsubbed
                except Exception as e:
                    print(f'{get_log_time()} Error generating mentions/unsubbed strings: {e}')
                try:
                    response = ''
                    if event.start_time.hour < 10 and event.start_time.minute < 10:
                        response = f'{mentions}\nHeads up! You are all available for {event.name} starting today at 0{event.start_time.hour}:0{event.start_time.minute} ET.\n' + unsubbed
                    elif event.start_time.hour >= 10 and event.start_time.minute < 10:
                        response = f'{mentions}\nHeads up! You are all available for {event.name} starting today at {event.start_time.hour}:0{event.start_time.minute} ET.\n' + unsubbed
                    elif event.start_time.hour < 10 and event.start_time.minute >= 10:
                        response = f'{mentions}\nHeads up! You are all available for {event.name} starting today at 0{event.start_time.hour}:{event.start_time.minute} ET.\n' + unsubbed
                    else:
                        response = f'{mentions}\nHeads up! You are all available for {event.name} starting today at {event.start_time.hour}:{event.start_time.minute} ET.\n' + unsubbed
                    await event.text_channel.send(content=response, view=EventButtons(event))
                except Exception as e:
                    print(f'{get_log_time()} Error sending event created notification with buttons: {e}')

    client.run(discord_token)


if __name__ == '__main__':
    main()
