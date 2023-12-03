'''Written by Cael Shoop.'''

import os
import random
import discord
import timestamps
import datetime
from time import sleep
from dotenv import load_dotenv
from discord import app_commands, Interaction, Intents, Client, ButtonStyle, VoiceChannel
from discord.ui import View, Button
from discord.ext import tasks

load_dotenv()


def get_time():
    ct = str(datetime.datetime.now())
    hour = int(ct[11:13])
    minute = int(ct[14:16])
    return hour, minute


def get_datetime_from_label(label: str):
    partitioned_time = label.partition(':')
    hour = int(partitioned_time[0])
    minute = int(partitioned_time[2])
    time = datetime.datetime.now().astimezone()
    time = time.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if time.hour < 2 and datetime.datetime.now().astimezone().hour > 7:
        time += datetime.timedelta(days=1)
    return time


def get_log_time():
    time = datetime.datetime.now().astimezone()
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
    return output


def main():
    class Participant():
        class Availability:
            def __init__(self):
                self.thirteen_hundred            = False
                self.thirteen_hundred_thirty     = False
                self.fourteen_hundred            = False
                self.fourteen_hundred_thirty     = False
                self.fifteen_hundred             = False
                self.fifteen_hundred_thirty      = False
                self.sixteen_hundred             = False
                self.sixteen_hundred_thirty      = False
                self.seventeen_hundred           = False
                self.seventeen_hundred_thirty    = False
                self.eighteen_hundred            = False
                self.eighteen_hundred_thirty     = False
                self.nineteen_hundred            = False
                self.nineteen_hundred_thirty     = False
                self.twenty_hundred              = False
                self.twenty_hundred_thirty       = False
                self.twenty_one_hundred          = False
                self.twenty_one_hundred_thirty   = False
                self.twenty_two_hundred          = False
                self.twenty_two_hundred_thirty   = False
                self.twenty_three_hundred        = False
                self.twenty_three_hundred_thirty = False
                self.zero_hundred                = False
                self.zero_hundred_thirty         = False
                self.one_hundred                 = False
                self.one_hundred_thirty          = False

        def __init__(self, member):
            self.member = member
            self.availability = Participant.Availability()
            self.answered = False
            self.subscribed = True
            self.msg_lock = False

        def toggle_availability(self, label):
            if label == timestamps.thirteen_hundred_hours:                 self.availability.thirteen_hundred               = not self.availability.thirteen_hundred
            elif label == timestamps.thirteen_hundred_thirty_hours:        self.availability.thirteen_hundred_thirty        = not self.availability.thirteen_hundred_thirty
            elif label == timestamps.fourteen_hundred_hours:               self.availability.fourteen_hundred               = not self.availability.fourteen_hundred
            elif label == timestamps.fourteen_hundred_thirty_hours:        self.availability.fourteen_hundred_thirty        = not self.availability.fourteen_hundred_thirty
            elif label == timestamps.fifteen_hundred_hours:                self.availability.fifteen_hundred                = not self.availability.fifteen_hundred
            elif label == timestamps.fifteen_hundred_thirty_hours:         self.availability.fifteen_hundred_thirty         = not self.availability.fifteen_hundred_thirty
            elif label == timestamps.sixteen_hundred_hours:                self.availability.sixteen_hundred                = not self.availability.sixteen_hundred
            elif label == timestamps.sixteen_hundred_thirty_hours:         self.availability.sixteen_hundred_thirty         = not self.availability.sixteen_hundred_thirty
            elif label == timestamps.seventeen_hundred_hours:              self.availability.seventeen_hundred              = not self.availability.seventeen_hundred
            elif label == timestamps.seventeen_hundred_thirty_hours:       self.availability.seventeen_hundred_thirty       = not self.availability.seventeen_hundred_thirty
            elif label == timestamps.eighteen_hundred_hours:               self.availability.eighteen_hundred               = not self.availability.eighteen_hundred
            elif label == timestamps.eighteen_hundred_thirty_hours:        self.availability.eighteen_hundred_thirty        = not self.availability.eighteen_hundred_thirty
            elif label == timestamps.nineteen_hundred_hours:               self.availability.nineteen_hundred               = not self.availability.nineteen_hundred
            elif label == timestamps.nineteen_hundred_thirty_hours:        self.availability.nineteen_hundred_thirty        = not self.availability.nineteen_hundred_thirty
            elif label == timestamps.twenty_hundred_hours:                 self.availability.twenty_hundred                 = not self.availability.twenty_hundred
            elif label == timestamps.twenty_hundred_thirty_hours:          self.availability.twenty_hundred_thirty          = not self.availability.twenty_hundred_thirty
            elif label == timestamps.twenty_one_hundred_hours:             self.availability.twenty_one_hundred             = not self.availability.twenty_one_hundred
            elif label == timestamps.twenty_one_hundred_thirty_hours:      self.availability.twenty_one_hundred_thirty      = not self.availability.twenty_one_hundred_thirty
            elif label == timestamps.twenty_two_hundred_hours:             self.availability.twenty_two_hundred             = not self.availability.twenty_two_hundred
            elif label == timestamps.twenty_two_hundred_thirty_hours:      self.availability.twenty_two_hundred_thirty      = not self.availability.twenty_two_hundred_thirty
            elif label == timestamps.twenty_three_hundred_hours:           self.availability.twenty_three_hundred           = not self.availability.twenty_three_hundred
            elif label == timestamps.twenty_three_hundred_thirty_hours:    self.availability.twenty_three_hundred_thirty    = not self.availability.twenty_three_hundred_thirty
            elif label == timestamps.zero_hundred_hours:                   self.availability.zero_hundred                   = not self.availability.zero_hundred
            elif label == timestamps.zero_hundred_thirty_hours:            self.availability.zero_hundred_thirty            = not self.availability.zero_hundred_thirty
            elif label == timestamps.one_hundred_hours:                    self.availability.one_hundred                    = not self.availability.one_hundred
            elif label == timestamps.one_hundred_thirty_hours:             self.availability.one_hundred_thirty             = not self.availability.one_hundred_thirty

        def is_available(self, label):
            if label == timestamps.thirteen_hundred_hours:                 return self.availability.thirteen_hundred
            elif label == timestamps.thirteen_hundred_thirty_hours:        return self.availability.thirteen_hundred_thirty
            elif label == timestamps.fourteen_hundred_hours:               return self.availability.fourteen_hundred
            elif label == timestamps.fourteen_hundred_thirty_hours:        return self.availability.fourteen_hundred_thirty
            elif label == timestamps.fifteen_hundred_hours:                return self.availability.fifteen_hundred
            elif label == timestamps.fifteen_hundred_thirty_hours:         return self.availability.fifteen_hundred_thirty
            elif label == timestamps.sixteen_hundred_hours:                return self.availability.sixteen_hundred
            elif label == timestamps.sixteen_hundred_thirty_hours:         return self.availability.sixteen_hundred_thirty
            elif label == timestamps.seventeen_hundred_hours:              return self.availability.seventeen_hundred
            elif label == timestamps.seventeen_hundred_thirty_hours:       return self.availability.seventeen_hundred_thirty
            elif label == timestamps.eighteen_hundred_hours:               return self.availability.eighteen_hundred
            elif label == timestamps.eighteen_hundred_thirty_hours:        return self.availability.eighteen_hundred_thirty
            elif label == timestamps.nineteen_hundred_hours:               return self.availability.nineteen_hundred
            elif label == timestamps.nineteen_hundred_thirty_hours:        return self.availability.nineteen_hundred_thirty
            elif label == timestamps.twenty_hundred_hours:                 return self.availability.twenty_hundred
            elif label == timestamps.twenty_hundred_thirty_hours:          return self.availability.twenty_hundred_thirty
            elif label == timestamps.twenty_one_hundred_hours:             return self.availability.twenty_one_hundred
            elif label == timestamps.twenty_one_hundred_thirty_hours:      return self.availability.twenty_one_hundred_thirty
            elif label == timestamps.twenty_two_hundred_hours:             return self.availability.twenty_two_hundred
            elif label == timestamps.twenty_two_hundred_thirty_hours:      return self.availability.twenty_two_hundred_thirty
            elif label == timestamps.twenty_three_hundred_hours:           return self.availability.twenty_three_hundred
            elif label == timestamps.twenty_three_hundred_thirty_hours:    return self.availability.twenty_three_hundred_thirty
            elif label == timestamps.zero_hundred_hours:                   return self.availability.zero_hundred
            elif label == timestamps.zero_hundred_thirty_hours:            return self.availability.zero_hundred_thirty
            elif label == timestamps.one_hundred_hours:                    return self.availability.one_hundred
            elif label == timestamps.one_hundred_thirty_hours:             return self.availability.one_hundred_thirty


    class Event:
        def __init__(self, name: str, voice_channel: VoiceChannel, participants: list, interaction: Interaction):
            self.name = name
            self.guild = interaction.guild
            self.text_channel = interaction.channel_id
            self.voice_channel = voice_channel
            self.participants = participants
            self.interaction = interaction
            self.buttons = []
            self.reason = ''
            self.nudges = ['respond', 'I showed you my event, pls respond', 'I\'m waiting for you', 'my brother in christ, click button(s)', 'your availability. hand it over', 'nudge', 'plz respond 🥺', 'I\'m literally crying rn omg, I need your availability', 'click button(s)', 'HURRY HURRY HURRY!', 'I want to create event: you sleep', 'I **NEED** AVAILABILITY!']
            self.nudge_unresponded_timer = 30
            self.ready_to_create = False
            self.created = False
            self.changed = False
            self.start_time = None
            self.end_time = None
            self.valid = True

        def check_times(self):
            # Find first available shared time block and configure start/end times
            print(f'{get_log_time()}> {self.name}> Comparing availabilities for {self.name}')
            shared_time_slot = ''
            for time_slot in timestamps.all_timestamps:
                check_time_obj = get_datetime_from_label(time_slot)
                if datetime.datetime.now().astimezone() > check_time_obj - datetime.timedelta(minutes=5):
                    continue
                skip_time_slot = False
                for event in client.events:
                    if self != event and check_time_obj == event.start_time and (self.shares_participants(event) or self.voice_channel == event.voice_channel):
                        print(f'{get_log_time()}> {self.name}> Skipping {time_slot} due to event {event.name} already existing at that time with shared participant(s) or shared location')
                        skip_time_slot = True
                        break
                if not skip_time_slot:
                    shared_availability = True
                    for participant in self.participants:
                        shared_availability = shared_availability and participant.is_available(time_slot)
                    if shared_availability:
                        shared_time_slot = time_slot
                        break
            if shared_time_slot == '':
                print(f'{get_log_time()}> {self.name}> Unable to find common availability')
                self.valid = False
                return
            self.start_time = get_datetime_from_label(shared_time_slot)
            self.end_time = self.start_time + datetime.timedelta(minutes=30)
            print(f'{get_log_time()}> {self.name}> Ready to create event on {self.start_time.month}/{self.start_time.day}/{self.start_time.year} at {self.start_time.hour}:{self.start_time.minute}')
            self.ready_to_create = True

        def shares_participants(self, event):
            for self_participant in self.participants:
                for other_participant in event.participants:
                    if self_participant.member == other_participant.member:
                        return True
            return False

        def has_everyone_answered(self):
            everyoneAnswered = True
            for participant in self.participants:
                if participant.subscribed:
                    everyoneAnswered = everyoneAnswered and participant.answered
            return everyoneAnswered

        def nudge_timer(self):
            self.nudge_unresponded_timer -= 1
            if self.nudge_unresponded_timer == 0:
                self.nudge_unresponded_timer = 30
                return True
            return False

        async def nudge_unresponded_participants(self):
            for participant in self.participants:
                if not participant.answered:
                    await participant.member.send(random.choice(self.nudges))
                    print(f'{get_log_time()}> {self.name}> Nudged {participant.member.name}')


    class TimeButton(View):
        def __init__(self, label: str, participant: Participant, event: Event):
            super().__init__(timeout=None)
            self.label = label
            self.participant = participant
            self.event = event
            self.add_button()

        def add_button(self):
            button = Button(label=self.label + ' EST', style=ButtonStyle.red)
            async def button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                self.participant.answered = True
                self.participant.toggle_availability(self.label)
                if self.participant.is_available(self.label):
                    button.style = ButtonStyle.green
                else:
                    button.style = ButtonStyle.red
                await interaction.response.edit_message(view=self)
                print(f'{get_log_time()}> {self.event.name}> {self.participant.member.name} toggled availability to {self.participant.is_available(self.label)} at {self.label}')

            button.callback = button_callback
            self.add_item(button)


    class OtherButtons(View):
        def __init__(self, participant: Participant, event: Event):
            super().__init__(timeout=None)
            self.all_label = "All"
            self.none_label = "None"
            self.unsub_label = "Unsubscribe"
            self.participant = participant
            self.event = event
            self.add_all_button()
            self.add_none_button()
            self.add_unsub_button()

        def add_all_button(self):
            button = Button(label=self.all_label, style=ButtonStyle.blurple)
            async def all_button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                self.participant.answered = True
                if button.style == ButtonStyle.blurple:
                    button.style = ButtonStyle.green
                    for time_slot in timestamps.all_timestamps:
                        if not self.participant.is_available(time_slot):
                            self.participant.toggle_availability(time_slot)
                    print(f'{get_log_time()}> {self.event.name}> {self.participant.member.name} selected full availability')
                else:
                    button.style = ButtonStyle.blurple
                    print(f'{get_log_time()}> {self.event.name}> {self.participant.member.name} deselected full availability')
                await interaction.response.edit_message(view=self)

            button.callback = all_button_callback
            self.add_item(button)

        def add_none_button(self):
            button = Button(label=self.none_label, style=ButtonStyle.blurple)
            async def none_button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                self.participant.answered = True
                if button.style == ButtonStyle.blurple:
                    button.style = ButtonStyle.gray
                    for time_slot in timestamps.all_timestamps:
                        if self.participant.toggle_availability(time_slot):
                            self.participant.toggle_availability(time_slot)
                    self.event.reason += f'{self.participant.member.name} has no availability. '
                    self.event.valid = False
                    print(f'{get_log_time()}> {self.event.name}> {self.participant.member.name} selected no availability')
                else:
                    button.style = ButtonStyle.blurple
                    self.event.reason.replace(f'{self.participant.member.name} has no availability. ', '')
                    self.event.valid = True
                    print(f'{get_log_time()}> {self.event.name}> {self.participant.member.name} deselected no availability')
                await interaction.response.edit_message(view=self)

            button.callback = none_button_callback
            self.add_item(button)

        def add_unsub_button(self):
            button = Button(label=self.unsub_label, style=ButtonStyle.blurple)
            async def unsub_button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                self.participant.answered = True
                self.participant.subscribed = not self.participant.subscribed
                if button.style == ButtonStyle.blurple:
                    button.style = ButtonStyle.gray
                    print(f'{get_log_time()}> {self.event.name}> {self.participant.member.name} unsubscribed')
                else:
                    button.style = ButtonStyle.blurple
                    print(f'{get_log_time()}> {self.event.name}> {self.participant.member.name} resubscribed')
                await interaction.response.edit_message(view=self)

            button.callback = unsub_button_callback
            self.add_item(button)


    class SchedulerClient(Client):
        FILENAME = 'info.json'

        def __init__(self, intents):
            super(SchedulerClient, self).__init__(intents=intents)
            self.tree = app_commands.CommandTree(self)
            self.events = []

        async def setup_hook(self):
            await self.tree.sync()


    discord_token = os.getenv('DISCORD_TOKEN')
    client = SchedulerClient(intents=Intents.all())

    @client.event
    async def on_ready():
        if not create_guild_event.is_running():
            create_guild_event.start()
        print(f'{get_log_time()}> {client.user} has connected to Discord!')

    @client.tree.command(name='schedule', description='Create a scheduling event.')
    @app_commands.describe(event_name='Name for the event.')
    @app_commands.describe(voice_channel="Voice channel for the event.")
    async def schedule_command(interaction: Interaction, event_name: str, voice_channel: discord.VoiceChannel):
        # Put participants into a list
        participants = []
        print(f'{get_log_time()}> {event_name}> Received event request')
        for member in interaction.channel.members:
            if not member.bot:
                participant = Participant(member)
                participants.append(participant)

        curHour, curMinute = get_time()

        if curHour == 1 and curMinute >= 30 and curHour < 7:
            await interaction.response.send_message(f'It\'s late, you should go to bed. Try again later today.')
            return

        curTimeObj = datetime.datetime(2000, 1, 1, curHour, curMinute)
        if curHour < 2:
            curTimeObj += datetime.timedelta(days=1)

        # Make event object
        event = Event(event_name, voice_channel, participants, interaction)
        client.events.append(event)
        await interaction.response.send_message(f'{interaction.user.mention} wants to create an event called {event.name}. Check your DMs to share your availability!')

        for participant in event.participants:
            buttonFlag = False
            while (participant.msg_lock):
                sleep(1)
            participant.msg_lock = True
            await participant.member.send(f'⬇️⬇️⬇️⬇️⬇️ __**{event.name}**__ ⬇️⬇️⬇️⬇️⬇️\n**Loading buttons, please wait.**\n{interaction.user.name} wants to create an event called {event.name}.')
            print(f'{get_log_time()}> {event_name}> Sending buttons to {participant.member.name}')

            for button_label in timestamps.all_timestamps:
                labelTime = button_label.partition(':')
                labelHour = int(labelTime[0])
                labelMinute = int(labelTime[2])

                if not buttonFlag:
                    labelTimeObj = datetime.datetime(2000, 1, 1, labelHour, labelMinute)
                    if labelHour < 2:
                        labelTimeObj += datetime.timedelta(days=1)
                    buttonFlag = curTimeObj + datetime.timedelta(minutes=5) < labelTimeObj

                if buttonFlag:
                    await participant.member.send(view=TimeButton(label=button_label, participant=participant, event=event))

            await participant.member.send(view=OtherButtons(participant=participant, event=event))
            await participant.member.send(f'Select **all** of the 30 minute blocks you could be available to attend {event.name}!\n"None" will stop the event from being created, so click "Unsubscribe" if you want the event to occur with or without you.\n'
                                          f'The event will be either created or cancelled 1-2 minutes after the last person responds, which renders the buttons useless.\n'f'⬆️⬆️⬆️⬆️⬆️ __**{event.name}**__ ⬆️⬆️⬆️⬆️⬆️')
            participant.msg_lock = False
        print(f'{get_log_time()}> {event_name}> Done DMing participants')


    @tasks.loop(minutes=1)
    async def create_guild_event():
        for event in client.events.copy():
            if not event.valid:
                channel = client.get_channel(int(event.interaction.channel_id))
                await channel.send('No shared availability has been found. The event scheduling has been cancelled. ' + event.reason)
                client.events.remove(event)
                print(f'{get_log_time()}> {event.name}> Event invalid, removed event from memory')
            elif event.created and get_datetime_from_label('01:30') <= datetime.datetime.now().astimezone():
                client.events.remove(event)
                print(f'{get_log_time()}> {event.name}> last time slot passed, removed event from memory')


        for event in client.events:
            everyoneAnswered = event.has_everyone_answered()

            if not everyoneAnswered and event.nudge_timer():
                await event.nudge_unresponded_participants()

            if event.created:
                continue
            if event.changed or not everyoneAnswered:
                event.changed = False
                continue

            event.check_times()

            if event.ready_to_create:
                privacy_level = discord.PrivacyLevel.guild_only
                await event.guild.create_scheduled_event(name=event.name, description='Bot generated event based on participant availabilities provided', start_time=event.start_time, end_time=event.end_time, channel=event.voice_channel, privacy_level=privacy_level)
                print(f'{get_log_time()}> {event.name}> Created event starting at {event.start_time.hour}:{event.start_time.minute} and ending at {event.end_time.hour}:{event.end_time.minute}')
                event.ready_to_create = False
                event.created = True

                mentions = ''
                unsubbed = ''
                for participant in event.participants:
                    if participant.subscribed:
                        mentions += f'{participant.member.mention} '
                    else:
                        unsubbed += f'{participant.member.name} '
                if unsubbed != '':
                    unsubbed = 'Unsubscribed: ' + unsubbed
                channel = client.get_channel(int(event.interaction.channel_id))
                if event.start_time.hour < 10 and event.start_time.minute < 10:
                    await channel.send(f'{mentions}\nHeads up! You are all available for {event.name} starting at 0{event.start_time.hour}:0{event.start_time.minute}.\n' + unsubbed)
                elif event.start_time.hour < 10 and event.start_time.minute >= 10:
                    await channel.send(f'{mentions}\nHeads up! You are all available for {event.name} starting at 0{event.start_time.hour}:{event.start_time.minute}.\n' + unsubbed)
                elif event.start_time.hour >= 10 and event.start_time.minute < 10:
                    await channel.send(f'{mentions}\nHeads up! You are all available for {event.name} starting at {event.start_time.hour}:0{event.start_time.minute}.\n' + unsubbed)
                else:
                    await channel.send(f'{mentions}\nHeads up! You are all available for {event.name} starting at {event.start_time.hour}:{event.start_time.minute}.\n' + unsubbed)

    client.run(discord_token)


if __name__ == '__main__':
    main()
