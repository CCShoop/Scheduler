'''Written by Cael Shoop.'''

import os
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
    print(f'Current time is {hour}:{minute}')
    return hour, minute


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
            self.ready_to_create = False
            self.created = False
            self.changed = False
            self.start_time = None
            self.end_time = None
            self.valid = True

        def check_times(self):
            # Find first available shared time block and configure start/end times
            print(f'Comparing availabilities for {self.name}')
            shared_time_slot = ''
            for time_slot in timestamps.all_timestamps:
                shared_availability = True
                for participant in self.participants:
                    shared_availability = shared_availability and participant.is_available(time_slot)
                if shared_availability:
                    shared_time_slot = time_slot
                    break
            if shared_time_slot == '':
                print(f'Unable to find common availability for {self.name}')
                self.valid = False
                return
            partitioned_shared_time_slot = shared_time_slot.partition(':')
            hour = int(partitioned_shared_time_slot[0])
            minute = int(partitioned_shared_time_slot[2])
            self.start_time = datetime.datetime.now().astimezone()
            self.start_time = self.start_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
            self.end_time = self.start_time + datetime.timedelta(minutes=30)

            print(f'Ready to create event {self.name} at {hour}:{minute}')
            self.ready_to_create = True


    class TimeButton(View):
        def __init__(self, label: str, participant: Participant, event: Event):
            super().__init__(timeout=None)
            self.label = label
            self.participant = participant
            self.event = event
            self.add_button()

        def add_button(self):
            button = Button(label=self.label, style=ButtonStyle.red)
            async def button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                self.participant.answered = True
                self.participant.toggle_availability(self.label)
                if button.style == ButtonStyle.red:
                    button.style = ButtonStyle.green
                else:
                    button.style = ButtonStyle.red
                await interaction.response.edit_message(view=self)
                print(f'{self.participant.name} toggled availability to {self.participant.is_available(self.label)} at {self.label} for {self.event.name}')

            button.callback = button_callback
            self.add_item(button)


    class NoneButton(View):
        def __init__(self, participant: Participant, event: Event):
            super().__init__(timeout=None)
            self.label = "None"
            self.participant = participant
            self.event = event
            self.add_button()

        def add_button(self):
            button = Button(label=self.label, style=ButtonStyle.blurple)
            async def button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                self.participant.answered = True
                if button.style == ButtonStyle.blurple:
                    button.style = ButtonStyle.gray
                else:
                    button.style = ButtonStyle.blurple
                await interaction.response.edit_message(view=self)
                print(f'{self.participant.name} is unavailable for {self.event.name}')

            button.callback = button_callback
            self.add_item(button)


    class UnsubButton(View):
        def __init__(self, participant: Participant, event: Event):
            super().__init__(timeout=None)
            self.label = "Unsubscribe"
            self.participant = participant
            self.event = event
            self.add_button()

        def add_button(self):
            button = Button(label=self.label, style=ButtonStyle.blurple)
            async def button_callback(interaction: Interaction):
                self.event.changed = True
                self.event.ready_to_create = False
                self.participant.answered = True
                self.participant.subscribed = not self.participant.subscribed
                if button.style == ButtonStyle.blurple:
                    button.style = ButtonStyle.gray
                else:
                    button.style = ButtonStyle.blurple
                await interaction.response.edit_message(view=self)
                print(f'{self.participant.name} unsubscribed from {self.event.name}')

            button.callback = button_callback
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
        print(f'{client.user} has connected to Discord!')

    @client.tree.command(name='schedule', description='Create a scheduling event.')
    @app_commands.describe(event_name='Name for the event.')
    @app_commands.describe(voice_channel="Voice channel for the event.")
    async def schedule_command(interaction: Interaction, event_name: str, voice_channel: discord.VoiceChannel):
        # Put participants into a list
        participants = []
        print(f'Received event request "{event_name}"')
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
            await participant.member.send('Loading buttons, please wait.')

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
                    print(f'Making button {button_label} for {participant.member.name}')
                    await participant.member.send(view=TimeButton(label=button_label, participant=participant, event=event))
            
            await participant.member.send(view=NoneButton(participant=participant, event=event))
            await participant.member.send(view=UnsubButton(participant=participant, event=event))
            await participant.member.send(f'Select all of the 30 minute blocks you will be available to attend {event.name}!\n"None" will stop the event from being created, so if you want the event to occur without you, click "Unsubscribe."\nThe event will be created or cancelled 1-2 minutes after the last person responds.')
        print(f'Done DMing participants')


    @tasks.loop(minutes=1)
    async def create_guild_event():
        for event in client.events.copy():
            if event.created or not event.valid:
                if not event.valid:
                    channel = client.get_channel(int(event.interaction.channel_id))
                    await channel.send('Not everyone is available at any one time. The event scheduling has been cancelled.')
                client.events.remove(event)
                print(f'Removed event {event.name} from memory')

        for event in client.events:
            everyoneResponded = True
            for participant in event.participants:
                if participant.subscribed:
                    everyoneResponded = everyoneResponded and participant.answered
            if event.created or event.changed or not everyoneResponded:
                event.changed = False
                return

            event.check_times()

            if event.ready_to_create:
                print(f'Creating guild event {event.name} starting at {event.start_time} and ending at {event.end_time}')
                privacy_level = discord.PrivacyLevel.guild_only
                await event.guild.create_scheduled_event(name=event.name, description='Automatically generated event', start_time=event.start_time, end_time=event.end_time, channel=event.voice_channel, privacy_level=privacy_level)
                print(f'Created event {event.name}')
                event.ready_to_create = False
                event.created = True

                mentions = ''
                unsubbed = ''
                for participant in event.participants:
                    if participant.subscribed:
                        mentions += f'{participant.member.mention} '
                    else:
                        unsubbed += f'{participant.member.name}'
                channel = client.get_channel(int(event.interaction.channel_id))
                if event.start_time.hour < 10 and event.start_time.minute < 10:
                    await channel.send(f'{mentions}\nHeads up! You are all available for {event.name} starting at 0{event.start_time.hour}:0{event.start_time.minute}.\nUnsubscribed: ' + unsubbed)
                elif event.start_time.hour < 10 and event.start_time.minute >= 10:
                    await channel.send(f'{mentions}\nHeads up! You are all available for {event.name} starting at 0{event.start_time.hour}:{event.start_time.minute}.\nUnsubscribed: ' + unsubbed)
                elif event.start_time.hour >= 10 and event.start_time.minute < 10:
                    await channel.send(f'{mentions}\nHeads up! You are all available for {event.name} starting at {event.start_time.hour}:0{event.start_time.minute}.\nUnsubscribed: ' + unsubbed)
                else:
                    await channel.send(f'{mentions}\nHeads up! You are all available for {event.name} starting at {event.start_time.hour}:{event.start_time.minute}.\nUnsubscribed: ' + unsubbed)

    client.run(discord_token)


if __name__ == '__main__':
    main()
