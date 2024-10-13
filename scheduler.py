'''Written by Cael Shoop.'''

import os
import time
import logging
import asyncio
import requests
from typing import Literal
from dotenv import load_dotenv
from datetime import datetime, timedelta
from discord import (app_commands, Interaction, Intents, Client, Embed, Color,
                     ButtonStyle, EventStatus, EntityType, TextChannel,
                     VoiceChannel, Message, SelectOption, ScheduledEvent,
                     Guild, PrivacyLevel, User, utils, NotFound, HTTPException)
from discord.ui import View, Button, Modal, TextInput, Select
from discord.ext import tasks

from persistence import Persistence
from participant import Participant, TimeBlock
from server import Server

# .env
load_dotenv()

# Logger setup
logger = logging.getLogger("Event Scheduler")
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter(fmt='[Scheduler] [%(asctime)s] [%(levelname)s\t] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

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

# Time in minutes to delay "immediate" start
START_TIME_DELAY = 10
# Number of updates before an event is cleared
# updates/min * min/hour * hours/day * days
EVENT_TIMEOUT: int = (2 * 60 * 24 * 3)


# Get time string from minutes
def get_time_str_from_minutes(minutes: int) -> str:
    if minutes < 0:
        minutes *= -1
    output = ''
    weeks = int(minutes // 60 // 24 // 7)
    if weeks != 0:
        output += f'{weeks} weeks ' if weeks != 1 else '1 week '
    days = int(minutes // 60 // 24 % 7)
    if days != 0:
        output += f'{days} days ' if days != 1 else '1 day '
    hours = int(minutes // 60 % 24)
    if hours != 0:
        output += f'{hours} hours ' if hours != 1 else '1 hour '
    mins = int(minutes % 60)
    if mins != 0:
        output += f'{mins} minutes ' if mins != 1 else '1 minute '
    if mins == 0 and hours == 0 and days == 0 and weeks == 0:
        output = '0 minutes'
    return output


# Add a 0 if the digit is < 10
def double_digit_string(digit_string: str) -> str:
    if int(digit_string) < 10 and len(digit_string) == 1:
        digit_string = '0' + digit_string
    return digit_string


class SchedulerClient(Client):
    def __init__(self, intents) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.loaded_json = False
        self.server_is_running = False
        self.server = Server()
        self.server.callback = self.schedule_from_dict
        self.events = []

    async def start_server(self):
        self.server_is_running = True
        asyncio.create_task(self.server.start_server())

    async def schedule_from_dict(self, data: dict) -> None:
        logger.info(f"{data['name']}: Schedule from dict triggered")
        guild = self.get_guild(data["guildId"])
        textChannel = guild.get_channel(data["textChannelId"])
        voiceChannel = guild.get_channel(data["voiceChannelId"])
        await schedule(eventName=data["name"],
                       guild=guild,
                       textChannel=textChannel,
                       voiceChannel=voiceChannel,
                       schedulerId=data["notifierId"],
                       imageUrl=data["imageUrl"],
                       includeExclude=data["includeExclude"],
                       usernames=data["usernames"],
                       roles=data["roles"],
                       duration=data["duration"],
                       multiEvent=data["multiEvent"])

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
                            raise Exception('Failed to create event object')
                        if event.availability_message is not None:
                            event.avail_buttons = AvailabilityButtons(event)
                            await event.availability_message.edit(view=event.avail_buttons)
                        if event.responded_message is not None:
                            await event.update_responded_message()
                        if event.event_buttons_message is not None:
                            event.event_buttons = EventButtons(event)
                            if event.started:
                                event.event_buttons.start_button.style = ButtonStyle.green
                                event.event_buttons.start_button.disabled = True
                                event.event_buttons.end_button.disabled = False
                                event.event_buttons.reschedule_button.disabled = True
                                event.event_buttons.cancel_button.disabled = True
                                # Offset all other events that share this location to start after the end of this event
                                buffer_time = timedelta(minutes=0)
                                other_events = []
                                prev_event = None
                                for other_event in client.events:
                                    if other_event != event and (other_event.voice_channel == event.voice_channel or other_event.shares_participants(event)):
                                        for other_event_start_time in other_event.start_times:
                                            if other_event_start_time < (event.start_times[0] + event.duration + buffer_time):
                                                other_event_start_time = (event.start_times[0] + event.duration + buffer_time)
                                        other_events.append(other_event)
                                # Offset the events from each other to prevent stack smashing
                                for other_event in other_events:
                                    if prev_event:
                                        for other_event_start_time in other_event.start_times:
                                            if other_event_start_time < (prev_event.start_times[0] + prev_event.duration + buffer_time):
                                                other_event_start_time = (prev_event.start_times[0] + prev_event.duration + buffer_time)
                                    prev_event = other_event
                                # Disable start buttons of events scheduled for the same channel
                                for other_event in client.events:
                                    if other_event == event or not other_event.created or other_event.voice_channel != event.voice_channel:
                                        continue
                                    other_event.event_buttons.start_button.disabled = True
                                    try:
                                        await other_event.event_buttons_message.edit(view=other_event.event_buttons)
                                        logger.info(f'{event}: Disabled start button for event with same location: {other_event.name}')
                                    except Exception as e:
                                        logger.error(f'{event}: Failed to disable start button for {other_event}: {e}')
                            await event.event_buttons_message.edit(view=event.event_buttons)
                        self.events.append(event)
                        logger.info(f'{event}: event loaded and added to client event list')
                    except Exception as e:
                        logger.error(f'Could not add event to client event list: {e}')
                    # Stop rate limiting when launching bot
                    time.sleep(3)
            else:
                logger.info('No json data found')

    # Return all events as a dict
    def get_events_dict(self) -> dict:
        events_data = {}
        events_data['events'] = [event.to_dict() for event in self.events]
        return events_data

    async def setup_hook(self):
        await self.tree.sync()


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
                 scheduler=None,
                 participants: list = None,
                 duration=timedelta(minutes=30),
                 multi_event: bool = False,
                 start_times: list = None,
                 availability_message=None,
                 avail_buttons=None,
                 responded_message=None,
                 event_buttons_message=None,
                 event_buttons=None,
                 event_buttons_msg_content_pt1: str = '',
                 event_buttons_msg_content_pt2: str = '',
                 event_buttons_msg_content_pt3: str = '',
                 event_buttons_msg_content_pt4: str = '',
                 ready_to_create: bool = False,
                 created: bool = False,
                 started: bool = False,
                 scheduled_events: list = None,
                 changed: bool = False,
                 unavailable: bool = False,
                 timeout_counter: int = EVENT_TIMEOUT
                 ) -> None:
        self.name = name
        self.guild = guild
        self.entity_type = EntityType.voice
        self.text_channel = text_channel
        self.availability_message = availability_message
        self.responded_message = responded_message
        if voice_channel:
            self.voice_channel = voice_channel
        else:
            try:
                self.voice_channel = self.guild.voice_channels[0]
            except Exception as e:
                logger.error(f'Failed to get voice channel: {e}')
                self.voice_channel = None
        self.privacy_level = PrivacyLevel.guild_only
        self.scheduler = scheduler
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
        self.scheduled_events: list = scheduled_events if scheduled_events is not None else []
        self.changed = changed
        self.start_times: list = start_times if start_times is not None else []
        self.duration = duration
        self.mins_until_start: int = 0
        self.unavailable = unavailable
        self.multi_event = multi_event
        self.timeout_counter: int = timeout_counter
        self.avail_msg_content_pt1 = f'**Event name:** {self.name}'
        self.avail_msg_content_pt1 += '\n**Duration:** '
        if self.scheduler:
            if self.scheduler.nick:
                self.avail_msg_content_pt2 = f'\n**Scheduled by:** {self.scheduler.nick}'
            else:
                self.avail_msg_content_pt2 = f'\n**Scheduled by:** {self.scheduler.name}'
        self.avail_msg_content_pt2 += f'\n**Multi-event:** {self.multi_event}'
        self.avail_msg_content_pt2 += '\n**Times out in:** '
        self.avail_msg_content_pt3 = '\n\nSelect **Respond** to enter your availability.'
        self.avail_msg_content_pt3 += '\n**Full** will mark you as available from now until midnight tonight.'
        self.avail_msg_content_pt3 += '\n**Use Existing** will attempt to grab your availability from another event.'
        self.avail_msg_content_pt3 += '\n**Unsubscribe** will allow the event to occur without you; however, you can still respond and participate.'
        self.avail_msg_content_pt3 += '\n**Cancel** will cancel scheduling.'

    # Return all timeblocks that intersect each other
    def intersect_time_blocks(self, timeblocks1: list, timeblocks2: list) -> list:
        intersected_time_blocks = []
        for block1 in timeblocks1:
            for block2 in timeblocks2:
                start_time = max(block1.start_time, block2.start_time)
                end_time = min(block1.end_time, block2.end_time)
                if start_time < end_time:
                    intersected_time_blocks.append(TimeBlock(start_time, end_time))
        return intersected_time_blocks

    # Compare availabilities of all subscribed participants
    def compare_availabilities(self) -> None:
        if self.created or self.ready_to_create or self.changed:
            return
        self.changed = True
        subbed_participants = []
        for participant in self.participants:
            if participant.subscribed:
                subbed_participants.append(participant)

        current_time = datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(minutes=START_TIME_DELAY)

        # Check if all participants are available in [START_TIME_DELAY] minutes
        all_participants_available = True
        for participant in subbed_participants:
            if not participant.is_available_at(current_time, self.duration):
                all_participants_available = False
                break

        dates_scheduled = []
        cur_date = current_time.date()
        if all_participants_available:
            self.start_times.append(current_time)
            self.five_minute_warning_flag = True
            self.ready_to_create = True
            if not self.multi_event:
                return
            dates_scheduled.append(cur_date)

        # Find the earliest common availability
        available_timeblocks = []
        for participant in subbed_participants:
            available_timeblocks.append(participant.availability)
        intersected_timeblocks = available_timeblocks[0]
        for timeblocks in available_timeblocks[1:]:
            intersected_timeblocks = self.intersect_time_blocks(intersected_timeblocks, timeblocks)

        for timeblock in intersected_timeblocks:
            date_scheduled = False
            tb_date = timeblock.start_time.date()
            for date in dates_scheduled:
                if tb_date.month == date.month and tb_date.day == date.day and tb_date.year == date.year:
                    logger.info(f'{self.name}: already has event scheduled for date: {tb_date.month}/{tb_date.day}: {timeblock.start_time.strftime("%H:%M")}')
                    date_scheduled = True
                    break
            if timeblock.duration >= self.duration and not date_scheduled:
                logger.info(f'{self.name}: start time for {tb_date.month}/{tb_date.day}: {timeblock.start_time.strftime("%H:%M")}')
                self.start_times.append(timeblock.start_time)
                self.ready_to_create = True
                dates_scheduled.append(tb_date)
        if not self.ready_to_create:
            logger.info(f'{self.name}: compare_availabilities: No common availability found between all participants, cancelling event')

    # Check before everyone has responded to see if two people have answered and are not available
    def check_availabilities(self):
        subbed_participants = []
        for participant in self.participants:
            if participant.subscribed and participant.answered:
                subbed_participants.append(participant)

        current_time = datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(minutes=START_TIME_DELAY)

        # Find common availability
        available_timeblocks = []
        for participant in subbed_participants:
            available_timeblocks.append(participant.availability)
        if not available_timeblocks:
            return None

        # Get the latest timeblock date
        latest_date = current_time.date()
        for availability in available_timeblocks:
            for timeblock in availability:
                latest_date = max(timeblock.start_time.date(), latest_date)
        intersected_timeblocks = available_timeblocks[0]
        for timeblocks in available_timeblocks[1:]:
            intersected_timeblocks = self.intersect_time_blocks(intersected_timeblocks, timeblocks)

        # Mark participant as unanswered if their last timeblock isn't on the same date as the last entry
        for participant in self.participants:
            if participant.answered and not participant.unavailable:
                try:
                    if participant.availability[-1].start_time.date() < latest_date:
                        participant.answered = False
                except Exception as e:
                    logger.warning(f'Failed to access last availability block: {e}')
                    participant.answered = False
        return latest_date

    # Return the number of participants who have responded
    def number_of_responded(self) -> int:
        responded = 0
        for participant in self.participants:
            if participant.subscribed and participant.answered:
                responded += 1
        return responded

    # Prep next scheduled event
    async def prep_next_scheduled_event(self) -> None:
        self.scheduled_events = self.scheduled_events[1:]
        self.start_times = self.start_times[1:]
        self.five_minute_warning_flag = False
        if self.scheduled_events:
            message_content = self.get_event_buttons_message_string()
            self.event_buttons = EventButtons(self)
            self.event_buttons_message = await self.text_channel.send(content=message_content, view=self.event_buttons)

    # Make the guild scheduled event objects, set an image if there is a url
    async def make_scheduled_events(self) -> None:
        for start_time in self.start_times:
            scheduled_event = await self.guild.create_scheduled_event(name=self.name, description='Bot-generated event', start_time=start_time, entity_type=self.entity_type, channel=self.voice_channel, privacy_level=self.privacy_level)
            if self.image_url:
                try:
                    response = requests.get(self.image_url)
                    if response.status_code == 200:
                        await scheduled_event.edit(image=response.content)
                        logger.info(f'{self.name}: Processed image')
                    else:
                        self.image_url = ''
                        logger.warning(f'{self.name}: Failed to get image')
                except Exception as e:
                    self.image_url = ''
                    logger.exception(f'{self.name}: Failed to process image: {e}')
            self.scheduled_events.append(scheduled_event)
            logger.info(f'{self.name}: Created event starting {start_time.strftime("%m/%d/%Y: %H:%M")} ET')
        self.ready_to_create = False
        self.created = True
        self.changed = False

    # Get a string explaining the current event status
    def get_scheduling_status(self) -> str:
        if self.started:
            return "Started event"
        if self.created:
            return "Created event"
        if self.ready_to_create:
            return "Preparing to create event"
        if self.changed:
            return "Processing availability"
        return "Awaiting availability"

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

    # If a user isn't a participant, add them
    def add_user_as_participant(self, user: User) -> None:
        if user.id not in [participant.member.id for participant in self.participants]:
            member = self.guild.get_member(user.id)
            participant = Participant(member=member)
            self.participants.append(participant)

    # Get a participant from the event with a username
    def get_participant(self, username: str) -> Participant:
        for participant in self.participants:
            if participant.member.name == username:
                return participant
        return None

    # Whether or not this event shares participants with the other event
    def shares_participants(self, event) -> bool:
        for self_participant in self.participants:
            for other_participant in event.participants:
                if self_participant.member.id == other_participant.member.id:
                    return True
        return False

    # Get a list of shared participants with the other event
    def shared_participants(self, event) -> list:
        other_participants = []
        for self_participant in self.participants:
            for other_participant in event.participants:
                if self_participant.member.id == other_participant.member.id:
                    other_participants.append(other_participant)
                    logger.info(f"Found shared participant {self_participant.member.name} in {self.name} and {event.name}")
        return other_participants

    # Get availability for participant from another event
    def get_other_availability(self, participant: Participant) -> list:
        event_availabilities = []
        for other_event in client.events:
            if other_event != self:
                for other_participant in other_event.participants:
                    if other_participant.member.id == participant.member.id and other_participant.answered:
                        event_avail = EventAvailability(other_event, other_participant.availability, other_participant.full_availability_flag)
                        event_availabilities.append(event_avail)
        return event_availabilities

    # Set event_buttons_msg_content parts and get response message
    def get_event_buttons_message_string(self, end_time: datetime = None) -> str:
        if end_time is None:
            duration = f"{get_time_str_from_minutes(self.get_duration_minutes())}"
            time_until_start: timedelta = self.start_times[0] - datetime.now().astimezone()
            self.mins_until_start = (time_until_start.total_seconds() // 60) + 1
        else:
            real_duration: timedelta = end_time - self.start_times[0]
            duration = f"{get_time_str_from_minutes(real_duration.total_seconds() // 60)}"
        unsubbed = self.get_names_string(unsubscribed_only=True)
        if unsubbed != "":
            unsubbed = f"\n**Unsubscribed:** {unsubbed}"

        # List subscribed people and list unsubscribed people
        self.event_buttons_msg_content_pt1  = f"**Event name:** {self.name}"
        self.event_buttons_msg_content_pt1 += f"\n**Scheduled:** {self.get_start_time_string(0)}"
        self.event_buttons_msg_content_pt1 += f"\n**Duration:** {duration}"
        self.event_buttons_msg_content_pt1 += f"\n**Multi-event:** {self.multi_event}"
        # Event has not started
        if end_time is None and not self.started:
            if self.mins_until_start > 0:
                self.event_buttons_msg_content_pt2 = f"\n**Starts in:** {get_time_str_from_minutes(self.mins_until_start)}"
            elif self.mins_until_start == 0:
                self.event_buttons_msg_content_pt2 = "\n**Starting now**"
            else:
                self.event_buttons_msg_content_pt2 = f"\n**Overdue by:** {get_time_str_from_minutes(self.mins_until_start)}"
        # Event is in progress
        elif end_time is None and self.started:
            self.event_buttons_msg_content_pt2 = f"\n**Started:** {self.get_start_time_string(0)}"
        # Event has ended
        else:
            self.event_buttons_msg_content_pt2 = f'\n**Ended:** {end_time.strftime("%A, %m/%d at %H:%M")} ET'
        self.event_buttons_msg_content_pt3 = f"\n{self.get_names_string(subscribed_only=True, mention=True)}"
        self.event_buttons_msg_content_pt4 = f"\n{unsubbed}"
        response = f"{self.event_buttons_msg_content_pt1} {self.event_buttons_msg_content_pt2} {self.event_buttons_msg_content_pt3} {self.event_buttons_msg_content_pt4}"
        return response

    # Get duration value in minutes
    def get_duration_minutes(self) -> int:
        return self.duration.total_seconds() // 60

    # Get timeout value in minutes
    def get_timeout_minutes(self) -> float:
        return self.timeout_counter / 2

    # Get start time string
    def get_start_time_string(self, index: int = 0) -> str:
        return f'{self.start_times[index].strftime("%A, %m/%d at %H:%M")} ET'

    # Get availability request string
    def get_availability_request_string(self) -> str:
        output = self.avail_msg_content_pt1
        output += get_time_str_from_minutes(self.get_duration_minutes())
        output += self.avail_msg_content_pt2
        output += get_time_str_from_minutes(self.get_timeout_minutes())
        output += self.avail_msg_content_pt3
        return output

    # All participants have responded
    def has_everyone_answered(self, latest_date=None) -> bool:
        if not latest_date:
            latest_date = datetime.now().astimezone().date()
        for participant in self.participants:
            if participant.subscribed and not participant.answered:
                return False
            if not participant.subscribed:
                continue
            if participant.availability:
                if participant.availability[-1].start_time.date() < latest_date:
                    return False
            else:
                return False
        return True

    # Request availability from all participants
    async def request_availability(self, reschedule: bool = False, rescheduler: Participant = None) -> None:
        self.avail_buttons = AvailabilityButtons(event=self)
        if not reschedule:
            self.avail_msg_content_pt3 += '\n\nThe event will be either created or cancelled within a minute after the last person responds.️'
        else:
            rescheduler.set_no_availability()
        response = self.get_availability_request_string()
        self.availability_message = await self.text_channel.send(content=response, view=self.avail_buttons)
        await self.update_responded_message()

    # Update the availability message to show duration changes and timeout countdown
    async def update_availability_message(self) -> None:
        if not self.availability_message:
            return
        try:
            await self.availability_message.edit(content=self.get_availability_request_string(), view=self.avail_buttons)
        except Exception as e:
            logger.exception(f'{self.name}: Failed to edit availability message in update: {e}')

    # Create or edit the responded message to show who still needs to respond to the availability request
    async def update_responded_message(self) -> None:
        if self.created:
            return
        latest_date = self.check_availabilities() if self.number_of_responded() > 1 else None
        if self.number_of_responded() == 1:
            for participant in self.participants:
                if participant.answered:
                    latest_date = participant.availability[-1].start_time.date()
                    break
        message_content = ''
        cur_date = datetime.now().astimezone().date()
        if latest_date and latest_date != cur_date:
            message_content = f'**Input up to latest availability date: {latest_date.month}/{latest_date.day}**\n'
        mentions = self.get_names_string(subscribed_only=True, unanswered_only=True, mention=True)
        message_content += f'Waiting for a response from: \n{mentions}'
        # Send new message
        if not self.responded_message:
            try:
                self.responded_message = await self.text_channel.send(content=message_content)
            except Exception as e:
                logger.exception(f'{self.name}: Error sending responded message: {e}')
            return
        # Edit existing message
        if not self.has_everyone_answered(latest_date):
            try:
                await self.responded_message.edit(content=message_content)
            except Exception as e:
                logger.exception(f'{self.name}: Error getting mentions string or editing responded message: {e}')
            return
        # Everyone has responded
        try:
            await self.responded_message.edit(content='Everyone has responded.')
        except Exception as e:
            logger.exception(f'{self.name}: Error editing responded message with "everyone has responded": {e}')

    @classmethod
    async def from_dict(cls, data):
        # Name
        event_name = data["name"]
        if event_name == '':
            raise Exception('Event has no name, disregarding event')
        else:
            logger.info(f'{event_name}: loading event')

        # Guild
        event_guild = client.get_guild(data["guild_id"])
        if event_guild:
            logger.info(f'{event_name}: guild found: {event_guild.id}')
        else:
            raise Exception(f'Could not find guild for {event_name}, disregarding event')

        # Text channel
        event_text_channel = event_guild.get_channel(data["text_channel_id"])
        if not event_text_channel:
            logger.info(f'{event_name}: no text channel found')
        else:
            logger.info(f'{event_name}: text channel found: {event_text_channel.id}')

        # Voice channel
        event_voice_channel = utils.get(event_guild.voice_channels, id=data["voice_channel_id"])
        if event_voice_channel:
            logger.info(f'{event_name}: voice channel found: {event_voice_channel.id}')
        else:
            raise Exception(f'Could not find voice channel for {event_name}, disregarding event')

        # Scheduler
        event_scheduler = event_guild.get_member(data['scheduler_id'])
        if event_scheduler:
            logger.info(f'{event_name}: found scheduler with id {event_scheduler.id}')
        else:
            logger.warning(f'{event_name}: failed to find scheduler')

        # Participants
        event_participants = [Participant.from_dict(event_guild, participant) for participant in data["participants"]]
        for participant in event_participants.copy():
            try:
                if participant is None:
                    event_participants.remove(participant)
            except Exception as e:
                logger.warning(f"Exception while adding participant: {e}")
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
        event_scheduled_events = []
        try:
            scheduled_event_ids = data["scheduled_event_ids"]
            for scheduled_event_id in scheduled_event_ids:
                for guild_scheduled_event in event_guild.scheduled_events:
                    if guild_scheduled_event.id == scheduled_event_id:
                        event_scheduled_events.append(guild_scheduled_event)
                        logger.info(f'{event_name}: found guild scheduled event: {guild_scheduled_event.id}')
                        break
        except Exception as e:
            logger.info(f'{event_name}: error getting guild scheduled events: {e}')

        # Changed
        event_changed = data["changed"]
        logger.info(f'{event_name}: changed: {event_changed}')

        # Start time
        try:
            event_start_times = [datetime.fromisoformat(start_time) for start_time in data["start_times"]]
            for event_start_time in event_start_times:
                logger.info(f'{event_name}: start time found: {event_start_time.strftime("%A, %m/%d/%Y %H%M")}')
        except Exception as e:
            event_start_time = []
            logger.info(f'{event_name}: no start times found: {e}')

        # Duration
        event_duration = timedelta(minutes=data["duration"])
        if event_duration:
            logger.info(f'{event_name}: duration found: {event_duration.total_seconds()//60}')
        else:
            logger.info(f'{event_name}: no duration found')

        # Unavailable
        event_unavailable = data["unavailable"]
        logger.info(f'{event_name}: unavailable: {event_unavailable}')

        # Multi-event
        try:
            event_multi_event = data["multi_event"]
        except Exception as e:
            logger.warning(f'Failed to read multi_event data: {e}')
            event_multi_event = False
        logger.info(f'{event_name}: multi_event: {event_multi_event}')

        # Timeout counter
        try:
            event_timeout_counter = data["timeout_counter"]
        except Exception as e:
            logger.warning(f'Failed to read timeout counter data: {e}')
            event_timeout_counter = EVENT_TIMEOUT
        logger.info(f'{event_name}: timeout_counter: {event_timeout_counter}')

        return cls(
            name=event_name,
            guild=event_guild,
            text_channel=event_text_channel,
            availability_message=event_availability_message,
            avail_buttons=event_avail_buttons,
            responded_message=event_responded_message,
            voice_channel=event_voice_channel,
            scheduler=event_scheduler,
            participants=event_participants,
            image_url=event_image_url,
            event_buttons_message=event_event_buttons_message,
            event_buttons=event_event_buttons,
            event_buttons_msg_content_pt1=event_event_buttons_msg_content_pt1,
            event_buttons_msg_content_pt2=event_event_buttons_msg_content_pt2,
            event_buttons_msg_content_pt3=event_event_buttons_msg_content_pt3,
            event_buttons_msg_content_pt4=event_event_buttons_msg_content_pt4,
            ready_to_create=event_ready_to_create,
            created=event_created,
            started=event_started,
            scheduled_events=event_scheduled_events,
            changed=event_changed,
            start_times=event_start_times,
            duration=event_duration,
            unavailable=event_unavailable,
            multi_event=event_multi_event,
            timeout_counter=event_timeout_counter
        )

    def to_dict(self) -> dict:
        try:
            availability_message_id = self.availability_message.id
        except Exception:
            availability_message_id = 0
        try:
            responded_message_id = self.responded_message.id
        except Exception:
            responded_message_id = 0
        try:
            scheduler_id = self.scheduler.id
        except Exception:
            scheduler_id = 0
        try:
            participants = [participant.to_dict() for participant in self.participants]
        except Exception as e:
            logger.warning(f'Failed getting participants dict list: {e}')
            participants = []
        try:
            event_buttons_message_id = self.event_buttons_message.id
        except Exception:
            event_buttons_message_id = 0
        try:
            scheduled_event_ids = [scheduled_event.id for scheduled_event in self.scheduled_events]
        except Exception as e:
            logger.warning(f'Failed getting scheduled event ids: {e}')
            scheduled_event_ids = []
        try:
            start_times = [start_time.isoformat() for start_time in self.start_times]
        except Exception as e:
            logger.warning(f'Failed getting start times: {e}')
            start_times = []
        return {
            'name': self.name,
            'guild_id': self.guild.id,
            'text_channel_id': self.text_channel.id,
            'availability_message_id': availability_message_id,
            'responded_message_id': responded_message_id,
            'voice_channel_id': self.voice_channel.id,
            'scheduler_id': scheduler_id,
            'participants': participants,
            'image_url': self.image_url,
            'event_buttons_message_id': event_buttons_message_id,
            'event_buttons_msg_content_pt1': self.event_buttons_msg_content_pt1,
            'event_buttons_msg_content_pt2': self.event_buttons_msg_content_pt2,
            'event_buttons_msg_content_pt3': self.event_buttons_msg_content_pt3,
            'event_buttons_msg_content_pt4': self.event_buttons_msg_content_pt4,
            'ready_to_create': self.ready_to_create,
            'created': self.created,
            'started': self.started,
            'scheduled_event_ids': scheduled_event_ids,
            'changed': self.changed,
            'start_times': start_times,
            'duration': self.get_duration_minutes(),
            'unavailable': self.unavailable,
            'multi_event': self.multi_event,
            'timeout_counter': self.timeout_counter
        }

    def __repr__(self) -> str:
        return f'{self.name}'


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
            logger.info(f'{self.event}: Received availability from {participant}')
            participant.set_specific_availability(avail_string, self.date.value)
            if participant.availability:
                participant.answered = True
            else:
                participant.answered = False
            response = f'**__Availability received for {self.event}!__**\n' + participant.get_availability_string()
            await interaction.response.send_message(response, ephemeral=True)
            self.event.changed = True
            cur_date = datetime.now().astimezone().date()
            for other_participant in self.event.participants:
                if other_participant != participant and other_participant.full_availability_flag:
                    for timeblock in participant.availability:
                        if timeblock.start_time.date() == cur_date and other_participant.availability[0].end_time < timeblock.end_time:
                            other_participant.availability[0].end_time = timeblock.end_time
            await self.event.update_responded_message()
            for timeblock in participant.availability:
                logger.info(f'\t{timeblock}')
        except Exception as e:
            try:
                await interaction.response.send_message(f'Error setting your availability: {e}')
            except Exception as e:
                logger.error(f'{self.event}: Failed sending interaction response: {e}')
            logger.exception(f'{self.event}: Error setting specific availability: {e}')

    async def on_error(self, interaction: Interaction, error: Exception) -> None:
        await interaction.response.send_message(f'Error getting availability: {error}', ephemeral=True)
        logger.exception(f'{self.event}: Error getting availability from {interaction.user.name} (AvailabilityModal): {error}')


class AvailabilityButtons(View):
    def __init__(self, event: Event) -> None:
        super().__init__(timeout=None)
        self.event = event
        self.respond_label = "Respond"
        self.full_label = "Full Availability (Today)"
        self.reuse_label = "Use Existing Availability"
        self.unsub_label = "Unsubscribe from Event"
        self.cancel_label = "Cancel Scheduling"
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
                am_title = f'Availability for {self.event}'
                am_title = am_title[:41] + '...'
                await interaction.response.send_modal(AvailabilityModal(event=self.event, title=am_title))
            except Exception as e:
                logger.exception(f'Error sending availability modal: {e}')
            persist.write(client.get_events_dict())
        button.callback = respond_button_callback
        self.add_item(button)
        return button

    # Set yourself as available for the rest of the day
    def add_full_button(self) -> Button:
        button = Button(label=self.full_label, style=ButtonStyle.blurple)

        async def full_button_callback(interaction: Interaction):
            self.event.ready_to_create = False
            participant = self.event.get_participant(interaction.user.name)
            if not participant.full_availability_flag:
                logger.info(f'{self.event}: {participant} selected full availability button')
                # Get last availability time that starts today
                end_time: datetime = None
                cur_date = datetime.now().astimezone().date()
                for other_participant in self.event.participants:
                    if other_participant != participant and other_participant.answered:
                        for timeblock in other_participant.availability:
                            if timeblock.start_time.date() == cur_date and not timeblock.end_time.date() == cur_date:
                                if not end_time:
                                    end_time = timeblock.end_time
                                elif end_time < timeblock.end_time:
                                    end_time = timeblock.end_time
                participant.set_full_availability(end_time=end_time)
                for timeblock in participant.availability:
                    logger.info(f'{self.event}: \t{timeblock}')
                participant.full_availability_flag = True
                participant.answered = True
                response = f"**Availability for {self.event.name}:**\n"
                response += participant.get_availability_string()
                await interaction.response.send_message(response, ephemeral=True)
            else:
                logger.info(f'{self.event}: {participant} deselected full availability')
                participant.set_no_availability()
                for timeblock in participant.availability:
                    logger.info(f'{self.event}: \t{timeblock}')
                participant.full_availability_flag = False
                if participant.subscribed:
                    participant.answered = False
                await interaction.response.send_message('Your availability has been cleared.', ephemeral=True)
            await self.event.update_responded_message()
            persist.write(client.get_events_dict())
        button.callback = full_button_callback
        self.add_item(button)
        return button

    # Reuse availability from another event
    def add_reuse_button(self) -> Button:
        button = Button(label=self.reuse_label, style=ButtonStyle.blurple)

        async def reuse_button_callback(interaction: Interaction):
            logger.info(f'{self.event}: Reuse button pressed by {interaction.user.name}')
            participant = self.event.get_participant(interaction.user.name)
            found_availabilities = self.event.get_other_availability(participant)
            if not found_availabilities:
                logger.info(f'{self.event}: \tNo existing availability found for {interaction.user.name}')
                await interaction.response.send_message('No existing availability found.', ephemeral=True)
                return
            logger.info(f'{self.event}: \tFound existing availability for {interaction.user.name}')
            if len(found_availabilities) == 1:
                participant.availability = found_availabilities[0].avail
                participant.answered = True
                response = f'**__Availability for {self.event}:__**\n'
                response += participant.get_availability_string()
                await interaction.response.send_message(response, ephemeral=True)
                await self.event.update_responded_message()
                persist.write(client.get_events_dict())
            else:
                await interaction.response.send_message('Select another event to grab your availability from.', view=ExistingAvailabilitiesSelectView(found_availabilities, participant), ephemeral=True)
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
                logger.info(f'{self.event}: {interaction.user.name} unsubscribed')
                participant.subscribed = False
                participant.answered = True
                await interaction.response.send_message(f'You have been unsubscribed from {self.event}.', ephemeral=True)
            else:
                logger.info(f'{self.event}: {interaction.user.name} resubscribed')
                participant.subscribed = True
                if not participant.availability:
                    participant.answered = False
                await interaction.response.send_message(f'You have been resubscribed to {self.event}.', ephemeral=True)
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
                participant.unavailable = True
                self.event.unavailable = True
                await interaction.response.send_message(f'{self.event} will be cancelled shortly unless you click the **Cancel** button again.', ephemeral=True)
                logger.info(f'{self.event}: {interaction.user.name} selected cancel')
            else:
                participant.unavailable = False
                self.event.unavailable = False
                await interaction.response.send_message(f'{self.event} will not be cancelled.', ephemeral=True)
                logger.info(f'{self.event}: {interaction.user.name} deselected cancel')
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
            logger.info(f'{self.event}: {interaction.user} started by button press')
            try:
                await self.event.scheduled_events[0].start(reason=f'Start button pressed by {interaction.user.name}.')
            except Exception as e:
                logger.exception(f'{self.event}: Failed to start event: {e}')
            try:
                self.event.start_times[0] = datetime.now().astimezone().replace(second=0, microsecond=0)
            except Exception as e:
                logger.warning(f'{self.event}: Error getting start time at button press: {e}')
                self.event.start_times.append(datetime.now().astimezone().replace(second=0, microsecond=0))
            self.event.add_user_as_participant(interaction.user)
            self.event.event_buttons_msg_content_pt2 = f'\n**Started at:** {datetime.now().astimezone().strftime("%H:%M")} ET'
            self.event.started = True
            self.start_button.style = ButtonStyle.green
            self.start_button.disabled = True
            self.end_button.disabled = False
            self.reschedule_button.disabled = True
            self.cancel_button.disabled = True
            # Offset all other events that share this location to start after the end of this event
            buffer_time = timedelta(minutes=0)
            other_events = []
            prev_event = None
            for other_event in client.events:
                if other_event != self.event and (other_event.voice_channel == self.event.voice_channel or other_event.shares_participants(self.event)):
                    for other_event_start_time in other_event.start_times:
                        if other_event_start_time < (self.event.start_times[0] + self.event.duration + buffer_time):
                            other_event_start_time = (self.event.start_times[0] + self.event.duration + buffer_time)
                    other_events.append(other_event)
            # Offset the events from each other to prevent stack smashing
            for other_event in other_events:
                if prev_event:
                    for other_event_start_time in other_event.start_times:
                        if other_event_start_time < (prev_event.start_times[0] + prev_event.duration + buffer_time):
                            other_event_start_time = (prev_event.start_times[0] + prev_event.duration + buffer_time)
                prev_event = other_event
            # Interaction response
            try:
                await interaction.response.edit_message(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt4}', view=self.event.event_buttons)
            except Exception as e:
                logger.error(f'{self.event}: Error responding to START button interaction: {e}')
            # Disable start buttons of events scheduled for the same channel
            for event in client.events:
                if event == self.event or not event.created or event.voice_channel != self.event.voice_channel:
                    continue
                event.event_buttons.start_button.disabled = True
                try:
                    await event.event_buttons_message.edit(view=event.event_buttons)
                    logger.info(f'{self.event}: Disabled start button for event with same location: {event.name}')
                except Exception as e:
                    logger.error(f'{self.event}: Failed to disable start button for {event}: {e}')
            persist.write(client.get_events_dict())
        self.start_button.callback = start_button_callback
        self.add_item(self.start_button)

    # End the event
    def add_end_button(self) -> None:
        self.end_button.disabled = True

        async def end_button_callback(interaction: Interaction):
            logger.info(f'{self.event}: {interaction.user} ended by button press')
            # Delete scheduled event
            try:
                await self.event.scheduled_events[0].delete(reason=f'End button pressed by {interaction.user.name}.')
            except Exception as e:
                logger.error(f'Error in event control end button callback while ending scheduled event: {e}')
            # Update event buttons message
            end_time: datetime = datetime.now().astimezone().replace(second=0, microsecond=0)
            content = self.event.get_event_buttons_message_string(end_time)
            try:
                await self.event.event_buttons_message.edit(content=content, view=None)
            except Exception as e:
                logger.error(f'Error in event control end button callback while editing event buttons message: {e}')
            # Remove start_time and scheduled event from lists
            try:
                await self.event.prep_next_scheduled_event()
            except Exception as e:
                logger.error(f'Error in event control end button callback while prepping next scheduled event: {e}')
            logger.info(f'{self.event}: Ended event')
            # Interaction response, remove buttons
            try:
                await interaction.response.edit_message(view=None)
            except Exception as e:
                logger.exception(f'Error responding to END button interaction: {e}')
            # Re-enable start buttons of appropriate events
            for event in client.events:
                if event == self.event or not event.created or event.voice_channel != self.event.voice_channel:
                    continue
                try:
                    event.event_buttons.start_button.disabled = False
                    await event.event_buttons_message.edit(view=event.event_buttons)
                    logger.info(f'{self.event}: Re-enabled start button for event with same location: {event.name}')
                except Exception as e:
                    logger.error(f'{self.event}: Failed to re-enable start button for {event}: {e}')
            if not self.event.start_times:
                client.events.remove(self.event)
                logger.info(f"{self.event}: last event ended, removed from memory")
            else:
                logger.info(f"{self.event}: next event starts at {self.event.start_times[0]}")
            persist.write(client.get_events_dict())
        self.end_button.callback = end_button_callback
        self.add_item(self.end_button)

    # Reschedule the event
    def add_reschedule_button(self) -> None:
        async def reschedule_button_callback(interaction: Interaction):
            logger.info(f'{self.event}: {interaction.user} rescheduled by button press')
            await interaction.response.defer(ephemeral=True)
            self.event.add_user_as_participant(interaction.user)
            try:
                await self.event.scheduled_events[0].delete(reason=f'Reschedule button pressed by {interaction.user.name}.')
            except Exception as e:
                logger.error(f"{self.event}: Error cancelling guild event to reschedule: {e}")
            try:
                self.event.scheduled_events.remove(self.event.scheduled_events[0])
            except Exception as e:
                logger.error(f"{self.event}: Error removing guild event from list: {e}")
            try:
                self.event.start_times.remove(self.event.start_times[0])
            except Exception as e:
                logger.error(f"{self.event}: Error removing start time from list: {e}")
            self.event.created = False
            self.event.five_minute_warning_flag = False
            self.event.event_buttons_msg_content_pt2 = f'\n**Rescheduled at:** {datetime.now().astimezone().strftime("%H:%M")} ET'
            try:
                await self.event.event_buttons_message.edit(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt4}', view=None)
            except Exception as e:
                logger.error(f"{self.event}: Error editing event buttons message during reschedule: {e}")
            self.event.event_buttons_message = None
            try:
                participant = self.event.get_participant(interaction.user.name)
                for p in self.event.participants:
                    p.confirm_answered(duration=self.event.duration)
                await self.event.request_availability(reschedule=True, rescheduler=participant)
                await interaction.followup.send(f"Event rescheduling started for {self.event.name}.", ephemeral=True)
            except Exception as e:
                logger.error(f"{self.event}: Error with RESCHEDULE button requesting availability: {e}")
            persist.write(client.get_events_dict())
        self.reschedule_button.callback = reschedule_button_callback
        self.add_item(self.reschedule_button)

    # Cancel the event
    def add_cancel_button(self) -> None:
        async def cancel_button_callback(interaction: Interaction):
            try:
                await self.event.event_buttons_message.delete()
            except Exception as e:
                logger.error(f'Error in cancel button callback while deleting event buttons message: {e}')
            try:
                await self.event.text_channel.send(f'{self.event.get_names_string(subscribed_only=True, mention=True)}\n{interaction.user.name} cancelled {self.event}.')
            except Exception as e:
                logger.error(f'Error in cancel button callback while sending text channel message: {e}')
            try:
                await self.event.scheduled_events[0].delete(reason=f'Cancel button pressed by {interaction.user.name}.')
            except Exception as e:
                logger.error(f'Error in cancel button callback while deleting scheduled event: {e}')
            try:
                await self.event.prep_next_scheduled_event()
            except Exception as e:
                logger.error(f'Error in cancel button callback while prepping next scheduled event: {e}')
            logger.info(f'{self.event}: {interaction.user} cancelled by button press')
            client.events.remove(self.event)
            persist.write(client.get_events_dict())
        self.cancel_button.callback = cancel_button_callback
        self.add_item(self.cancel_button)


# Dropdown of existing guild scheduled events
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
        selected_guild_event_id = int(self.values[0])
        selected_guild_event: ScheduledEvent = self.guild.get_scheduled_event(selected_guild_event_id)
        if selected_guild_event:
            await interaction.response.defer(ephemeral=True)
            logger.info(f'{interaction.user.name} attached to {selected_guild_event.name}')
            existingEvent = False
            # Event exists, adding guild event to that event
            for it_event in client.events:
                if selected_guild_event.name == it_event.name and selected_guild_event.location == it_event.voice_channel:
                    existingEvent = True
                    it_event.created = True
                    it_event.text_channel = interaction.channel
                    event = it_event
                    break
            # Event does not exist
            if not existingEvent:
                participants = get_participants_from_interaction(interaction)
                for participant in participants:
                    participant.answered = True
                start_times = [selected_guild_event.start_time.astimezone()]
                event = Event(name=selected_guild_event.name,
                              voice_channel=selected_guild_event.location,
                              guild=self.guild,
                              text_channel=interaction.channel,
                              scheduler=self.guild.get_member(interaction.user.id),
                              participants=participants,
                              start_times=start_times,
                              created=True)
                client.events.append(event)
            for guild_event in self.guild.scheduled_events:
                if guild_event.name == selected_guild_event.name and guild_event.location == selected_guild_event.location:
                    event.start_times.append(guild_event.start_time.astimezone())
                    event.scheduled_events.append(guild_event)
            response = event.get_event_buttons_message_string()
            event.event_buttons = EventButtons(event)
            event.event_buttons_message = await event.text_channel.send(content=response, view=event.event_buttons)
            await interaction.followup.send('Success!', ephemeral=True)
            persist.write(client.get_events_dict())
        else:
            await interaction.response.send_message('Error getting guild scheduled event.')
            logger.exception(f'Error getting guild scheduled event selected by {interaction.user.name}')


# View to house the existing guild events dropdown
class ExistingGuildEventsSelectView(View):
    def __init__(self, guild: Guild):
        super().__init__()
        self.add_item(ExistingGuildEventsSelect(guild))


# Class for ease of tying availabilities to events for the Select dropdown
class EventAvailability:
    def __init__(self, event: Event, avail: list, full_flag: bool):
        self.event = event
        self.avail = avail
        self.full_flag = full_flag


# Dropdown to select previous availability options
class ExistingAvailabilitiesSelect(Select):
    def __init__(self, event_avails: list, participant: Participant):
        self.event_avails = event_avails
        self.participant = participant
        options = [
            SelectOption(label=event_avail.event.name, value=event_avail.event.name)
            for event_avail in self.event_avails
        ]
        super().__init__(placeholder='Event Availabilities', options=options)

    # Select an availability to attach
    async def callback(self, interaction: Interaction):
        logger.info(f'{interaction.user.name} selected an event to get their availability from')
        response = "**__Failed to get your availability.__**"
        for event_avail in self.event_avails:
            if event_avail.event.name == self.values[0]:
                self.participant.availability = event_avail.avail
                self.participant.full_availability_flag = event_avail.full_flag
                self.participant.answered = True
                response = f"**__Availability for {event_avail.event.name}:__**\n"
                response += self.participant.get_availability_string()
                await event_avail.event.update_responded_message()
                break
        await interaction.response.send_message(content=response, ephemeral=True)


# View to house the previous availability dropdown
class ExistingAvailabilitiesSelectView(View):
    def __init__(self, event_avails: list, participant: Participant):
        super().__init__()
        self.add_item(ExistingAvailabilitiesSelect(event_avails, participant))


# Wrapper
def get_participants_from_interaction(interaction: Interaction,
                                      include_exclude: INCLUDE_EXCLUDE = None,
                                      usernames: str = None,
                                      roles: str = None) -> list:
    return get_participants_from_channel(guild=interaction.guild,
                                         channel=interaction.channel,
                                         user=interaction.user,
                                         include_exclude=include_exclude,
                                         usernames=usernames,
                                         roles=roles)

# Put participants into a list
def get_participants_from_channel(guild: Guild,
                                  channel,
                                  user: User = None,
                                  include_exclude: INCLUDE_EXCLUDE = None,
                                  usernames: str = None,
                                  roles: str = None):
    participants = []
    # Add the scheduler/creator as a participant
    if user is not None:
        member = guild.get_member(user.id)
        if not member.bot:
            participants.append(Participant(member=member))

    # Add users meeting role criteria
    if roles and roles != '':
        logger.info("Parsing roles")
        try:
            roles = roles.split(',')
            roles = [role.strip() for role in roles]
            roles = [utils.find(lambda r: r.name.lower() == role.lower(), guild.roles) for role in roles]
        except Exception as e:
            raise Exception(f'Failed to parse role(s): {e}')
        for member in channel.members:
            if member.bot:
                continue
            if user is not None:
                if member.name == user.name:
                    continue
            found_role = False
            for role in roles:
                if role in member.roles:
                    found_role = True
                    break
            if include_exclude == INCLUDE and found_role:
                participants.append(Participant(member=member))
            elif include_exclude == EXCLUDE and not found_role:
                participants.append(Participant(member=member))
        return participants

    # Add users meeting username criteria
    if type(usernames) is str:
        usernames = usernames.split(',')
    if usernames and type(usernames) is not list:
        raise Exception(f'Received incompatible usernames variable type: {type(usernames)}')
    if usernames and usernames != '':
        logger.info("Adding specific members")
        logger.debug("Received unsubscribed user names/ids")
        for username in usernames:
            logger.debug(f"\t{username.strip()}")
        try:
            usernames = [username.strip() for username in usernames]
        except Exception as e:
            raise Exception(f'Failed to parse username(s): {e}')
        for member in channel.members:
            if member.bot:
                continue
            if user is not None:
                if member.name == user.name:
                    continue
            if include_exclude == INCLUDE and (member.name in usernames or str(member.id) in usernames):
                participants.append(Participant(member=member))
            elif include_exclude == EXCLUDE and member.name not in usernames and str(member.id) not in usernames:
                participants.append(Participant(member=member))
        return participants

    # Add all users in the channel
    logger.info("Adding all members in channel")
    for member in channel.members:
        if member.bot:
            continue
        if user is not None:
            if member.id == user.id:
                continue
        participants.append(Participant(member=member))
    return participants


# Check if an event is active in the given location
def location_has_active_event(location: VoiceChannel) -> bool:
    for event in client.events:
        if event.voice_channel == location and event.started:
            return True
    return False


# Return first start time of event for sorting
def first_start_time(event):
    time = None
    try:
        time = event.start_times[0]
    except Exception as e:
        logger.error(f'Failed to access first start time: {e}')
    return time


# Sort created events and then append uncreated events
def sort_events() -> None:
    new_events = []
    for event in client.events:
        if event.created and event.start_times:
            new_events.append(event)
    if new_events:
        try:
            new_events.sort(key=first_start_time)
        except Exception as e:
            logger.error(f'Failed to sort created events: {e}')
    for event in client.events:
        if not event.created and not event.start_times:
            new_events.append(event)
    client.events = new_events
    persist.write(client.get_events_dict())


# Decrement event timeout counters and remove events that hit 0
async def clear_timed_out_events() -> None:
    new_events = []
    for event in client.events:
        if event.created:
            new_events.append(event)
            continue
        event.timeout_counter -= 1
        if event.timeout_counter > 0:
            new_events.append(event)
        else:
            notification_message = f'{event.get_names_string(subscribed_only=True, mention=True)}\nScheduling for **{event.name}** has timed out and has been cancelled.\n'
            if event.text_channel:
                await event.text_channel.send(notification_message)
            else:
                for participant in event.participants:
                    async with participant.msg_lock:
                        await participant.member.send(notification_message)
            logger.info(f'{event.name} has timed out and has been cancelled.')
            try:
                await event.availability_message.delete()
            except NotFound:
                logger.warn(f"{event}: Availability message not found")
            except Exception as e:
                logger.error(f"{event}: Couldn't delete availability_message: {e}")
            event.availability_message = None
            try:
                await event.responded_message.delete()
            except NotFound:
                logger.warn(f"{event}: Responded message not found")
            except Exception as e:
                logger.error(f"{event}: Couldn't delete responded_message: {e}")
            event.responded_message = None
    client.events = new_events
    persist.write(client.get_events_dict())


@client.event
async def on_ready():
    logger.info(f'{client.user} has connected to Discord!')
    await client.retrieve_events()
    if not client.server_is_running:
        await client.start_server()
    if not update.is_running():
        update.start()
    logger.info(f'{client.user} is ready!')


@client.event
async def on_message(message: Message):
    # Event image
    if not message.guild and message.attachments and message.content:
        msg_content = message.content.lower()
        for event in client.events:
            if event.name.lower() in msg_content:
                if event.created:
                    try:
                        image_bytes = await message.attachments[0].read()
                        for scheduled_event in event.scheduled_events:
                            await scheduled_event.edit(image=image_bytes)
                        await message.channel.send(f'Added your image to {event.name}.', reference=message)
                        logger.info(f'{event}: {message.author.name} added an image')
                    except Exception as e:
                        await message.channel.send(f'Failed to add your image to {event.name}.\nError: {e}', reference=message)
                        logger.warning(f'{event}: Error adding image from {message.author.name}: {e}')
                else:
                    event.image_url = message.attachments[0].url
                    await message.channel.send('Attached image url to event object. Will try setting it when the event is made.', reference=message)
                return
        await message.channel.send(f'Could not find event {msg_content}.\n\n__Existing events:__\n{", ".join([event.name for event in client.events])}', reference=message)
        return

    # Owner syncs commands
    if message.author.id == OWNER_ID and 'scheduler: sync' in message.content:
        await client.tree.sync()
        logger.info(f'User {message.author.name} synced commands')
        await message.channel.send(content='Synced', reference=message)

    # Owner requests to see all events
    if message.author.id == OWNER_ID and 'scheduler: list all' in message.content:
        logger.info(f"User {message.author.name} listed all events")
        embed = Embed(title="All events", color=Color.blue())
        for event in client.events:
            eventStatus = event.get_scheduling_status()
            embed.add_field(name=event.name, value=eventStatus, inline=True)
        await message.channel.send(embed=embed, reference=message)

    # Owner unsubscribes another user
    if message.author.id == OWNER_ID and 'scheduler: unsubscribe' in message.content:
        foundEvent = False
        for event in client.events:
            if event.name in message.content.split('from')[1].strip():
                foundEvent = True
                try:
                    id = message.content.split('unsubscribe')[1].split('from')[0].strip()
                    id = int(id)
                    found = False
                    for participant in event.participants:
                        if participant.member.id == id:
                            found = True
                            participant.subscribed = False
                            if participant.member.nick:
                                await message.channel.send(f"Unsubscribed {participant.member.nick}", reference=message)
                            else:
                                await message.channel.send(f"Unsubscribed {participant.member.name}", reference=message)
                            await event.update_responded_message()
                            break
                    if not found:
                        await message.channel.send(f"{event.name}: participant {participant.name} not found", reference=message)
                except Exception as e:
                    await message.channel.send("Invalid ID provided", reference=message)
                    logger.warn(f"Invalid unsubscribe other user format from owner: {e}")
                break
        if not foundEvent:
            await message.content.channel.send("No event found", reference=message)


@client.tree.command(name='create', description='Create an event.')
@app_commands.describe(event_name='Name for the event.')
@app_commands.describe(voice_channel='Voice channel for the event.')
@app_commands.describe(start_time='Start time (in Eastern Time or ISO format) for the event.')
@app_commands.describe(image_url='URL to an image for the event.')
@app_commands.describe(include_exclude='Whether to include or exclude users with the designated role.')
@app_commands.describe(usernames='Comma separated usernames of users to include/exclude.')
@app_commands.describe(roles='Comma separated roles of users to include/exclude.')
@app_commands.describe(duration='Event duration in minutes (30 minutes default).')
async def create_command(interaction: Interaction, event_name: str, voice_channel: VoiceChannel, start_time: str, image_url: str = None, include_exclude: INCLUDE_EXCLUDE = INCLUDE, usernames: str = None, roles: str = None, duration: int = 30):
    logger.info(f'{event_name}: Received event creation request from {interaction.user.name}')
    if not interaction.guild.voice_channels:
        raise Exception('The server must have at least one voice channel to schedule an event.')

    if event_name in [event.name for event in client.events]:
        await interaction.response.send_message(f'Sorry, I already have an event called {event_name}. Please choose a different name.', ephemeral=True)
        return

    # Parse start time
    try:
        start_time_obj = datetime.fromisoformat(start_time)
    except Exception as e:
        logger.info(f"Start time was not in iso format: {e}")
        start_time = start_time.strip()
        start_time = start_time.replace(':', '')
        if len(start_time) == 1 or len(start_time) == 2:
            start_time = start_time + '00'
        if len(start_time) == 3:
            start_time = '0' + start_time
        elif len(start_time) != 4:
            await interaction.response.send_message('Invalid start time format. Examples: "1630" or "00:30"')
        hour = int(start_time[:2])
        minute = int(start_time[2:])
        start_time_obj = datetime.now().astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0)
    while start_time_obj <= datetime.now().astimezone().replace(second=0, microsecond=0):
        start_time_obj += timedelta(days=1)

    scheduler = interaction.guild.get_member(interaction.user.id)
    participants = get_participants_from_interaction(interaction, include_exclude, usernames, roles)
    for participant in participants:
        participant.answered = True

    # Make event
    duration = timedelta(minutes=duration)
    start_times = [start_time_obj]
    event = Event(name=event_name,
                  voice_channel=voice_channel,
                  scheduler=scheduler,
                  participants=participants,
                  guild=interaction.guild,
                  text_channel=interaction.channel,
                  image_url=image_url,
                  duration=duration,
                  start_times=start_times)
    client.events.append(event)
    await event.make_scheduled_events()

    try:
        await interaction.response.send_message(content='Event created!', ephemeral=True)
    except Exception as e:
        logger.error(f'Error sending interaction response for create command: {e}')
    try:
        response = event.get_event_buttons_message_string()
        event.event_buttons = EventButtons(event)
        event.event_buttons_message = await event.text_channel.send(content=response, view=event.event_buttons)
    except Exception as e:
        logger.error(f'Error making event buttons or sending event buttons message in create command: {e}')
    persist.write(client.get_events_dict())


@client.tree.command(name='schedule', description='Schedule an event.')
@app_commands.describe(event_name='Name for the event.')
@app_commands.describe(voice_channel='Voice channel for the event.')
@app_commands.describe(image_url="URL to an image for the event.")
@app_commands.describe(include_exclude='Whether to include or exclude users specified.')
@app_commands.describe(usernames='Comma separated usernames of users to include/exclude.')
@app_commands.describe(roles='Comma separated roles of users to include/exclude.')
@app_commands.describe(duration="Event duration in minutes (30 minutes default).")
@app_commands.describe(multi_event='Create an event on each date that everyone is available.')
async def schedule_command(interaction: Interaction,
                           event_name: str,
                           voice_channel: VoiceChannel,
                           image_url: str = None,
                           include_exclude: INCLUDE_EXCLUDE = INCLUDE,
                           usernames: str = None,
                           roles: str = None,
                           duration: int = 30,
                           multi_event: bool = False):
    logger.info(f'{event_name}: Received event schedule request from {interaction.user.name}')
    content = ""
    ephemeral = True
    try:
        content, ephemeral = await schedule(eventName=event_name,
                                            guild=interaction.guild,
                                            textChannel=interaction.channel,
                                            voiceChannel=voice_channel,
                                            schedulerId=interaction.user.id,
                                            imageUrl=image_url,
                                            includeExclude=include_exclude,
                                            usernames=usernames,
                                            roles=roles,
                                            duration=duration,
                                            multiEvent=multi_event)
    except Exception as e:
        content = f"Failed to schedule event: {e}"
        logger.error(content)
    await interaction.response.send_message(content=content, ephemeral=ephemeral)


async def schedule(eventName: str,
                   guild: Guild,
                   textChannel,
                   voiceChannel: VoiceChannel,
                   schedulerId: int = 0,
                   imageUrl: str = None,
                   includeExclude: INCLUDE_EXCLUDE = INCLUDE,
                   usernames: str = None,
                   roles: str = None,
                   duration: int = 30,
                   multiEvent: bool = False):
    logger.info(f"{eventName}: Scheduling event...")
    if not guild.voice_channels:
        logger.info(f"{eventName}: Scheduling cancelled due to no voice channel in guild")
        content = "The server must have at least one voice channel to schedule an event."
        ephemeral = True
        return content, ephemeral

    scheduler = None
    if schedulerId != 0:
        scheduler = guild.get_member(schedulerId)
    if scheduler is None:
        scheduler = guild.members[0]

    if eventName in [event.name for event in client.events]:
        logger.info(f"{eventName}: Scheduling cancelled due to existing name")
        content = f"Sorry, I already have an event called {eventName}. Please choose a different name."
        ephemeral = True
        return content, ephemeral

    # Generate participants list
    try:
        logger.debug(f"{eventName}: Received raw unsubscribed user IDs: {usernames}")
        participants = get_participants_from_channel(guild=guild,
                                                     channel=textChannel,
                                                     user=scheduler,
                                                     include_exclude=includeExclude,
                                                     usernames=usernames,
                                                     roles=roles)
        logger.debug(f"{eventName}: Got participants")
    except Exception as e:
        logger.error(f"{eventName}: Error getting participants: {e}")
        content = f"Failed to generate participants list: {e}"
        ephemeral = True
        return content, ephemeral

    # Make event object
    try:
        logger.debug(f"{eventName}: SchedulerId: {schedulerId}")
        logger.debug(f"{eventName}: Scheduler.name: {scheduler.name}")
        duration = timedelta(minutes=duration)
        event = Event(name=eventName,
                      voice_channel=voiceChannel,
                      scheduler=scheduler,
                      participants=participants,
                      guild=guild,
                      text_channel=textChannel,
                      image_url=imageUrl,
                      duration=duration,
                      multi_event=multiEvent)
        client.events.append(event)
        logger.info(f"{eventName}: Created and saved event object")
    except Exception as e:
        logger.error(f'Error making event object: {e}')
        content = f"Failed to make event object: {e}"
        ephemeral = True
        return content, ephemeral

    # Request availability and make participant response tracker message
    try:
        logger.info(f"{eventName}: Requesting availability")
        await event.request_availability()
    except Exception as e:
        logger.exception(f'Error requesting availability: {e}')
    persist.write(client.get_events_dict())
    content = f"Event scheduling started for {eventName}."
    ephemeral = True
    return content, ephemeral


@client.tree.command(name='attach', description='Create an event message for an existing guild event.')
async def attach_command(interaction: Interaction):
    logger.info(f'Received attach command request from {interaction.user.name}')
    await interaction.response.send_message('Select an existing guild event from the dropdown menu.', view=ExistingGuildEventsSelectView(interaction.guild), ephemeral=True)


@client.tree.command(name='listevents', description='List all events in this server.')
async def listevents_command(interaction: Interaction):
    logger.info(f"Received list events command request from {interaction.user.name}")
    foundEvents = False
    content = ""
    embed = Embed(title=f"All events in {interaction.guild.name}", color=Color.blue())
    for event in client.events:
        if event.guild == interaction.guild:
            foundEvents = True
            eventStatus = event.get_scheduling_status()
            embed.add_field(name=event.name, value=eventStatus, inline=True)
    if foundEvents:
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        content = "No events found for this server."
        await interaction.response.send_message(content=content, ephemeral=True)


@tasks.loop(seconds=30)
async def update():
    sort_events()
    await clear_timed_out_events()

    # Participant availability checks
    for event in client.events:
        # If availability expires before the event is created, mark the participant as unanswered
        if not event.created:
            await event.update_availability_message()
            for participant in event.participants:
                participant.confirm_answered()
            await event.update_responded_message()
        # Remove this event from each participant's other availabilities
        else:
            for participant in event.participants:
                for other_event in client.events:
                    if other_event != event and not other_event.created:
                        for other_participant in other_event.participants:
                            if other_participant.member.id == participant.member.id:
                                other_participant.remove_availability_for_event(event_start_times=event.start_times, event_duration=event.duration)
                                break

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
                except NotFound as e:
                    logger.warn(f"{event}: Unavailable delete: Availability message not found: {e}")
                except Exception as e:
                    logger.error(f"{event}: Unavailable delete: Error deleting availability message: {e}")
                event.availability_message = None
                try:
                    await event.responded_message.delete()
                except NotFound:
                    logger.warn(f"{event}: Unavailable delete: Responded message not found")
                except Exception as e:
                    logger.error(f"{event}: Unavailable delete: Error while deleting responded message: {e}")
                event.responded_message = None
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
                            await participant.member.send(notification_message)
                logger.info(f'{event}: Participant(s) lacked (common) availability, removed event from memory')
                client.events.remove(event)
                del event
                persist.write(client.get_events_dict())
            except Exception as e:
                logger.error(f'Error invalidating and deleting event: {e}')
                continue
            continue

        # Countdown to start + 5 minute warning
        if event.created and not event.started:
            # Countdown
            try:
                if datetime.now().astimezone() < event.start_times[0]:
                    time_until_start: timedelta = event.start_times[0] - datetime.now().astimezone()
                    event.mins_until_start = int(time_until_start.total_seconds() // 60) + 1
                elif datetime.now().astimezone().replace(second=0, microsecond=0) == event.start_times[0]:
                    event.mins_until_start = 0
                else:
                    time_until_start: timedelta = datetime.now().astimezone() - event.start_times[0]
                    event.mins_until_start = int(time_until_start.total_seconds() // 60) + 1
                # Event start time is in the past
                if event.start_times[0] < datetime.now().astimezone().replace(second=0, microsecond=0):
                    event.event_buttons_msg_content_pt2 = '\n**Overdue by:**'
                    hrs_mins_overdue_start_string = get_time_str_from_minutes(event.mins_until_start)
                    response = f'{event.event_buttons_msg_content_pt1} {event.event_buttons_msg_content_pt2} {hrs_mins_overdue_start_string} {event.event_buttons_msg_content_pt4}'
                # It is event start time
                elif event.start_times[0] == datetime.now().astimezone().replace(second=0, microsecond=0):
                    event.event_buttons_msg_content_pt2 = '\n**Starting now**'
                    response = f'{event.event_buttons_msg_content_pt1} {event.event_buttons_msg_content_pt2} {event.event_buttons_msg_content_pt4}'
                # Event start time is in the future
                else:
                    event.event_buttons_msg_content_pt2 = '\n**Starts in:**'
                    hrs_mins_until_start_string = get_time_str_from_minutes(event.mins_until_start)
                    response = f'{event.event_buttons_msg_content_pt1} {event.event_buttons_msg_content_pt2} {hrs_mins_until_start_string} {event.event_buttons_msg_content_pt4}'
                await event.event_buttons_message.edit(content=response, view=event.event_buttons)
            except Exception as e:
                logger.error(f'{event}: Error counting down: {e}')
                continue

            # Send 5 minute warning
            try:
                if datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(minutes=5) == event.start_times[0] and event.scheduled_events[0].status == EventStatus.scheduled and not event.started:
                    if not event.five_minute_warning_flag:
                        event.five_minute_warning_flag = True
                        if event.text_channel:
                            try:
                                message = f'{event.get_names_string(subscribed_only=True, mention=True)}'
                                message += f'\n**5 minute warning!** {event.name} is scheduled to start in 5 minutes.'
                                await event.text_channel.send(content=message, reference=event.event_buttons_message)
                            except Exception as e:
                                logger.error(f'Error sending 5 minute nudge: {e}')
                                continue
                        else:
                            for participant in event.participants:
                                async with participant.msg_lock:
                                    message = f'**5 minute warning!** {event.name} is scheduled to start in 5 minutes.'
                                    await participant.member.send(content=message, reference=event.event_buttons_message)
            except Exception as e:
                logger.error(f'{event}: Error sending 5 minute warning: {e}')
            continue

        # Skip the rest of update() for this event if it is created or if we are waiting for answers
        if event.created or not event.has_everyone_answered():
            continue

        # Delete availability request message
        try:
            await event.availability_message.delete()
        except Exception as e:
            logger.error(f'{event}: Error disabling availability buttons: {e}')
        # Compare availabilities
        try:
            event.compare_availabilities()
        except Exception as e:
            logger.error(f'{event}: Error comparing availabilities: {e}')
            continue

        # Cancel the event if no common availability was found
        if not event.ready_to_create:
            try:
                logger.info(f'{event}: No common availability found')
                if event.text_channel:
                    await event.text_channel.send(f'No common availability was found. Scheduling for {event.name} has been cancelled.')
                else:
                    for participant in event.participants:
                        async with participant.msg_lock:
                            await participant.member.send(f'No common availability was found. Scheduling for {event.name} has been cancelled.')
                client.events.remove(event)
                del event
            except Exception as e:
                logger.error(f'{event}: Error messaging participants: {e}')
            continue
        # Create the event if it is ready to create
        else:
            # If start time is in the past, start it after the preset delay
            cur_time = datetime.now().astimezone().replace(second=0, microsecond=0)
            if event.start_times[0] <= cur_time:
                logger.warning(f'{event}: Tried to create event in the past! Moving to {START_TIME_DELAY} minutes from now.')
                event.start_times[0] = cur_time + timedelta(minutes=START_TIME_DELAY)
                event.ready_to_create = False
                continue

            # If there is an active event in the same location or sharing
            # participants, offset the start time to after the event
            prev_event = None
            for other_event in client.events:
                if other_event != event and (other_event.voice_channel == event.voice_channel or other_event.shares_participants(event)) and other_event.started:
                    if not prev_event:
                        prev_event = other_event
                        continue
                    if (prev_event.start_times[0] + prev_event.duration + event.duration) > other_event.start_times[0]:
                        prev_event = other_event
                        continue
                    if event.start_times[0] < (prev_event.start_times[0] + prev_event.duration):
                        event.start_times[0] = (prev_event.start_times[0] + prev_event.duration)
                    break

            # Create event
            try:
                await event.make_scheduled_events()
            except Exception as e:
                logger.error(f'{event}: Error creating scheduled event: {e}')
                continue

            # Go through created events and remove availability during the event time of all shared participants
            for other_event in client.events:
                for participant in other_event.participants:
                    participant.remove_availability_for_event(event_start_times=event.start_times, event_duration=event.duration)

            # Calculate time until start
            try:
                response = event.get_event_buttons_message_string()
                try:
                    await event.responded_message.delete()
                except NotFound:
                    logger.warn("Creation delete: Responded message not found")
                except Exception as e:
                    logger.error(f"Creation delete: Failed to delete responded message: {e}")
                event.responded_message = None
                event.event_buttons = EventButtons(event)
                event.event_buttons_message = await event.text_channel.send(content=response, view=event.event_buttons)
            except Exception as e:
                logger.error(f'{event}: Error sending event created notification with buttons: {e}')
                continue

            # If there is an active event in the same location, disable the start button
            if location_has_active_event(event.voice_channel):
                event.event_buttons.start_button.disabled = True
    persist.write(client.get_events_dict())

client.run(DISCORD_TOKEN)
