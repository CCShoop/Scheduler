'''Written by Cael Shoop.'''

import os
import random
import requests
import timestamps
from asyncio import Lock
from dotenv import load_dotenv
from datetime import datetime, timedelta
from discord import app_commands, Interaction, Intents, Client, ButtonStyle, EventStatus, EntityType, TextChannel, VoiceChannel, ScheduledEvent, Guild, PrivacyLevel, utils, File
from discord.ui import View, Button
from discord.ext import tasks

load_dotenv()


def get_time():
    ct = str(datetime.now())
    hour = int(ct[11:13])
    minute = int(ct[14:16])
    return hour, minute

def get_datetime_from_label(label: str):
    partitioned_time = label.partition(':')
    hour = int(partitioned_time[0])
    minute = int(partitioned_time[2])
    time = datetime.now().astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0)
    if time.hour < 2 and datetime.now().astimezone().hour > 6:
        time += timedelta(days=1)
    return time

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
            self.weekly = False

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
        def __init__(self, name: str, entity_type: EntityType, voice_channel: VoiceChannel, participants: list, guild: Guild, text_channel: TextChannel, image_url: str, duration: int = 30, start_time: datetime = None): #, weekly: bool
            self.name = name
            self.guild = guild
            self.entity_type = entity_type
            self.text_channel = text_channel
            self.voice_channel = voice_channel
            self.privacy_level = PrivacyLevel.guild_only
            self.participants = participants
            self.image_url = image_url
            #self.requested_weekly = weekly
            self.buttons = []
            self.reason = ''
            self.nudges = ['respond', 'I showed you my event, pls respond', 'I\'m waiting for you', 'my brother in christ, click button(s)', 'your availability. hand it over', 'nudge', 'plz respond 🥺', 'I\'m literally crying rn omg, I need your availability', 'click button(s)', 'HURRY HURRY HURRY!', 'I want to create event: you sleep', 'I **NEED** AVAILABILITY!']
            self.nudge_unresponded_timer = 30
            self.ready_to_create = False
            self.created = False
            self.scheduled_event: ScheduledEvent = None
            self.changed = False
            self.start_time = start_time
            if self.start_time:
                self.end_time = self.start_time + timedelta(minutes=duration)
            else:
                self.end_time = None
            self.duration = duration
            self.valid = True

        def check_times(self):
            # Find first available shared time block and configure start/end times
            print(f'{get_log_time()}> {self.name}> Comparing availabilities for {self.name}')
            shared_time_slot = ''
            if self.valid:
                for time_slot in timestamps.all_timestamps:
                    check_time_obj = get_datetime_from_label(time_slot)
                    if datetime.now().astimezone() > check_time_obj:
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
                            if participant.subscribed:
                                shared_availability = shared_availability and participant.is_available(time_slot)
                        if shared_availability:
                            shared_time_slot = time_slot
                            break
            if shared_time_slot == '':
                print(f'{get_log_time()}> {self.name}> Unable to find common availability')
                self.valid = False
                return
            self.start_time = get_datetime_from_label(shared_time_slot)
            self.end_time = self.start_time + timedelta(minutes=self.duration)
            print(f'{get_log_time()}> {self.name}> Ready to create event on {self.start_time.month}/{self.start_time.day}/{self.start_time.year} at {self.start_time.hour}:{self.start_time.minute}')
            self.ready_to_create = True

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

        async def dm_all_participants(self, interaction: Interaction, duration: int = 30, reschedule: bool = False):
            curHour, curMinute = get_time()
            curTimeObj = datetime(2000, 1, 1, curHour, curMinute).replace(second=0, microsecond=0)
            for participant in self.participants:
                buttonFlag = False
                views = []
                for button_label in timestamps.all_timestamps:
                    labelTime = button_label.partition(':')
                    labelHour = int(labelTime[0])
                    labelMinute = int(labelTime[2])

                    if not buttonFlag:
                        labelTimeObj = datetime(2000, 1, 1, labelHour, labelMinute).replace(second=0, microsecond=0)
                        if labelHour < 2:
                            labelTimeObj += timedelta(days=1)
                        buttonFlag = curTimeObj + timedelta(minutes=5) < labelTimeObj

                    if buttonFlag:
                        views.append(TimeButton(label=button_label, participant=participant, event=self))
                views.append(OtherButtons(participant=participant, event=self))

                print(f'{get_log_time()}> {self.name}> Sending buttons to {participant.member.name}')
                async with client.msg_lock:
                    if reschedule:
                        await participant.member.send(f'⬇️⬇️⬇️⬇️⬇️ __**{self.name}**__ ⬇️⬇️⬇️⬇️⬇️\n**Loading buttons, please wait.**\n{interaction.user.name} wants to **reschedule** {self.name}.\nThe event will last {duration} minutes.')
                    else:
                        await participant.member.send(f'⬇️⬇️⬇️⬇️⬇️ __**{self.name}**__ ⬇️⬇️⬇️⬇️⬇️\n**Loading buttons, please wait.**\n{interaction.user.name} wants to create an event called {self.name}.\nThe event will last {duration} minutes.')
                    for view in views:
                        await participant.member.send(view=view)
                    await participant.member.send(f'Select **all** of the 30 minute blocks you could be available to attend {self.name}!\n"None" will stop the event from being created, so click "Unsubscribe" if you want the event to occur with or without you.\n'
                                                  f'The event will be either created or cancelled 1-2 minutes after the last person responds, which renders the buttons useless.\n'f'⬆️⬆️⬆️⬆️⬆️ __**{self.name}**__ ⬆️⬆️⬆️⬆️⬆️')
            print(f'{get_log_time()}> {self.name}> Done DMing participants')

        def nudge_timer(self):
            self.nudge_unresponded_timer -= 1
            if self.nudge_unresponded_timer == 0:
                self.nudge_unresponded_timer = 30
                return True
            return False

        async def nudge_unresponded_participants(self):
            if not self.nudge_timer() or self.created or self.has_everyone_answered():
                return
            mentions = ''
            for participant in self.participants:
                if not participant.answered:
                    await participant.member.send(random.choice(self.nudges))
                    mentions += f'{participant.member.mention} '
                    print(f'{get_log_time()}> {self.name}> Nudged {participant.member.name}')
            if mentions != '':
                mentions = 'Waiting for a response from these participants:\n' + mentions
                await self.text_channel.send(mentions)

        async def remove(self):
            client.events.remove(self)

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
            self.weekly_label = "Can Attend Weekly"
            self.participant = participant
            self.event = event
            self.add_all_button()
            self.add_none_button()
            self.add_unsub_button()
            # if self.event.requested_weekly:
            #     self.add_weekly_button()

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

        # def add_weekly_button(self):
        #     button = Button(label=self.weekly_label, style=ButtonStyle.gray)
        #     async def weekly_button_callback(interaction: Interaction):
        #         self.event.changed = True
        #         self.event.ready_to_create = False
        #         if button.style == ButtonStyle.gray:
        #             button.style = ButtonStyle.green
        #             self.participant.weekly = True
        #             print(f'{get_log_time()}> {self.event.name}> {self.participant.member.name} can attend weekly')
        #         else:
        #             button.style = ButtonStyle.gray
        #             self.participant.weekly = False
        #             print(f'{get_log_time()}> {self.event.name}> {self.participant.member.name} cannot attend weekly')

        #     button.callback = weekly_button_callback
        #     self.add_item(button)

    class EventButtons(View):
        def __init__(self, event: Event):
            super().__init__(timeout=None)
            self.start_label = "Start"
            self.end_label = "End"
            self.reschedule_label = "Reschedule"
            self.cancel_label = "Cancel"
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
                    await interaction.response.defer()
                    return
                print(f'{get_log_time()}> {self.event.name}> {interaction.user} started by button press')
                await self.event.scheduled_event.start(reason='Start button pressed.')
                self.start_button.style = ButtonStyle.green
                self.start_button.disabled = True
                self.end_button.disabled = False
                await interaction.response.edit_message(view=self)
            self.start_button.callback = start_button_callback
            self.add_item(self.start_button)

        def add_end_button(self):
            self.end_button.disabled = True
            async def end_button_callback(interaction: Interaction):
                self.event.text_channel = interaction.channel
                if self.event.scheduled_event.status != EventStatus.active and self.event.scheduled_event.status != EventStatus.scheduled:
                    await interaction.response.defer()
                    return
                print(f'{get_log_time()}> {self.event.name}> {interaction.user} ended by button press')
                client.scheduled_events.remove(self.event.scheduled_event)
                await self.event.scheduled_event.delete(reason='End button pressed.')
                self.event.created = False
                self.end_button.style = ButtonStyle.gray
                self.end_button.disabled = True
                self.reschedule_button.disabled = True
                self.cancel_button.disabled = True
                await interaction.response.edit_message(view=self)
            self.end_button.callback = end_button_callback
            self.add_item(self.end_button)

        def add_reschedule_button(self):
            async def reschedule_button_callback(interaction: Interaction):
                self.event.text_channel = interaction.channel
                if not self.event.created:
                    await interaction.response.defer()
                    return
                print(f'{get_log_time()}> {self.event.name}> {interaction.user} rescheduled by button press')
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
                await self.event.remove()
                client.events.append(new_event)
                mentions = ''
                for participant in self.event.participants:
                    if participant.member != interaction.user:
                        mentions += participant.member.mention
                await self.event.text_channel.send(f'{mentions}\n{interaction.user.mention} wants to reschedule {new_event.name}. Check your DMs to share your availability!')
                await new_event.dm_all_participants(interaction, self.event.duration, reschedule=True)
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
                mentions = ''
                for participant in self.event.participants:
                    if participant.member != interaction.user:
                        async with client.msg_lock:
                            participant.member.send(f'{interaction.user.name} has cancelled {self.event.name}.')
                        mentions += participant.member.mention
                print(f'{get_log_time()}> {self.event.name}> {interaction.user} cancelled by button press')
                self.cancel_button.style = ButtonStyle.gray
                self.start_button.disabled = True
                self.end_button.disabled = True
                self.reschedule_button.disabled = True
                self.cancel_button.disabled = True
                await interaction.response.edit_message(view=self)
                await self.event.text_channel.send(f'{mentions}\n{interaction.user.mention} cancelled {self.event.name}.')
            self.cancel_button.callback = cancel_button_callback
            self.add_item(self.cancel_button)

    class SchedulerClient(Client):
        FILENAME = 'info.json'

        def __init__(self, intents):
            super(SchedulerClient, self).__init__(intents=intents)
            self.tree = app_commands.CommandTree(self)
            self.events = []
            self.scheduled_events = []
            self.guild_scheduled_events = {}
            self.msg_lock = Lock()

        async def parse_scheduled_events(self):
            # track events that we find an existing scheduled event for
            touched_events = {}
            for event in self.events:
                touched_events[event] = False
            for scheduled_event in self.scheduled_events:
                if scheduled_event.status == EventStatus.scheduled or scheduled_event.status == EventStatus.active:
                    found = False
                    for event in self.events:
                        if not event.created:
                            continue
                        if scheduled_event.id == event.scheduled_event.id:
                            found = True
                            touched_events[event] = True
                            event.name = scheduled_event.name
                            event.created = True
                            event.start_time = scheduled_event.start_time.replace(second=0, microsecond=0)
                            event.end_time = scheduled_event.end_time
                            if scheduled_event.entity_type == EntityType.external:
                                location = scheduled_event.location
                            else:
                                location = client.get_channel(scheduled_event.channel_id)
                            event.voice_channel = location
                            participants = []
                            async for user in scheduled_event.users():
                                participants.append(Participant(user))
                            event.participants = participants
                            break
                    # create event in memory to match existing scheduled event
                    if not found:
                        participants = []
                        async for user in scheduled_event.users():
                            participants.append(Participant(user))
                        if scheduled_event.end_time:
                            time_difference = scheduled_event.end_time.replace(second=0, microsecond=0) - scheduled_event.start_time.replace(second=0, microsecond=0)
                            duration = int(time_difference.total_seconds() / 60)
                        else:
                            duration = 30
                        if scheduled_event.entity_type == EntityType.external:
                            location = scheduled_event.location
                        else:
                            location = client.get_channel(scheduled_event.channel_id)
                        event = Event(scheduled_event.name, scheduled_event.entity_type, location, participants, scheduled_event.guild, scheduled_event.guild.text_channels[0], None, duration)
                        event.created = True
                        event.start_time = scheduled_event.start_time.replace(second=0, microsecond=0)
                        event.end_time = event.start_time.replace(second=0, microsecond=0) + timedelta(minutes=duration)
                        event.voice_channel = location
                        event.scheduled_event = scheduled_event
                        self.events.append(event)
                        print(f'{get_log_time()}> {event.name}> Found event and added to memory')
                        print(f'{get_log_time()}> {event.name}> participants:')
                        for participant in event.participants:
                            print(f'{get_log_time()}> {event.name}> \t{participant.member.name}')
            # if a memory event is marked as created but doesn't have a scheduled event, delete it
            for event in touched_events:
                if not touched_events[event] and event.created and not event.scheduled_event:
                    self.events.remove(event)
                    print(f'{get_log_time()}> {event.name}> Did not find event and removed from memory')

        async def make_scheduled_event(self, event):
            event.scheduled_event = await event.guild.create_scheduled_event(name=event.name, description='Bot-generated event', start_time=event.start_time, end_time=event.end_time, entity_type=event.entity_type, channel=event.voice_channel, privacy_level=event.privacy_level)
            if event.image_url:
                try:
                    response = requests.get(event.image_url)
                    if response.status_code == 200:
                        await event.scheduled_event.edit(image=response.content)
                        print(f'{get_log_time()}> {event.name}> Processed image')
                    else:
                        event.image_url = ''
                        print(f'{get_log_time()}> {event.name}> Failed to get image')
                except Exception as e:
                    event.image_url = ''
                    print(f'{get_log_time()}> {event.name}> Failed to process image: {e}')
            client.scheduled_events.append(event.scheduled_event)
            event.ready_to_create = False
            event.created = True
            print(f'{get_log_time()}> {event.name}> Created event starting at {event.start_time.hour}:{event.start_time.minute} and ending at {event.end_time.hour}:{event.end_time.minute}')
            return event.scheduled_event

        async def setup_hook(self):
            await self.tree.sync()


    discord_token = os.getenv('DISCORD_TOKEN')
    client = SchedulerClient(intents=Intents.all())

    @client.event
    async def on_ready():
        print(f'{get_log_time()}> {client.user} has connected to Discord!')
        if not create_guild_event.is_running():
            create_guild_event.start()

    @client.event
    async def on_message(message):
        if message.author.bot or message.guild or not message.attachments or not message.content:
            return

        for guild in client.guilds:
            for scheduled_event in guild.scheduled_events:
                if scheduled_event.start_time < datetime.now().astimezone() + timedelta(hours=13):
                    client.scheduled_events.append(scheduled_event)
        await client.parse_scheduled_events()
        event_name = message.content.lower()
        for event in client.events:
            if event_name == event.name.lower():
                if event.created:
                    try:
                        image_bytes = await message.attachments[0].read()
                        await event.scheduled_event.edit(image=image_bytes)
                        await message.channel.send(f'Added your image to {event.name}.')
                        print(f'{get_log_time()}> {event.name}> {message.author.name} added an image')
                    except Exception as e:
                        await message.channel.send(f'Failed to add your image to {event.name}.\nError: {e}')
                        print(f'{get_log_time()}> {event.name}> Error adding image from {message.author.name}: {e}')
                else:
                    await message.channel.send(f'{event.name} has not been created yet. Please send an image after the event is created.')
                return
        await message.channel.send(f'Could not find event {event_name}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}')

    @client.tree.command(name='create', description='Create an event.')
    @app_commands.describe(event_name='Name for the event.')
    @app_commands.describe(voice_channel='Voice channel for the event.')
    @app_commands.describe(start_time='Start time for the event.')
    @app_commands.describe(image_url="URL to an image for the event.")
    @app_commands.describe(role='Only add users with this role as participants.')
    @app_commands.describe(duration="Event duration in minutes (30 minutes default).")
    # @app_commands.describe(weekly="Whether you want this to be a weekly reoccuring event.")
    async def create_command(interaction: Interaction, event_name: str, voice_channel: VoiceChannel, start_time: str, image_url: str = None, role: str = None, duration: int = 30): #, weekly: bool = False
        curHour, curMinute = get_time()
        if curHour == 1 and curMinute >= 25 and curHour < 7:
            await interaction.response.send_message(f'It\'s late, you should go to bed. Try again later today.')
            return

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

        # Put participants into a list
        participants = []
        print(f'{get_log_time()}> {event_name}> Received event request from {interaction.user.name}')
        if role != None:
            role = utils.find(lambda r: r.name.lower() == role.lower(), interaction.guild.roles)
        for member in interaction.channel.members:
            if not member.bot:
                if member.name != interaction.user.name and role != None and role not in member.roles:
                    continue
                participant = Participant(member)
                participants.append(participant)

        # Make event
        event = Event(event_name, EntityType.voice, voice_channel, participants, interaction.guild, interaction.channel, image_url, duration, start_time_obj) #, weekly
        event.start_time = start_time_obj
        client.events.append(event)
        event.scheduled_event = await client.make_scheduled_event(event)
        response = ''
        if event.start_time.hour < 10 and event.start_time.minute < 10:
            response = f'{interaction.user.mention} created an event called {event.name} starting at 0{event.start_time.hour}:0{event.start_time.minute}.'
        elif event.start_time.hour >= 10 and event.start_time.minute < 10:
            response = f'{interaction.user.mention} created an event called {event.name} starting at {event.start_time.hour}:0{event.start_time.minute}.'
        elif event.start_time.hour < 10 and event.start_time.minute >= 10:
            response = f'{interaction.user.mention} created an event called {event.name} starting at 0{event.start_time.hour}:{event.start_time.minute}.'
        else:
            response = f'{interaction.user.mention} created an event called {event.name} starting at {event.start_time.hour}:{event.start_time.minute}.'
        await interaction.response.send_message(response, view=EventButtons(event))

    @client.tree.command(name='schedule', description='Create a scheduling event.')
    @app_commands.describe(event_name='Name for the event.')
    @app_commands.describe(voice_channel='Voice channel for the event.')
    @app_commands.describe(image_url="URL to an image for the event.")
    @app_commands.describe(role='Only add users with this role as participants.')
    @app_commands.describe(duration="Event duration in minutes (30 minutes default).")
    # @app_commands.describe(weekly="Whether you want this to be a weekly reoccuring event.")
    async def schedule_command(interaction: Interaction, event_name: str, voice_channel: VoiceChannel, image_url: str = None, role: str = None, duration: int = 30): #, weekly: bool = False
        curHour, curMinute = get_time()
        if curHour == 1 and curMinute >= 25 and curHour < 7:
            await interaction.response.send_message(f'It\'s late, you should go to bed. Try again later today.')
            return

        # Put participants into a list
        participants = []
        print(f'{get_log_time()}> {event_name}> Received event request from {interaction.user.name}')
        if role != None:
            role = utils.find(lambda r: r.name.lower() == role.lower(), interaction.guild.roles)
        for member in interaction.channel.members:
            if not member.bot:
                if member.name != interaction.user.name and role != None and role not in member.roles:
                    continue
                participant = Participant(member)
                participants.append(participant)

        # Make event object
        event = Event(event_name, EntityType.voice, voice_channel, participants, interaction.guild, interaction.channel, image_url, duration) #, weekly
        client.events.append(event)
        await interaction.response.send_message(f'{interaction.user.mention} wants to create an event called {event.name}. Check your DMs to share your availability!', view=EventButtons(event))

        await event.dm_all_participants(interaction, duration)

    @client.tree.command(name='reschedule', description='Reschedule an existing scheduled event.')
    @app_commands.describe(event_name='Name of the event to reschedule.')
    @app_commands.describe(image_url='URL to an image for the event.')
    @app_commands.describe(duration='Event duration in minutes (default 30 minutes).')
    async def reschedule_command(interaction: Interaction, event_name: str, image_url: str = None, duration: int = 30):
        for guild in client.guilds:
            for scheduled_event in guild.scheduled_events:
                if scheduled_event.start_time < datetime.now().astimezone() + timedelta(hours=13):
                    client.scheduled_events.append(scheduled_event)
        await client.parse_scheduled_events()
        event_name = event_name.lower()
        for event in client.events:
            if event_name == event.name.lower():
                print(f'{get_log_time()}> {event.name}> {interaction.user.name} requested reschedule')
                if event.created:
                    new_event = Event(event.name, event.entity_type, event.voice_channel, event.participants, event.guild, interaction.channel, image_url, duration) #, weekly
                    client.scheduled_events.remove(event.scheduled_event)
                    await event.scheduled_event.delete(reason='Reschedule command issued.')
                    await event.remove()
                    client.events.append(new_event)
                    await interaction.response.send_message(f'{interaction.user.mention} wants to reschedule {new_event.name}. Check your DMs to share your availability!')
                    await new_event.dm_all_participants(interaction, duration, reschedule=True)
                else:
                    await interaction.response.send_message(f'{event.name} has not been created yet. Your buttons will work until it is created or cancelled.')
                return
        await interaction.response.send_message(f'Could not find event {event_name}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}')

    @client.tree.command(name='cancel', description='Cancel an event.')
    @app_commands.describe(event_name='Name of the event to cancel.')
    async def cancel_command(interaction: Interaction, event_name: str):
        for guild in client.guilds:
            for scheduled_event in guild.scheduled_events:
                if scheduled_event.start_time < datetime.now().astimezone() + timedelta(hours=13):
                    client.scheduled_events.append(scheduled_event)
        await client.parse_scheduled_events()
        event_name = event_name.lower()
        for event in client.events:
            if event_name == event.name.lower():
                print(f'{get_log_time()}> {event.name}> {interaction.user.name} cancelled event')
                if event.created:
                    client.scheduled_events.remove(event.scheduled_event)
                    await event.scheduled_event.delete(reason='Cancel command issued.')
                await event.remove()
                mentions = ''
                for participant in event.participants:
                    if participant.member != interaction.user:
                        async with client.msg_lock:
                            participant.member.send(f'{interaction.user.name} has cancelled {event.name}.')
                        mentions += participant.member.mention
                await interaction.response.send_message(f'{mentions}\n{interaction.user.mention} has cancelled {event.name}.')
                return
        await interaction.response.send_message(f'Could not find event {event_name}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}')

    @tasks.loop(minutes=1)
    async def create_guild_event():
        local_scheduled_event_names = [scheduled_event.name for scheduled_event in client.scheduled_events]
        for guild in client.guilds:
            guild_scheduled_event_names = [scheduled_event.name for scheduled_event in guild.scheduled_events]
            # Clean removed events
            if guild.id in client.guild_scheduled_events:
                try:
                    for local_scheduled_event in client.guild_scheduled_events[guild.id].copy():
                        if local_scheduled_event.name not in guild_scheduled_event_names:
                            client.guild_scheduled_events[guild.id].remove(local_scheduled_event)
                            client.scheduled_events.remove(local_scheduled_event)
                            print(f'{get_log_time()}> {local_scheduled_event.name}> Guild scheduled_event is gone, removed from local scheduled_events')
                except Exception as e: 
                    print(f'{get_log_time()}> Error removing guild scheduled event: {e}')
            # Add new events
            for guild_scheduled_event in guild.scheduled_events:
                if guild_scheduled_event.name not in local_scheduled_event_names:
                    try:
                        guild_events = client.guild_scheduled_events[guild.id]
                        guild_events.append(guild_scheduled_event)
                    except:
                        guild_events = [guild_scheduled_event]
                    client.guild_scheduled_events[guild.id] = guild_events
                    client.scheduled_events.append(guild_scheduled_event)
                    print(f'{get_log_time()}> {guild_scheduled_event.name}> New guild scheduled_event, added to local scheduled_events')
        await client.parse_scheduled_events()

        curTime = datetime.now().astimezone().replace(second=0, microsecond=0)
        for event in client.events.copy():
            if not event.valid:
                await event.text_channel.send(f'No shared availability has been found. Scheduling for {event.name} has been cancelled.\n' + event.reason)
                for participant in event.participants:
                    await participant.member.send(f'Scheduling for {event.name} has been cancelled.')
                await event.remove()
                print(f'{get_log_time()}> {event.name}> Event invalid, removed event from memory')
            elif get_datetime_from_label('01:30') <= curTime:
                await event.remove()
                print(f'{get_log_time()}> {event.name}> last time slot passed, removed event from memory')

        for event in client.events:
            await event.nudge_unresponded_participants()

            if event.created:
                if curTime + timedelta(minutes=5) == event.start_time and event.scheduled_event.status == EventStatus.scheduled:
                    await event.text_channel.send(f'**5 minute warning!** {event.name} will start in 5 minutes.')
                elif curTime == event.start_time and event.scheduled_event.status == EventStatus.scheduled:
                    try:
                        await event.scheduled_event.start(reason='It is the event\'s start time.')
                        await event.text_channel.send(f'**Event starting now!** {event.name} is starting now.')
                    except Exception as e:
                        print(f'{get_log_time()}> {event.name}> Failed to start event: {e}')
                elif curTime == event.end_time and event.scheduled_event.status == EventStatus.active:
                    try:
                        await event.scheduled_event.end(reason='It is the event\'s end time.')
                        client.scheduled_events.remove(event.scheduled_event)
                        client.events.remove(event)
                    except Exception as e:
                        print(f'{get_log_time()}> {event.name}> Failed to end event: {e}')
                continue

            if event.changed or not event.has_everyone_answered():
                event.changed = False
                continue

            event.check_times()

            if event.ready_to_create:
                event.scheduled_event = await client.make_scheduled_event(event)

                mentions = ''
                unsubbed = ''
                for participant in event.participants:
                    if participant.subscribed:
                        mentions += f'{participant.member.mention} '
                    else:
                        unsubbed += f'{participant.member.name} '
                if unsubbed != '':
                    unsubbed = '\nUnsubscribed: ' + unsubbed
                response = ''
                if event.start_time.hour < 10 and event.start_time.minute < 10:
                    response = f'{mentions}\nHeads up! You are all available for {event.name} starting at 0{event.start_time.hour}:0{event.start_time.minute}.\n' + unsubbed
                elif event.start_time.hour >= 10 and event.start_time.minute < 10:
                    response = f'{mentions}\nHeads up! You are all available for {event.name} starting at {event.start_time.hour}:0{event.start_time.minute}.\n' + unsubbed
                elif event.start_time.hour < 10 and event.start_time.minute >= 10:
                    response = f'{mentions}\nHeads up! You are all available for {event.name} starting at 0{event.start_time.hour}:{event.start_time.minute}.\n' + unsubbed
                else:
                    response = f'{mentions}\nHeads up! You are all available for {event.name} starting at {event.start_time.hour}:{event.start_time.minute}.\n' + unsubbed
                await event.text_channel.send(response, view=EventButtons(event))

    client.run(discord_token)


if __name__ == '__main__':
    main()
