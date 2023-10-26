'''Written by Cael Shoop.'''

import os
import discord
from buttons import Buttons
from dotenv import load_dotenv
from discord import app_commands

load_dotenv()

MY_GUILD = discord.Object(id=1163693407206645782)

def main():
    '''Main function'''


    class Availability:
        '''Availability class'''

        def __init__(self):
            '''Availability __init__'''

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

        # end Availability.__init__()

    # end Availability


    class Participant:
        '''Participant class'''

        def __init__(self, member):
            '''Participant __init__'''

            self.member = member
            self.availability = Availability()

    # end Participant


    class Event:
        '''Event class'''
    
        def __init__(self, name: str, participants: list):
            self.name = name
            self.participants = participants

    # end Event


    class SchedulerClient(discord.Client):
        '''Custom client for Scheduling bot'''

        FILENAME = 'info.json'

        def __init__(self, intents):
            '''Subclass __init__'''
            super(SchedulerClient, self).__init__(intents=intents)
            self.tree = app_commands.CommandTree(self)
            self.events = []


        async def setup_hook(self):
            '''Setup hook'''
            self.tree.copy_global_to(guild=MY_GUILD)
            await self.tree.sync(guild=MY_GUILD)

    # end SchedulerClient


    discord_token = os.getenv('DISCORD_TOKEN')
    intents = discord.Intents.all()
    client = SchedulerClient(intents=intents)


    @client.event
    async def on_ready():
        '''Client on_ready event'''
    
        print(f'{client.user} has connected to Discord!')


    @client.tree.command(name='schedule', description='Create a scheduling event.')
    @app_commands.describe(event_name='Name for the event.')
    async def schedule_command(interaction: discord.Interaction, event_name: str):
        '''Command to create a scheduling event'''
    
        # Put participants into a list
        participants = []
        print(f'Received event request "{event_name}" for these members:', end='')
        for member in interaction.channel.members:
            if not member.bot:
                print(f' {member.name}', end='')
                participant = Participant(member)
                participants.append(participant)


        # Make event object
        event = Event(event_name, participants)
        client.events.append(event)
        await interaction.response.send_message(f'{interaction.user.mention} wants to create an event called "{event_name}". Check your DMs to share your availability!')


        # Create buttons
        creator = Buttons()
        sixteen_hundred_button = creator.make_button(creator.sixteen_hundred_hours)
        sixteen_hundred_thirty_button = creator.make_button(creator.sixteen_hundred_thirty_hours)
        seventeen_hundred_button = creator.make_button(creator.seventeen_hundred_hours)
        seventeen_hundred_thirty_button = creator.make_button(creator.seventeen_hundred_thirty_hours)
        eighteen_hundred_button = creator.make_button(creator.eighteen_hundred_hours)
        eighteen_hundred_thirty_button = creator.make_button(creator.eighteen_hundred_thirty_hours)
        nineteen_hundred_button = creator.make_button(creator.nineteen_hundred_hours)
        nineteen_hundred_thirty_button = creator.make_button(creator.nineteen_hundred_thirty_hours)
        twenty_hundred_button = creator.make_button(creator.twenty_hundred_hours)
        twenty_hundred_thirty_button = creator.make_button(creator.twenty_hundred_thirty_hours)
        twenty_one_hundred_button = creator.make_button(creator.twenty_one_hundred_hours)
        twenty_one_hundred_thirty_button = creator.make_button(creator.twenty_one_hundred_thirty_hours)
        twenty_two_hundred_button = creator.make_button(creator.twenty_two_hundred_hours)
        twenty_two_hundred_thirty_button = creator.make_button(creator.twenty_two_hundred_thirty_hours)
        twenty_three_hundred_button = creator.make_button(creator.twenty_three_hundred_hours)
        twenty_three_hundred_thirty_button = creator.make_button(creator.twenty_three_hundred_thirty_hours)
        zero_hundred_button = creator.make_button(creator.zero_hundred_hours)
        zero_hundred_thirty_button = creator.make_button(creator.zero_hundred_thirty_hours)
        one_hundred_button = creator.make_button(creator.one_hundred_hours)
        one_hundred_thirty_button = creator.make_button(creator.one_hundred_thirty_hours)
        done_button = creator.make_button(creator.done)

        def n_split(iterable, n, fillvalue=None):
            num_extra = len(iterable) % n
            zipped = zip(*[iter(iterable)] * n)
            return zipped if not num_extra else zipped + [iterable[-num_extra:], ]

        action_groups = []
        for group in n_split(creator.buttons, 5):
            action_groups.append(group)


        def get_participant(name):
            for participant in participants:
                if participant.member.name == name:
                    return participant
            return False


        # Button callbacks
        async def sixteen_hundred_button_callback(interaction):
            sixteen_hundred_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.sixteen_hundred = True
            print(f'{participant.member.name} is available at 16:00')
            # TODO: followup or response

        async def sixteen_hundred_thirty_button_callback(interaction):
            sixteen_hundred_thirty_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.sixteen_hundred_thirty = True
            print(f'{participant.member.name} is available at 16:30')

        async def seventeen_hundred_button_callback(interaction):
            seventeen_hundred_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.seventeen_hundred = True
            print(f'{participant.member.name} is available at 17:00')

        async def seventeen_hundred_thirty_button_callback(interaction):
            seventeen_hundred_thirty_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.seventeen_hundred_thirty = True
            print(f'{participant.member.name} is available at 17:30')

        async def eighteen_hundred_button_callback(interaction):
            eighteen_hundred_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.eighteen_hundred = True
            print(f'{participant.member.name} is available at 18:00')

        async def eighteen_hundred_thirty_button_callback(interaction):
            eighteen_hundred_thirty_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.eighteen_hundred_thirty = True
            print(f'{participant.member.name} is available at 18:30')

        async def nineteen_hundred_button_callback(interaction):
            nineteen_hundred_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.nineteen_hundred = True
            print(f'{participant.member.name} is available at 19:00')

        async def nineteen_hundred_thirty_button_callback(interaction):
            nineteen_hundred_thirty_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.nineteen_hundred_thirty = True
            print(f'{participant.member.name} is available at 19:30')

        async def twenty_hundred_button_callback(interaction):
            twenty_hundred_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.twenty_hundred = True
            print(f'{participant.member.name} is available at 20:00')

        async def twenty_hundred_thirty_button_callback(interaction):
            twenty_hundred_thirty_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.twenty_hundred_thirty = True
            print(f'{participant.member.name} is available at 20:30')

        async def twenty_one_hundred_button_callback(interaction):
            twenty_one_hundred_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.twenty_one_hundred = True
            print(f'{participant.member.name} is available at 21:00')

        async def twenty_one_hundred_thirty_button_callback(interaction):
            twenty_one_hundred_thirty_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.twenty_one_hundred_thirty = True
            print(f'{participant.member.name} is available at 21:30')

        async def twenty_two_hundred_button_callback(interaction):
            twenty_two_hundred_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.twenty_two_hundred = True
            print(f'{participant.member.name} is available at 22:00')

        async def twenty_two_hundred_thirty_button_callback(interaction):
            twenty_two_hundred_thirty_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.twenty_two_hundred_thirty = True
            print(f'{participant.member.name} is available at 22:30')

        async def twenty_three_hundred_button_callback(interaction):
            twenty_three_hundred_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.twenty_three_hundred = True
            print(f'{participant.member.name} is available at 23:00')

        async def twenty_three_hundred_thirty_button_callback(interaction):
            twenty_three_hundred_thirty_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.twenty_three_hundred_thirty = True
            print(f'{participant.member.name} is available at 23:30')

        async def zero_hundred_button_callback(interaction):
            zero_hundred_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.zero_hundred = True
            print(f'{participant.member.name} is available at 00:00')

        async def zero_hundred_thirty_button_callback(interaction):
            zero_hundred_thirty_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.zero_hundred_thirty = True
            print(f'{participant.member.name} is available at 00:30')

        async def one_hundred_button_callback(interaction):
            one_hundred_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.one_hundred = True
            print(f'{participant.member.name} is available at 01:00')

        async def one_hundred_thirty_button_callback(interaction):
            one_hundred_thirty_button.style = discord.ButtonStyle.green
            participant = get_participant(interaction.user.name)
            participant.availability.one_hundred_thirty = True
            print(f'{participant.member.name} is available at 01:30')

        async def done_button_callback(interaction):
            participant = get_participant(interaction.user.name)
            print(f'{interaction.user.name} completed their schedule')
            # TODO: share schedule in text_channel


        sixteen_hundred_button.callback = sixteen_hundred_button_callback
        sixteen_hundred_thirty_button.callback = sixteen_hundred_thirty_button_callback
        seventeen_hundred_button.callback = seventeen_hundred_button_callback
        seventeen_hundred_thirty_button.callback = seventeen_hundred_thirty_button_callback
        eighteen_hundred_button.callback = eighteen_hundred_button_callback
        eighteen_hundred_thirty_button.callback = eighteen_hundred_thirty_button_callback
        nineteen_hundred_button.callback = nineteen_hundred_button_callback
        nineteen_hundred_thirty_button.callback = nineteen_hundred_thirty_button_callback
        twenty_hundred_button.callback = twenty_hundred_button_callback
        twenty_hundred_thirty_button.callback = twenty_hundred_thirty_button_callback
        twenty_one_hundred_button.callback = twenty_one_hundred_button_callback
        twenty_one_hundred_thirty_button.callback = twenty_one_hundred_thirty_button_callback
        twenty_two_hundred_button.callback = twenty_two_hundred_button_callback
        twenty_two_hundred_thirty_button.callback = twenty_two_hundred_thirty_button_callback
        twenty_three_hundred_button.callback = twenty_three_hundred_button_callback
        twenty_three_hundred_thirty_button.callback = twenty_three_hundred_thirty_button_callback
        zero_hundred_button.callback = zero_hundred_button_callback
        zero_hundred_thirty_button.callback = zero_hundred_thirty_button_callback
        one_hundred_button.callback = one_hundred_button_callback
        one_hundred_thirty_button.callback = one_hundred_thirty_button_callback
        done_button.callback = done_button_callback


        # Prep DM messages
        button_messages = []
        for action_group in action_groups:
            print(f'{action_group}')
            button_messages.append(action_group)

        # DM event participants
        for participant in participants:
            await participant.member.send(f'Select all of the 30 minute blocks you will be available to attend "{event_name}!"')
            for button_message in button_messages:
                view = discord.ui.View(timeout=1080)
                for button in button_message:
                    view.add_item(button)
                await participant.member.send(view=view)

    # end schedule command


    client.run(discord_token)

# end main()


if __name__ == '__main__':
    main()
