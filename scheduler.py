'''Written by Cael Shoop.'''

import os
import time
import logging
import asyncio
import aiohttp
from typing import Literal
from dotenv import load_dotenv
from datetime import datetime, timedelta
from discord import (app_commands, Interaction, Intents, Client, Embed, Color, Activity,
                     ButtonStyle, EventStatus, EntityType, TextChannel, File, ActivityType,
                     VoiceChannel, Message, SelectOption, ScheduledEvent, Member,
                     Guild, PrivacyLevel, User, utils, NotFound, HTTPException)
from discord.ui import View, Button, Modal, TextInput, Select
from discord.ext import tasks

from persistence import Persistence
from participant import Participant, TimeBlock, HOURS_PAST_MIDNIGHT_CUTOFF
from server import Server

# .env
load_dotenv()

# Logger setup
logger = logging.getLogger("Event Scheduler")
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter(fmt='[Scheduler] [%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

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
UPDATES_PER_MINUTE: int = 2
MINUTES_PER_HOUR: int = 60
HOURS_PER_DAY: int = 24
EVENT_TIMEOUT_DAYS: int = 3
EVENT_TIMEOUT: int = UPDATES_PER_MINUTE * MINUTES_PER_HOUR * HOURS_PER_DAY * EVENT_TIMEOUT_DAYS

RESEND_INTERVAL_HOURS: int = 23
RESEND_INTERVAL: int = UPDATES_PER_MINUTE * MINUTES_PER_HOUR * RESEND_INTERVAL_HOURS

OFFSET = EVENT_TIMEOUT % RESEND_INTERVAL


def save() -> None:
    """Saves the bot's status by writing the client's events to a file."""
    persist.write(client.get_events_dict())


def get_time_str_from_minutes(minutes: int) -> str:
    """Makes a formatted string including weeks, days, hours, and minutes.

    Arguments
    ----------
    minutes: :class:`int`
        The number of minutes to format.
    """
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
    """Adds 0 if a digit string is < 10.

    Arguments
    ----------
    digit_string: :class`str`
        The digit string that may need a 0 inserted at the beginning.

    Returns
    --------
    digit_string: :class`str`
        The digit string with a 0 appended if appropriate.

    Raises
    -------
    ValueError
        An invalid string was passed in.
    """
    try:
        if int(digit_string) < 10 and len(digit_string) == 1:
            digit_string = '0' + digit_string
    except ValueError as e:
        raise e
    except Exception as e:
        raise e
    return digit_string


class SchedulerClient(Client):
    """Represents the Scheduler Client.

    This client assists guild members in scheduling an event
    using slash commands and accepting json packets on udp.

    Attributes
    -----------
    tree: :class:`app_commands.CommandTree`
        The command tree for slash commands.
    loaded_json: :class:`bool`
        Whether or not the client has loaded the json file.
    server_is_running: :class:`bool`
        Whether or not the client's server is running to accept event scheduling from json packets.
    server: :class:`Server`
        The server to accept event scheduling from json packets.
    server.callback: :class:`callable`
        The callback for the server to use when it receives an event json packet.
    events: :class:`list`
        The list of events that the client is managing.
    """

    def __init__(self, intents) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.loaded_json = False
        self.server_is_running = False
        self.server = Server()
        self.server.callback = self.schedule_from_dict
        self.events = []

    async def start_server(self):
        """Starts the client's server for accepting event scheduling json packets.
        """
        self.server_is_running = True
        asyncio.create_task(self.server.start_server())

    async def schedule_from_dict(self, data: dict) -> None:
        """The callback to process an event scheduling json packet.

        Arguments
        ----------
        data: :class:`dict`
            The json packet with the information necessary for scheduling an event.
        """
        logger.info(f"[{data['name']}] Schedule from dict triggered")
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

    async def retrieve_events(self) -> None:
        """Load event data from the data file to resume operations after a restart.

        If an event has an invalid field, it will receive a default value or be discarded
        dependant on which field. Availability and event control buttons are reconfigured from scratch.
        """
        if not self.loaded_json:
            self.loaded_json = True
            events_data = persist.read()
            if events_data:
                for event_data in events_data['events']:
                    try:
                        event = await Event.from_dict(event_data)
                        if not event:
                            raise Exception('Failed to create event object')
                        await event.update_messages()
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
                                        logger.info(f'[{event}] Disabled start button for event with same location: {other_event.name}')
                                    except Exception as e:
                                        logger.error(f'[{event}] Failed to disable start button for {other_event}: {e}')
                            await event.event_buttons_message.edit(view=event.event_buttons)
                        client.events.append(event)
                        logger.info(f'[{event}] event loaded and added to client event list')
                    except Exception as e:
                        logger.error(f'Could not add event to client event list: {e}')
                    # Stop rate limiting when launching bot
                    time.sleep(3)
            else:
                logger.info('No json data found')

    # Return all events as a dict
    def get_events_dict(self) -> dict:
        """Shove all events into a dictionary for writing to the data file.

        Returns
        --------
        events_data: :class:`dict`
            A dict containing all of the data for each event.
        """
        events_data = {}
        events_data['events'] = [event.to_dict() for event in self.events]
        return events_data

    async def setup_hook(self):
        """Syncs the command tree with the guilds the client is in."""
        await self.tree.sync()


OWNER_ID = int(os.getenv('OWNER_ID'))
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
client = SchedulerClient(intents=Intents.all())


class Event:
    """Represents an event that the bot will manage.

    Events can be scheduled through slash commands or network json packets.
    They can be also be manually created for a specific time.

    Attributes
    -----------
    name: :class:`str`
        The name for the event. Two events cannot share the same name.
    voice_channel: :class:`VoiceChannel`
        The voice channel that the event will occur in.
    guild: :class:`Guild`
        The guild that the event will occur in.
    text_channel: :class:`TextChannel`
        The text channel that event related messages will be sent in.
    image_url: :class:`str`
        The url to an image to use for the event.
    scheduler: :class:`Participant`
        The :class:`Participant` who scheduled the event.
    rescheduler: :class:`Participant`
        The most recent :class:`Participant` to reschedule the event.
    participants: :class:`list`
        The list of :class:`Participant`s invited to the event.
    duration: :class:`timedelta`
        The duration of the event.
    multi_event: :class:`bool`
        Whether or not this :class:`Event` will have multiple guild events.
    start_times: :class:`list`
        The list of guild event start times.
    availability_message: :class:`Message`
        The message object that is requesting availability from participants.
    avail_buttons: :class:`AvailabilityButtons`
        The availability buttons attached to the availability_message that users can
        use to submit their availability, unsubscribe, or cancel.
    event_buttons_message: :class:`Message`
        The event control buttons message. States the start time, time remaining until
        the start time, when the event was started, when the event was rescheduled,
        the event's duration, and when the event was ended.
    event_buttons: :class:`EventButtons`
        The event control buttons attached to the event_buttons_message. These allow
        for starting, ending, unsubscribing from, rescheduling, and cancelling the event.
    event_buttons_msg_content_pt1: :class:`str`
        The first segment of the event buttons message. The duration comes after.
    event_buttons_msg_content_pt2: :class:`str`
        The second segment of the event buttons message. The timeout counter comes after.
    event_buttons_msg_content_pt3: :class:`str`
        The third segment of the event buttons message. The participant mentions come after.
    event_buttons_msg_content_pt4: :class:`str`
        The fourth segment of the event buttons message. It contains the unsubscribed users.
    ready_to_create: :class:`bool`
        Indicator of whether (a) start time(s) has been set and the event(s) is(/are) ready to create.
    created: :class:`bool`
        Indicator of whether or not the event has had (a) guild event(s) created.
    started: :class:`bool`
        Indicator of whether or not the first in line guild event has been started.
    scheduled_events: :class:`list`
        List of guild scheduled event objects.
    changed: :class:`bool`
        Indicator of whether a participant has interacted with the bot since the last update.
    timeout_counter: :class:`int`
        The event's time to live. Also used to resend the availability message for visibility.
    """

    def __init__(self,
                 name: str,
                 voice_channel: VoiceChannel,
                 guild: Guild,
                 text_channel: TextChannel,
                 image_url: str = None,
                 scheduler: Participant = None,
                 rescheduler: Participant = None,
                 participants: list = None,
                 duration=timedelta(minutes=30),
                 multi_event: bool = False,
                 start_times: list = None,
                 availability_message=None,
                 avail_buttons=None,
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
                 timeout_counter: int = EVENT_TIMEOUT
                 ) -> None:
        self.name = name
        self.guild = guild
        self.entity_type = EntityType.voice
        self.text_channel = text_channel
        self.availability_message = availability_message
        if voice_channel:
            self.voice_channel = voice_channel
        else:
            try:
                self.voice_channel = self.guild.voice_channels[0]
            except Exception as e:
                logger.exception(f'Failed to get voice channel: {e}')
                self.voice_channel = None
        self.privacy_level = PrivacyLevel.guild_only
        self.scheduler = scheduler
        self.rescheduler = rescheduler
        self.participants = participants
        self.image_url = image_url
        self.image_path = f'{self.name}.png'
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
        self.start_times: list = start_times or []
        self.duration = duration
        self.mins_until_start: int = 0
        self.multi_event = multi_event
        self.timeout_counter: int = timeout_counter
        self.avail_msg_content_pt1 = f'**Event name:** {self.name}'
        self.avail_msg_content_pt1 += '\n**Duration:** '
        self.avail_msg_content_pt2 = ''
        if self.scheduler is not None:
            self.avail_msg_content_pt2 += f'\n**Scheduled by:** {self.scheduler}'
        if self.rescheduler is not None:
            self.avail_msg_content_pt2 += f'\n**Rescheduled by:** {self.rescheduler}'
        self.avail_msg_content_pt2 += f'\n**Multi-event:** {self.multi_event}'
        self.avail_msg_content_pt2 += '\n**Times out in:** '
        self.avail_msg_content_pt3 = '\n\nSelect **Respond** to enter your availability.'
        self.avail_msg_content_pt3 += '\n**Full** will mark you as available from now until midnight tonight.'
        self.avail_msg_content_pt3 += '\n**Use Existing** will attempt to grab your availability from another event.'
        self.avail_msg_content_pt3 += '\n**Unsubscribe** will allow the event to occur without you; however, you can still respond and participate.'
        self.avail_msg_content_pt3 += '\n**Cancel** will cancel scheduling.'

    def intersect_time_blocks(self, timeblocks1: list, timeblocks2: list) -> list:
        """Gets all timeblocks in the two availabilities that intersect.

        Arguments
        ----------
        timeblocks1: :class:`list`
            The first availability to compare.
        timeblocks2: :class:`list`
            The second availability to compare.

        Returns
        --------
        intersected_time_blocks: :class:`list`
            A list of timeblocks representing the overlapping time between the two availabilities.
        """
        intersected_time_blocks = []
        for block1 in timeblocks1:
            for block2 in timeblocks2:
                start_time = max(block1.start_time, block2.start_time)
                end_time = min(block1.end_time, block2.end_time)
                if start_time < end_time:
                    intersected_time_blocks.append(TimeBlock(start_time, end_time))
        return intersected_time_blocks

    def compare_availabilities(self) -> None:
        """Compares availabilites of all subscribed participants to select (a) start time(s) for the event."""
        if self.created or self.ready_to_create:
            return
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
                    logger.info(f'[{self}] already has event scheduled for date: {tb_date.month}/{tb_date.day}: {timeblock.start_time.strftime("%H:%M")}')
                    date_scheduled = True
                    break
            if timeblock.duration >= self.duration and not date_scheduled:
                self.start_times.append(timeblock.start_time)
                self.ready_to_create = True
                dates_scheduled.append(tb_date)
        if not self.ready_to_create:
            logger.info(f'[{self.name}] compare_availabilities: No common availability found between all participants, cancelling event')

    async def send_five_minute_warning(self) -> None:
        self.five_minute_warning_flag = True
        try:
            message = f'{self.get_names_string(subscribed_only=True, mention=True)}'
            message += f'\n**5 minute warning!** {self} is scheduled to start in 5 minutes.'
            await self.text_channel.send(content=message, reference=self.event_buttons_message)
        except Exception as e:
            logger.error(f'Error sending 5 minute warning: {e}')

    async def start(self, reason: str = f"Event started by {client.user}.") -> None:
        logger.info(f"[{self}] starting, reason: {reason}")
        try:
            await self.scheduled_events[0].start(reason=reason)
        except Exception as e:
            logger.exception(f'[{self}] Failed to start: {e}')
        try:
            self.start_times[0] = datetime.now().astimezone().replace(second=0, microsecond=0)
        except Exception as e:
            logger.warning(f'[{self}] Error getting start time: {e}')
            self.start_times.append(datetime.now().astimezone().replace(second=0, microsecond=0))
        self.event_buttons_msg_content_pt2 = f'\n**Started at:** {datetime.now().astimezone().strftime("%H:%M")} ET'
        self.started = True
        self.event_buttons.start_button.style = ButtonStyle.green
        self.event_buttons.start_button.disabled = True
        self.event_buttons.end_button.disabled = False
        self.event_buttons.reschedule_button.disabled = True
        self.event_buttons.cancel_button.disabled = True
        await self.update_event_buttons_message()
        # Offset all other events that share this location to start after the end of this event
        buffer_time = timedelta(minutes=0)
        other_events = []
        prev_event = None
        for other_event in client.events:
            if other_event != self and (other_event.voice_channel == self.voice_channel or other_event.shares_participants(self)):
                for other_event_start_time in other_event.start_times:
                    if other_event_start_time < (self.start_times[0] + self.duration + buffer_time):
                        other_event_start_time = (self.start_times[0] + self.duration + buffer_time)
                other_events.append(other_event)
        # Offset the events from each other to prevent stack smashing
        for other_event in other_events:
            if prev_event:
                for other_event_start_time in other_event.start_times:
                    if other_event_start_time < (prev_event.start_times[0] + prev_event.duration + buffer_time):
                        other_event_start_time = (prev_event.start_times[0] + prev_event.duration + buffer_time)
            prev_event = other_event
        # Disable start buttons of events scheduled for the same channel
        for event in client.events:
            if event == self or not event.created or event.voice_channel != self.voice_channel:
                continue
            event.event_buttons.start_button.disabled = True
            try:
                await event.event_buttons_message.edit(view=event.event_buttons)
                logger.info(f'[{self}] Disabled start button for event with same location: {event}')
            except Exception as e:
                logger.error(f'[{self}] Failed to disable start button for {event}: {e}')
        save()

    async def start_if_participants_in_vc(self) -> None:
        """Starts the event if all of the participants are in the voice channel."""
        if all(participant.member in self.voice_channel.members for participant in self.participants):
            await self.start(f'Event started by {client.user} because all users were in the voice channel.')

    async def end(self, reason: str = f"Event ended by {client.user}.") -> None:
        """Ends the event. If there are more scheduled events in this event, shift them forward and prep them."""
        logger.info(f"[{self}] ending, reason: {reason}")
        # Delete scheduled event
        try:
            await self.scheduled_events[0].delete(reason=reason)
        except Exception as e:
            logger.error(f"[{self}] Error in event control end button callback while ending scheduled event: {e}")
        # Update event buttons message
        end_time: datetime = datetime.now().astimezone().replace(second=0, microsecond=0)
        content = self.get_event_buttons_message_string(end_time)
        try:
            self.event_buttons = None
            await self.event_buttons_message.edit(content=content, view=None)
        except Exception as e:
            logger.error(f"[{self}] Error in event control end button callback while editing event buttons message: {e}")
        # Remove start_time and scheduled event from lists
        future_event = False
        try:
            future_event = await self.prep_next_scheduled_event()
        except Exception as e:
            logger.error(f"[{self}] Error in event control end button callback while prepping next scheduled event: {e}")
        # Re-enable start buttons of appropriate events
        for event in client.events:
            if event == self or not event.created or event.voice_channel != self.voice_channel:
                continue
            try:
                event.event_buttons.start_button.disabled = False
                await event.event_buttons_message.edit(view=event.event_buttons)
                logger.info(f'[{self}] Re-enabled start button for event with same location: {event}')
            except Exception as e:
                logger.error(f'[{self}] Failed to re-enable start button for {event}: {e}')
        if not future_event:
            self.remove()
            logger.info(f"[{self}] last event ended, removed from memory")
        else:
            logger.info(f"[{self}] next event starts at {self.start_times[0]}")
        save()

    async def end_if_participants_leave_vc(self) -> None:
        """Ends the event if all of the participants have left the voice channel."""
        if not self.voice_channel.members:
            await self.end(f'Event ended by {client.user} because no users were in the voice channel.')

    def number_of_responded(self) -> int:
        """Gets the number of participants who are subscribed and have responded to the event.

        Returns
        --------
        responded: :class:`int`
            The number of participants who are subscribed and have responded to the event.
        """
        responded = 0
        for participant in self.participants:
            if participant.subscribed and participant.answered:
                responded += 1
        return responded

    async def prep_next_scheduled_event(self) -> bool:
        """Preps the next guild scheduled event and update the event control buttons message.

        Returns
        --------
        :class:`bool`
            Whether or not the event has more scheduled events.
        """
        if len(self.scheduled_events) > 1 and len(self.start_times) > 1:
            self.scheduled_events = self.scheduled_events[1:]
            self.start_times = self.start_times[1:]
            self.five_minute_warning_flag = False
            self.event_buttons.start_button.disabled = True
            self.event_buttons.end_button.disabled = True
            self.event_buttons.unsubscribe_button.disabled = True
            self.event_buttons.reschedule_button.disabled = True
            self.event_buttons.cancel_button.disabled = True
            if self.event_buttons_message is not None:
                self.event_buttons_message.delete()
                self.event_buttons_message = None
            self.event_buttons = None
            await self.update_event_buttons_message()
            save()
            return True
        else:
            return False

    async def make_scheduled_events(self) -> None:
        """Creates a scheduled event for each start time and sets the guild event's image if appropriate."""
        for start_time in self.start_times:
            scheduled_event = await self.guild.create_scheduled_event(name=self.name,
                                                                      description='Bot-generated event',
                                                                      start_time=start_time,
                                                                      entity_type=self.entity_type,
                                                                      channel=self.voice_channel,
                                                                      privacy_level=self.privacy_level)
            await self.save_image_to_file()
            if self.has_image_saved():
                await scheduled_event.edit(image=self.get_image())
            self.scheduled_events.append(scheduled_event)
            logger.info(f'[{self}] Created event starting {start_time.strftime("%A, %m/%d/%Y: %H:%M")} ET')
        self.ready_to_create = False
        self.created = True

    async def save_image_to_file(self) -> str:
        """Saves the image from the url to a file to allow for sending in messages."""
        if self.image_url == "":
            self.image_url = None
        if self.image_url is None:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.image_url) as response:
                    logger.info(f"[{self}] Retrieving image from {self.image_url}")
                    if response.status == 200:
                        with open(self.image_path, 'wb') as file:
                            file.write(await response.read())
                        logger.info(f"[{self}] Saved image")
                    else:
                        logger.error(f"[{self}] Request returned: {response.status}")
                        self.image_url = None
        except Exception as e:
            logger.exception(f"[{self}] Failed to download or save image: {e}")
            logger.error(f"[{self}] Image link: {self.image_url}")
            self.image_url = None

    def get_image(self) -> bytes:
        """Gets the image from the file as bytes for use in messages.

        Returns
        --------
        image_bytes: :class:`bytes`
            The image file loaded as bytes.
        """
        return open(self.image_path, 'rb').read()

    def delete_image_file(self) -> None:
        """Deletes the image file if one has been downloaded for the event."""
        if not self.has_image_saved():
            return
        try:
            os.remove(self.image_path)
            logger.info(f"[{self}] Deleted image file")
        except Exception as e:
            logger.exception(f"[{self}] Failed to delete image: {e}")

    def get_scheduling_status(self) -> str:
        """Gets the current event status.

        Returns
        --------
        status: :class:`str`
            A string describing the current status of the event.
        """
        if self.started:
            return "Started event"
        if self.created:
            return "Created event"
        if self.ready_to_create:
            return "Creating event"
        if self.changed:
            return "Availability input cooldown"
        if self.has_everyone_answered():
            return "Preparing to create event"
        return "Awaiting availability"

    def get_names_string(self, subscribed_only: bool = False, unsubscribed_only: bool = False, unanswered_only: bool = False, mention: bool = False) -> str:
        """Gets a string of names meeting the criteria provided through arguments.

        Arguments
        ----------
        subscribed_only: :class:`bool`
            Only include subscribed participants in the string.
        unsubscribed_only: :class:`bool`
            Only include unsubscribed participants in the string.
        unanswered_only: :class:`bool`
            Only include unanswered participants in the string.
        mention: :class:`bool`
            Use mentions instead of nicknames or usernames.
        """
        names = []
        mentions = ''

        if subscribed_only and unsubscribed_only:
            subscribed_only = False
            unsubscribed_only = False

        for participant in self.participants:
            if mention:
                name_string = f'{participant.member.mention} '
            else:
                name_string = f'{participant}'

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
            return f'\n{mentions}'
        return ", ".join(names)

    def add_user_as_participant(self, user: User) -> None:
        """Adds the user to the event as a participant if they are not one already.

        Arguments
        ----------
        user: :class:`User` or :class:`Member`
            The user to add to the event.
        """
        if user.id not in [participant.member.id for participant in self.participants]:
            member = self.guild.get_member(user.id)
            participant = Participant(member=member)
            self.participants.append(participant)

    def get_participant(self, username_or_id) -> Participant:
        """Gets a participant with their nickname, username, or id.

        Arguments
        ----------
        username_or_id: :class:`str` or :class:`int`
            The nickname, username, or id to get the participant object for.

        Returns
        --------
        participant: :class:`Participant`
            If a participant with that nickname, username, or id is found.
        None:
            If no participant is found matching the provided data.
        """
        for participant in self.participants:
            if participant.member.nick and participant.member.nick == username_or_id:
                return participant
            if participant.member.name == username_or_id or participant.member.id == username_or_id:
                return participant
        return None

    def shares_participants(self, event) -> bool:
        """Indicates whether this event shares participants with the event provided.

        Arguments
        ----------
        event: :class:`Event`
            The event to compare participants with.

        Returns
        --------
        True:
            If a participant with a matching member id is found.
        False:
            If no participants with a matching member id are found.
        """
        for self_participant in self.participants:
            for other_participant in event.participants:
                if self_participant.member.id == other_participant.member.id:
                    return True
        return False

    def shared_participants(self, event) -> list:
        """Gets the list of participants shared with the provided event.

        Arguments
        ----------
        event: :class:`Event`
            The event to compare participants with.

        Returns
        --------
        other_participants: :class:`list`
            The list of shared participants between this event and the other event.
            Empty if the events do not share participants.
        """
        other_participants = []
        for self_participant in self.participants:
            for other_participant in event.participants:
                if self_participant.member.id == other_participant.member.id:
                    other_participants.append(other_participant)
                    logger.info(f"Found shared participant {self_participant} in {self.name} and {event}")
        return other_participants

    def get_other_availability(self, participant: Participant) -> list:
        """Gets availability of a participant from another event that they are in.

        Arguments
        ----------
        participant: :class:`Participant`
            The participant to get availability for.

        Returns
        --------
        event_availabilities: :class:`list`
            The list of availabilities from other events the participant is in.
        """
        event_availabilities = []
        for other_event in client.events:
            if other_event != self:
                for other_participant in other_event.participants:
                    if other_participant.member.id == participant.member.id and other_participant.answered:
                        event_avail = EventAvailability(event=other_event, avail=other_participant.availability, full_flag=other_participant.full_availability_flag)
                        event_availabilities.append(event_avail)
        return event_availabilities

    def get_duration_minutes(self) -> int:
        """Gets the duration of the event in minutes.

        Returns
        --------
        duration: :class:`int`
            The duration of the event in minutes.
        """
        return self.duration.total_seconds() // 60

    def get_timeout_minutes(self) -> float:
        """Gets the event's time remaining until timeout in minutes.

        Returns
        --------
        timeout: :class:`float`
            The event's time remaining until timeout in minutes.
        """
        return self.timeout_counter / 2

    def reset_timeout_counter(self) -> None:
        """Resets the timeout counter to the default value.
        """
        self.timeout_counter = EVENT_TIMEOUT

    def get_start_time_string(self, index: int = 0) -> str:
        """Gets the string for the start time at the provided index.

        Arguments
        ----------
        index: :class:`int`
            Optional. Index of the start time to get the string for.
            Default: 0

        Returns
        --------
        start_time: :class:`str`
            The string for the start time.
        """
        return f'{self.start_times[index].strftime("%A, %m/%d at %H:%M")} ET'

    def get_availability_request_string(self) -> str:
        """Gets the content string for the availability message.

        Returns
        --------
        output: :class:`str`
            The content string for the availability message.
        """
        output = self.avail_msg_content_pt1
        output += get_time_str_from_minutes(self.get_duration_minutes())
        output += self.avail_msg_content_pt2
        output += get_time_str_from_minutes(self.get_timeout_minutes())
        output += self.avail_msg_content_pt3
        if not self.has_everyone_answered():
            cur_date = datetime.now().astimezone().date()
            latest_date = self.get_latest_date()
            if cur_date < latest_date:
                output += f'\n\n**Input availability with start time on latest availability date: {latest_date.strftime("%m/%d")}**'
            mentions = self.get_names_string(subscribed_only=True, unanswered_only=True, mention=True)
            output += f'\n\nWaiting for a response from:{mentions}'
        else:
            output += '\n\nEveryone has responded.'
        return output

    def get_latest_date(self):
        """Gets the latest date of all start times in all participants' availabilities.

        Returns
        --------
        latest_date: :class:`datetime.date`
            The latest date of all start times in all participants' availabilities.
        """
        current_time = datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(minutes=START_TIME_DELAY)
        latest_date = current_time.date()
        for participant in self.participants:
            for timeblock in participant.availability:
                latest_date = max((timeblock.start_time - timedelta(hours=HOURS_PAST_MIDNIGHT_CUTOFF)).date(), latest_date)
        return latest_date

    def has_everyone_answered(self) -> bool:
        """Indicates whether or not all participants have responded.

        Returns
        --------
        True
            If all participants have responded.
        False
            If at least one participant has not yet responded.
        """
        latest_date = self.get_latest_date()
        for participant in self.participants:
            if participant.subscribed:
                participant.confirm_answered(duration=self.duration, latest_date=latest_date)
                if not participant.answered:
                    return False
        return True

    def has_image_saved(self) -> bool:
        """Indicates whether the event has an image saved.

        Returns
        --------
        True
            If an image is saved.
        False
            If an image is not saved.
        """
        return os.path.exists(self.image_path)

    def restore_availabilities(self, event) -> None:
        """Restores availabilities that were modified by this event's creation.

        Arguments
        ----------
        event: :class:`Event`
            The event to restore availability for each shared participant in.
        """
        if event is self:
            return
        [participant.restore_availability_for_event(event.name) for participant in self.shared_participants(event)]

    def update_availabilities_to(self, participant: Participant) -> None:
        """Updates end time of full flag availabilities to the latest time.

        Arguments
        ----------
        participant: :class:`Participant`
            The participant to update all other participants to.
        """
        if len(participant.availability) == 0:
            return
        for other_participant in self.participants:
            # If this is a different participant and they have selected full availability
            if other_participant != participant and other_participant.full_availability_flag:
                # For every timeblock, if they start on the same day
                # and their end time is sooner, we update their end time to this one
                for timeblock in participant.availability:
                    if timeblock.start_time.date() == other_participant.availability[0].start_time.date():
                        other_participant.availability[0].end_time = max(other_participant.availability[0].end_time, timeblock.end_time)
                        logger.info(f'[{self}] Updated {other_participant}\'s first timeblock\'s end time to {other_participant.availability[0].end_time.strftime("%a, %m/%d %H:%M")}')

    def get_event_buttons_message_string(self, end_time: datetime = None) -> str:
        """Gets the content for the event buttons message.

        Arguments
        ----------
        end_time: :class:`datetime`
            The end time of the event to put in the message string.

        Returns
        --------
        response: :class:`str`
            The content for the event buttons message.
        """
        if end_time is None:
            # Get time until start
            duration = f"{get_time_str_from_minutes(self.get_duration_minutes())}"
            time_until_start: timedelta = self.start_times[0] - datetime.now().astimezone()
            self.mins_until_start = int(time_until_start.total_seconds() // 60)
        else:
            # Replace duration with actual duration
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
                self.event_buttons_msg_content_pt2 = f"\n**Starts in:** {get_time_str_from_minutes(self.mins_until_start + 1)}"
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
        save()
        response = f"{self.event_buttons_msg_content_pt1} {self.event_buttons_msg_content_pt2} {self.event_buttons_msg_content_pt3} {self.event_buttons_msg_content_pt4}"
        return response

    async def update_messages(self) -> None:
        """Update the availability and event buttons messages."""
        await self.update_availability_message()
        await self.update_event_buttons_message()

    async def update_availability_message(self, rescheduler: Participant = None) -> None:
        """Update the availability message.

        Arguments
        ----------
        rescheduler: :class:`Participant`
            Optional. The participant who rescheduled the event.
            Default: None
        """
        # Delete the message if the event was created
        if self.created:
            if self.availability_message is not None:
                await self.availability_message.delete()
                self.availability_message = None
            self.avail_buttons = None
            return
        self.rescheduler = rescheduler
        if self.avail_buttons is None:
            self.avail_buttons = AvailabilityButtons(event=self)
        save()
        # Create the embed for the message
        description = self.get_scheduling_status()
        embed = Embed(title='Availabilities', description=description, color=Color.blue())
        if self.image_url is not None and self.image_url != "":
            embed.set_image(url=self.image_url)
        for participant in self.participants:
            participantName = f'{participant}'
            if participant.availability and participant.subscribed:
                availString = participant.get_availability_string()
                embed.add_field(name=participantName, value=availString, inline=False)
            elif not participant.subscribed:
                embed.add_field(name=participantName, value="Unsubscribed", inline=False)
        # Send a new message
        if self.availability_message is None:
            if self.rescheduler is None:
                self.avail_msg_content_pt3 += '\n\nThe event will be either created or cancelled within a minute after the last person responds.️'
            else:
                self.rescheduler.set_no_availability()
            response = self.get_availability_request_string()
            self.availability_message = await self.text_channel.send(content=response,
                                                                     view=self.avail_buttons,
                                                                     embed=embed)
        # Update existing message
        else:
            try:
                await self.availability_message.edit(content=self.get_availability_request_string(),
                                                     view=self.avail_buttons,
                                                     embed=embed)
            except Exception as e:
                logger.exception(f'[{self}] Failed to edit availability message in update: {e}')

    async def update_event_buttons_message(self) -> None:
        """Updates the event buttons message."""
        # Delete the message if the event was rescheduled
        if not self.created:
            if self.event_buttons_message is not None:
                await self.event_buttons_message.delete()
                self.event_buttons_message = None
            self.event_buttons = None
            return
        if not self.event_buttons:
            self.event_buttons = EventButtons(self)
        save()
        message = self.get_event_buttons_message_string()
        # Send a new message
        if self.event_buttons_message is None:
            if self.has_image_saved():
                self.event_buttons_message = await self.text_channel.send(content=message,
                                                                          view=self.event_buttons,
                                                                          file=File(self.image_path))
            else:
                self.event_buttons_message = await self.text_channel.send(content=message,
                                                                          view=self.event_buttons)
        # Edit existing message
        else:
            if self.has_image_saved():
                await self.event_buttons_message.edit(content=message,
                                                      view=self.event_buttons,
                                                      attachments=[File(self.image_path)])
            else:
                await self.event_buttons_message.edit(content=message,
                                                      view=self.event_buttons)

    async def cancel(self, reason: str = "", canceller: str = "") -> None:
        """Cancels the event.

        Arguments
        ----------
        reason: :class:`str`
            The reason for the cancellation of the event.
        canceller: :class:`str`
            The name of the canceller of the event.
        """
        content = f'**{self.name} has been cancelled'
        if canceller == "":
            content += '.**'
        else:
            content += f' by {canceller}.**'
        if reason != "":
            content += f'\n**Reason:** "{reason}"'
        content += f'\n{self.get_names_string(subscribed_only=True, mention=True)}'
        if self.text_channel:
            if self.has_image_saved():
                await self.text_channel.send(content=content, file=File(self.image_path))
            else:
                await self.text_channel.send(content=content)
        else:
            for participant in self.participants:
                async with participant.msg_lock:
                    if self.has_image_saved():
                        await participant.member.send(content=content, file=File(self.image_path))
                    else:
                        await participant.member.send(content=content)
        try:
            if self.availability_message:
                await self.availability_message.delete()
                self.availability_message = None
            if self.event_buttons_message:
                await self.event_buttons_message.delete()
                self.event_buttons_message = None
        except Exception as e:
            logger.error(f'Error in event cancel while deleting a message: {e}')
        try:
            if len(self.scheduled_events) > 0:
                await self.scheduled_events[0].delete(reason=f'Cancel button pressed by {canceller}.')
        except Exception as e:
            logger.error(f'Error in event cancel while deleting scheduled event: {e}')
        try:
            anotherEvent = await self.prep_next_scheduled_event()
        except Exception as e:
            logger.error(f'Error in event cancel while prepping next scheduled event: {e}')
        if not anotherEvent:
            self.remove()
        for event in client.events:
            event.restore_availabilities(self)
        save()

    def remove(self) -> None:
        """Deletes the event's image file and removes the event from the client's event list."""
        self.delete_image_file()
        client.events.remove(self)
        save()
        logger.info(f'[{self}] Removed from client events list')

    @classmethod
    async def from_dict(cls, data):
        """Constructs an :class:`Event` from a data dict.

        Arguments
        ----------
        data: :class:`dict`
            The data to create the :class:`Event` from.

        Returns
        --------
        class: :class:`Event`
            The :class:`Event` object.
        """
        # Name
        event_name = data["name"]
        if event_name == '':
            raise Exception('Event has no name, discarding event')
        elif event_name in [event.name for event in client.events]:
            raise Exception(f'[{event_name}] Event name already in use, discarding repeat event')
        else:
            logger.info(f'[{event_name}] loading event')

        # Guild
        event_guild = client.get_guild(data["guild_id"])
        if event_guild:
            logger.info(f'[{event_name}] guild found: {event_guild.id}')
        else:
            raise Exception(f'[{event_name}] Could not find guild, discarding event')

        # Text channel
        event_text_channel = event_guild.get_channel(data["text_channel_id"])
        if not event_text_channel:
            logger.info(f'[{event_name}] no text channel found')
        else:
            logger.info(f'[{event_name}] text channel found: {event_text_channel.id}')

        # Voice channel
        event_voice_channel = utils.get(event_guild.voice_channels, id=data["voice_channel_id"])
        if event_voice_channel:
            logger.info(f'[{event_name}] voice channel found: {event_voice_channel.id}')
        else:
            raise Exception(f'[{event_name}] Could not find voice channel, discarding event')

        # Scheduler
        event_scheduler = event_guild.get_member(data['scheduler_id'])
        if event_scheduler:
            logger.info(f'[{event_name}] found scheduler with id {event_scheduler.id}')
        event_rescheduler = event_guild.get_member(data['rescheduler_id'])
        if event_rescheduler:
            logger.info(f'[{event_name}] found rescheduler with id {event_rescheduler.id}')

        # Participants
        event_participants = [Participant.from_dict(event_guild, participant) for participant in data["participants"]]
        for participant in event_participants.copy():
            try:
                if participant is None:
                    event_participants.remove(participant)
            except Exception as e:
                logger.warning(f"[{event_name}] Exception while adding participant: {e}")
                event_participants.remove(participant)
        if event_participants:
            logger.info(f'[{event_name}] found participant(s): {", ".join([p.member.name for p in event_participants])}')
        else:
            raise Exception(f'[{event_name}] no participant(s) found, discarding event')

        # Set (re)scheduler to a participant
        for participant in event_participants:
            if type(event_scheduler) is Member and event_scheduler.id == participant.member.id:
                event_scheduler = participant
            if type(event_rescheduler) is Member and event_rescheduler.id == participant.member.id:
                event_rescheduler = participant

        # Interaction (availability) message
        event_avail_buttons = None
        event_availability_message = None
        try:
            event_availability_message = await event_text_channel.fetch_message(data["availability_message_id"])
            logger.info(f'[{event_name}] found availability_message: {event_availability_message.id}')
        except NotFound:
            logger.info(f'[{event_name}] no availability_message found')
        except HTTPException as e:
            logger.error(f'[{event_name}] error getting availability_message: {e}')

        # Event buttons message
        event_event_buttons = None
        event_event_buttons_message = None
        try:
            event_event_buttons_message = await event_text_channel.fetch_message(data["event_buttons_message_id"])
            logger.info(f'[{event_name}] found event_buttons_message: {event_event_buttons_message.id}')
        except NotFound:
            logger.info(f'[{event_name}] no event_buttons_message found')
        except HTTPException as e:
            logger.error(f'[{event_name}] error getting event_buttons_message: {e}')

        # Image url
        event_image_url = data["image_url"]
        logger.info(f'[{event_name}] image url: {event_image_url}')

        # Event buttons msg content
        event_event_buttons_msg_content_pt1 = data["event_buttons_msg_content_pt1"]
        logger.info(f'[{event_name}] event_buttons_msg_content_pt1: ' + r'{event_event_buttons_msg_content_pt1}')
        event_event_buttons_msg_content_pt2 = data["event_buttons_msg_content_pt2"]
        logger.info(f'[{event_name}] event_buttons_msg_content_pt2: ' + r'{event_event_buttons_msg_content_pt2}')
        event_event_buttons_msg_content_pt3 = data["event_buttons_msg_content_pt3"]
        logger.info(f'[{event_name}] event_buttons_msg_content_pt3: ' + r'{event_event_buttons_msg_content_pt3}')
        event_event_buttons_msg_content_pt4 = data["event_buttons_msg_content_pt4"]
        logger.info(f'[{event_name}] event_buttons_msg_content_pt4: ' + r'{event_event_buttons_msg_content_pt4}')

        # Ready to create
        event_ready_to_create = data["ready_to_create"]
        logger.info(f'[{event_name}] ready_to_create: {event_ready_to_create}')

        # Created
        event_created = data["created"]
        logger.info(f'[{event_name}] created: {event_created}')

        # Started
        event_started = data["started"]
        logger.info(f'[{event_name}] started: {event_started}')

        # Scheduled event
        event_scheduled_events = []
        try:
            scheduled_event_ids = data["scheduled_event_ids"]
            for scheduled_event_id in scheduled_event_ids:
                for guild_scheduled_event in event_guild.scheduled_events:
                    if guild_scheduled_event.id == scheduled_event_id:
                        event_scheduled_events.append(guild_scheduled_event)
                        logger.info(f'[{event_name}] found guild scheduled event: {guild_scheduled_event.id}')
                        break
        except Exception as e:
            logger.info(f'[{event_name}] error getting guild scheduled events: {e}')

        # Changed
        event_changed = data["changed"]
        logger.info(f'[{event_name}] changed: {event_changed}')

        # Start time
        try:
            event_start_times = [datetime.fromisoformat(start_time) for start_time in data["start_times"]]
            for event_start_time in event_start_times:
                logger.info(f'[{event_name}] start time found: {event_start_time.strftime("%a, %m/%d/%Y %H:%M")}')
        except Exception as e:
            event_start_time = []
            logger.info(f'[{event_name}] no start times found: {e}')

        # Duration
        event_duration = timedelta(minutes=data["duration"])
        if event_duration:
            logger.info(f'[{event_name}] duration found: {event_duration.total_seconds() // 60}')
        else:
            logger.info(f'[{event_name}] no duration found')

        # Multi-event
        try:
            event_multi_event = data["multi_event"]
        except Exception as e:
            logger.warning(f'Failed to read multi_event data: {e}')
            event_multi_event = False
        logger.info(f'[{event_name}] multi_event: {event_multi_event}')

        # Timeout counter
        try:
            event_timeout_counter = data["timeout_counter"]
        except Exception as e:
            logger.warning(f'Failed to read timeout counter data: {e}')
            event_timeout_counter = EVENT_TIMEOUT
        logger.info(f'[{event_name}] timeout_counter: {event_timeout_counter}')

        return cls(
            name=event_name,
            guild=event_guild,
            text_channel=event_text_channel,
            availability_message=event_availability_message,
            avail_buttons=event_avail_buttons,
            voice_channel=event_voice_channel,
            scheduler=event_scheduler,
            rescheduler=event_rescheduler,
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
            multi_event=event_multi_event,
            timeout_counter=event_timeout_counter
        )

    def to_dict(self) -> dict:
        """Packs the event into a dict for saving.

        Returns
        --------
        data: :class:`dict`
            The event data dict.
        """
        try:
            availability_message_id = self.availability_message.id
        except Exception:
            availability_message_id = 0
        try:
            scheduler_id = self.scheduler.member.id
        except Exception:
            scheduler_id = 0
        try:
            rescheduler_id = self.rescheduler.member.id
        except Exception:
            rescheduler_id = 0
        try:
            participants = [participant.to_dict() for participant in self.participants]
        except Exception as e:
            logger.warning(f'Failed getting participants dict list: {e}')
            participants = []
        if self.image_url is not None:
            image_url = self.image_url
        else:
            image_url = ''
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
            'voice_channel_id': self.voice_channel.id,
            'scheduler_id': scheduler_id,
            'rescheduler_id': rescheduler_id,
            'participants': participants,
            'image_url': image_url,
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
            'multi_event': self.multi_event,
            'timeout_counter': self.timeout_counter
        }

    def __repr__(self) -> str:
        """Gets the name of the event for string formatting purposes.

        Returns
        --------
        name: :class:`str`
            The name of the event.
        """
        return f'{self.name}'


class CancelModal(Modal):
    """Represents a modal for cancelling an event.

    Attributes
    -----------
    event: :class:`Event`
        The event that is being cancelled.
    reason: :class:`TextInput`
        The reason for the event's cancellation.
    """

    def __init__(self, event: Event, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.event = event
        self.reason = TextInput(label='Reason', placeholder="don't wanna")
        self.add_item(self.reason)

    async def on_submit(self, interaction: Interaction) -> None:
        logger.info(f'[{self.event}] {interaction.user} cancelled event with reason: {self.reason.value}')
        canceller = interaction.user.name
        if interaction.user.nick:
            canceller = interaction.user.nick
        await self.event.cancel(reason=self.reason.value, canceller=canceller)
        await interaction.response.send_message(content=f"Cancelled {self.event}.", ephemeral=True)

    async def on_error(self, interaction: Interaction, error: Exception) -> None:
        await interaction.response.send_message(content=f'Error cancelling event: {error}', ephemeral=True)
        logger.exception(f'[{self.event}] Error cancelling event through modal: {error}')


class AvailabilityModal(Modal):
    """Represents a modal for inputting availability for an event.

    Attributes
    -----------
    event: :class:`Event`
        The event that the availability is being collected for.
    timeslot1: :class`TextInput`
        The first field for availability time input.
    timeslot2: :class`TextInput`
        The second field for availability time input.
    timeslot3: :class`TextInput`
        The third field for availability time input.
    date: :class:`TextInput`
        The date for the availability.
    timezone: :class:`TextInput`
        The timezone that the time input is in.
    """

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
        if participant is None:
            member = self.event.guild.get_member(interaction.user.id)
            participant = Participant(member=member)
            self.event.participants.append(participant)
        participant.subscribed = True
        avail_string = f'{self.timeslot1.value}, {self.timeslot2.value}, {self.timeslot3.value} {self.timezone.value}'
        try:
            logger.info(f'[{self.event}] Received availability from {interaction.user.name}')
            participant.set_specific_availability(avail_string, self.date.value)
            # self.event.update_availabilities_to(participant)
            response = f'**__Availability received for {self.event}:__**' + participant.get_availability_string()
            await interaction.response.send_message(response, ephemeral=True)
            for timeblock in participant.availability:
                logger.info(f'[{self.event}] \t{timeblock}')
        except Exception as e:
            try:
                await interaction.response.send_message(f'Error setting your availability: {e}')
            except Exception as e:
                logger.error(f'[{self.event}] Failed sending interaction response: {e}')
            logger.exception(f'[{self.event}] Error setting specific availability: {e}')
        finally:
            await self.event.update_availability_message()

    async def on_error(self, interaction: Interaction, error: Exception) -> None:
        await interaction.response.send_message(f'Error getting availability: {error}', ephemeral=True)
        logger.exception(f'[{self.event}] Error getting availability from {interaction.user.name} (AvailabilityModal): {error}')


class AvailabilityButtons(View):
    """Represents the availability buttons tied to an availability message.

    Attributes
    -----------
    event: :class:`Event`
        The event that the buttons are for.
    respond_label: :class:`str`
        The label for the Respond button.
    full_label: :class:`str`
        The label for the Full Availability button.
    reuse_label: :class:`str`
        The label for the Reuse Availability button.
    unsub_label: :class:`str`
        The label for the Unsubscribe button.
    cancel_label: :class:`str`
        The label for the Cancel button.
    respond_button: :class:`callable`
        The Respond button.
    full_button: :class:`callable`
        The Full Availability button.
    reuse_button: :class:`callable`
        The Reuse Availability button.
    unsub_button: :class:`callable`
        The Unsubscribe button.
    cancel_button: :class:`callable`
        The Cancel button.
    """

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

    def add_respond_button(self) -> Button:
        """Sets up and gets the Respond button.

        Returns
        --------
        button: :class:`Button`
            The Respond button.
        """
        button = Button(label=self.respond_label, style=ButtonStyle.green)

        async def respond_button_callback(interaction: Interaction):
            try:
                am_title = f'Availability for {self.event}'
                if len(am_title) >= 45:
                    am_title = f"{am_title[:41]}..."
                await interaction.response.send_modal(AvailabilityModal(event=self.event, title=am_title))
            except Exception as e:
                logger.exception(f'Error sending availability modal: {e}')
            save()
        button.callback = respond_button_callback
        self.add_item(button)
        return button

    def add_full_button(self) -> Button:
        """Sets up and gets the Full Availability button.

        Returns
        --------
        button: :class:`Button`
            The Full Availability button.
        """
        button = Button(label=self.full_label, style=ButtonStyle.green)

        async def full_button_callback(interaction: Interaction):
            self.event.ready_to_create = False
            participant = self.event.get_participant(interaction.user.name)
            if participant is None:
                member = self.event.guild.get_member(interaction.user.id)
                participant = Participant(member=member)
                self.event.participants.append(participant)
                await self.event.update_availability_message()
            participant.subscribed = True
            if not participant.full_availability_flag:
                logger.info(f'[{self.event}] {participant} selected full availability button')
                participant.set_full_availability()
                self.event.update_availabilities_to(participant)
                for timeblock in participant.availability:
                    logger.info(f'[{self.event}] \t{timeblock}')
                participant.answered = True
                response = f"__**Availability for {self.event}:**__"
                response += participant.get_availability_string()
                await interaction.response.send_message(response, ephemeral=True)
            else:
                logger.info(f'[{self.event}] {participant} deselected full availability')
                participant.set_no_availability()
                for timeblock in participant.availability:
                    logger.info(f'[{self.event}] \t{timeblock}')
                participant.answered = False
                await interaction.response.send_message('Your availability has been cleared.', ephemeral=True)
            await self.event.update_availability_message()
            save()
        button.callback = full_button_callback
        self.add_item(button)
        return button

    def add_reuse_button(self) -> Button:
        """Sets up and gets the Reuse Availability button.

        Returns
        --------
        button: :class:`Button`
            The Reuse Availability button.
        """
        button = Button(label=self.reuse_label, style=ButtonStyle.blurple)

        async def reuse_button_callback(interaction: Interaction):
            logger.info(f'[{self.event}] Reuse button pressed by {interaction.user.name}')
            participant = self.event.get_participant(interaction.user.name)
            if participant is None:
                member = self.event.guild.get_member(interaction.user.id)
                participant = Participant(member=member)
                self.event.participants.append(participant)
                await self.event.update_availability_message()
            found_availabilities = self.event.get_other_availability(participant)
            if not found_availabilities:
                logger.info(f'[{self.event}] No existing availability found for {interaction.user.name}')
                await interaction.response.send_message('No existing availability found.', ephemeral=True)
                return
            logger.info(f'[{self.event}] Found existing availability for {interaction.user.name}')
            if len(found_availabilities) == 1:
                participant.availability = found_availabilities[0].avail.copy()
                participant.answered = True
                response = f"__**Availability for {self.event}:**__"
                response += participant.get_availability_string()
                await interaction.response.send_message(response, ephemeral=True)
            else:
                await interaction.response.send_message('Select another event to grab your availability from.', view=ExistingAvailabilitiesSelectView(found_availabilities, participant), ephemeral=True)
            await self.event.update_availability_message()
            save()
        button.callback = reuse_button_callback
        self.add_item(button)
        return button

    def add_unsub_button(self) -> Button:
        """Sets up and gets the Unsubscribe button.

        Returns
        --------
        button: :class:`Button`
            The Unsubscribe button.
        """
        button = Button(label=self.unsub_label, style=ButtonStyle.red)

        async def unsub_button_callback(interaction: Interaction):
            self.event.ready_to_create = False
            participant = self.event.get_participant(interaction.user.name)
            if participant is None:
                await interaction.response.send_message('You are already not part of this event.', ephemeral=True)
                return
            if participant.subscribed:
                logger.info(f'[{self.event}] {interaction.user.name} unsubscribed')
                participant.subscribed = False
                participant.answered = True
                await interaction.response.send_message(f'You have been unsubscribed from {self.event}.', ephemeral=True)
            else:
                logger.info(f'[{self.event}] {interaction.user.name} resubscribed')
                participant.subscribed = True
                if not participant.availability:
                    participant.answered = False
                await interaction.response.send_message(f'You have been resubscribed to {self.event}.', ephemeral=True)
            await self.event.update_availability_message()
            save()
        button.callback = unsub_button_callback
        self.add_item(button)
        return button

    def add_cancel_button(self) -> Button:
        """Sets up and gets the Cancel button.

        Returns
        --------
        button: :class:`Button`
            The Cancel button.
        """
        button = Button(label=self.cancel_label, style=ButtonStyle.red)

        async def cancel_button_callback(interaction: Interaction):
            self.event.ready_to_create = False
            title = f"Cancel {self.event.name}"
            if len(title) >= 45:
                title = f"{title[:41]}..."
            await interaction.response.send_modal(CancelModal(event=self.event, title=title))
            save()
        button.callback = cancel_button_callback
        self.add_item(button)
        return button


class EventButtons(View):
    """Represents the event buttons attached to an event control message.

    Attributes
    -----------
    event: :class:`Event`
        The event that the buttons are for.
    start_label: :class:`str`
        The label for the Start button.
    end_label: :class:`str`
        The label for the End button.
    unsubscribe_label: :class:`str`
        The label for the Unsubscribe button.
    reschedule_label: :class:`str`
        The label for the Reschedule button.
    cancel_label: :class:`str`
        The label for the Cancel button.
    start_button: :class:`Button`
        The Start button.
    end_button: :class:`Button`
        The End button.
    unsubscribe_button: :class:`Button`
        The Unsubscribe button.
    reschedule_button: :class:`Button`
        The Reschedule button.
    cancel_button: :class:`Button`
        The Cancel button.
    """

    def __init__(self, event: Event) -> None:
        super().__init__(timeout=None)
        self.event = event
        self.start_label = "Start Event"
        self.end_label = "End Event"
        self.unsubscribe_label = "Unsubscribe"
        self.reschedule_label = "Reschedule Event"
        self.cancel_label = "Cancel Event"
        self.start_button = Button(label=self.start_label, style=ButtonStyle.blurple)
        self.end_button = Button(label=self.end_label, style=ButtonStyle.blurple)
        self.unsubscribe_button = Button(label=self.unsubscribe_label, style=ButtonStyle.red)
        self.reschedule_button = Button(label=self.reschedule_label, style=ButtonStyle.red)
        self.cancel_button = Button(label=self.cancel_label, style=ButtonStyle.red)
        self.add_start_button()
        self.add_end_button()
        self.add_unsubscribe_button()
        self.add_reschedule_button()
        self.add_cancel_button()

    def add_start_button(self) -> None:
        """Sets up the Start button.

        Returns
        --------
        button: :class:`Button`
            The Start button.
        """
        async def start_button_callback(interaction: Interaction):
            logger.info(f'[{self.event}] {interaction.user} started by button press')
            self.event.add_user_as_participant(interaction.user)
            await self.event.start(f'Event started by {interaction.user} pressing start button.')
            # Interaction response
            try:
                await interaction.response.edit_message(content=f'{self.event.event_buttons_msg_content_pt1} {self.event.event_buttons_msg_content_pt2} {self.event.event_buttons_msg_content_pt3} {self.event.event_buttons_msg_content_pt4}', view=self.event.event_buttons)
            except Exception as e:
                logger.error(f'[{self.event}] Error responding to START button interaction: {e}')
        self.start_button.callback = start_button_callback
        self.add_item(self.start_button)

    def add_end_button(self) -> None:
        """Sets up the End button.

        Returns
        --------
        button: :class:`Button`
            The End button.
        """
        self.end_button.disabled = True

        async def end_button_callback(interaction: Interaction):
            member = self.event.guild.get_member(interaction.user.id)
            if member not in [participant.member for participant in self.event.participants]:
                logger.info(f"[{self.event}] {member.name} tried to end event but is not a member")
                await interaction.response.send_message(content="You are not a participant of this event!", ephemeral=True)
                return
            await self.event.end(f"Event ended by {interaction.user} pressing end button.")
            # Interaction response, remove buttons
            try:
                await interaction.response.edit_message(view=None)
            except Exception as e:
                logger.exception(f'[{self.event}] Error responding to END button interaction: {e}')
        self.end_button.callback = end_button_callback
        self.add_item(self.end_button)

    def add_unsubscribe_button(self) -> None:
        """Sets up the Unsubscribe button.

        Returns
        --------
        button: :class:`Button`
            The Unsubscribe button.
        """
        async def unsubscribe_button_callback(interaction: Interaction):
            participant = self.event.get_participant(interaction.user.name)
            if participant is None:
                await interaction.response.send_message('You are already not part of this event.', ephemeral=True)
                return
            if participant.subscribed:
                logger.info(f'[{self.event}] {interaction.user.name} unsubscribed')
                participant.subscribed = False
                await interaction.response.send_message(f'You have been unsubscribed from {self.event}.', ephemeral=True)
            else:
                logger.info(f'[{self.event}] {interaction.user.name} resubscribed')
                participant.subscribed = True
                await interaction.response.send_message(f'You have been resubscribed to {self.event}.', ephemeral=True)
            await self.event.update_event_buttons_message()
            save()
        self.unsubscribe_button.callback = unsubscribe_button_callback
        self.add_item(self.unsubscribe_button)

    def add_reschedule_button(self) -> None:
        """Sets up the Reschedule button.

        Returns
        --------
        button: :class:`Button`
            The Reschedule button.
        """
        async def reschedule_button_callback(interaction: Interaction):
            member = self.event.guild.get_member(interaction.user.id)
            if member not in [participant.member for participant in self.event.participants]:
                logger.info(f"[{self.event}] {member.name} tried to reschedule event but is not a member")
                await interaction.response.send_message(content="You are not a participant of this event!", ephemeral=True)
                return
            logger.info(f'[{self.event}] {interaction.user} rescheduled by button press')
            await interaction.response.defer(ephemeral=True)
            self.event.reset_timeout_counter()
            self.event.add_user_as_participant(interaction.user)
            try:
                await self.event.scheduled_events[0].delete(reason=f'Reschedule button pressed by {interaction.user.name}.')
            except Exception as e:
                logger.error(f"[{self.event}] Error cancelling guild event to reschedule: {e}")
            try:
                self.event.scheduled_events.remove(self.event.scheduled_events[0])
            except Exception as e:
                logger.error(f"[{self.event}] Error removing guild event from list: {e}")
            try:
                self.event.start_times.remove(self.event.start_times[0])
            except Exception as e:
                logger.error(f"[{self.event}] Error removing start time from list: {e}")
            self.event.created = False
            self.event.five_minute_warning_flag = False
            self.event.event_buttons_msg_content_pt2 = f'\n**Rescheduled at:** {datetime.now().astimezone().strftime("%H:%M")} ET'
            await self.event.update_event_buttons_message()
            try:
                participant = self.event.get_participant(interaction.user.name)
                participant.answered = False
                participant.subscribed = True
                participant.full_availability_flag = False
                await self.event.update_availability_message(rescheduler=participant)
                await interaction.followup.send(f"Event rescheduling started for {self.event.name}.", ephemeral=True)
            except Exception as e:
                logger.error(f"[{self.event}] Error with RESCHEDULE button requesting availability: {e}")
            # Restore removed availabilities
            for other_event in client.events:
                other_event.restore_availabilities(self.event)
            save()
        self.reschedule_button.callback = reschedule_button_callback
        self.add_item(self.reschedule_button)

    def add_cancel_button(self) -> None:
        """Sets up the Cancel button.

        Returns
        --------
        button: :class:`Button`
            The Cancel button.
        """
        async def cancel_button_callback(interaction: Interaction):
            member = self.event.guild.get_member(interaction.user.id)
            if member not in [participant.member for participant in self.event.participants]:
                logger.info(f"[{self.event}] {member.name} tried to cancel event but is not a member")
                await interaction.response.send_message(content="You are not a participant of this event!", ephemeral=True)
                return
            title = f"Cancel {self.event}"
            if len(title) >= 45:
                title = f"{title[:41]}..."
            await interaction.response.send_modal(CancelModal(event=self.event, title=title))
            save()
        self.cancel_button.callback = cancel_button_callback
        self.add_item(self.cancel_button)


class ExistingGuildEventsSelect(Select):
    """Represents a dropdown of existing guild scheduled events for a user to attach to.

    Attributes
    -----------
    guild: :class:`Guild`
        The guild to get the events from.
    """

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
                participants = get_participants_from_interaction(event_name=selected_guild_event.name, interaction=interaction)
                scheduler = None
                for participant in participants:
                    participant.answered = True
                    if participant.member.id == interaction.user.id:
                        scheduler = participant
                start_times = [selected_guild_event.start_time.astimezone()]
                image_url = None
                if selected_guild_event.cover_image is not None:
                    image_url = selected_guild_event.cover_image.url
                event = Event(name=selected_guild_event.name,
                              voice_channel=selected_guild_event.location,
                              guild=self.guild,
                              text_channel=interaction.channel,
                              image_url=image_url,
                              scheduler=scheduler,
                              participants=participants,
                              start_times=start_times,
                              created=True)
                client.events.append(event)
                await event.save_image_to_file()
            for guild_event in self.guild.scheduled_events:
                if guild_event.name == selected_guild_event.name and guild_event.location == selected_guild_event.location:
                    event.start_times.append(guild_event.start_time.astimezone())
                    if event.has_image_saved():
                        guild_event.edit(image=event.get_image())
                    event.scheduled_events.append(guild_event)
            await event.update_event_buttons_message()
            await interaction.followup.send('Success!', ephemeral=True)
            save()
        else:
            await interaction.response.send_message('Error getting guild scheduled event.')
            logger.exception(f'Error getting guild scheduled event selected by {interaction.user.name}')


class ExistingGuildEventsSelectView(View):
    """Represents a view to house the guild scheduled events dropdown."""

    def __init__(self, guild: Guild):
        super().__init__()
        self.add_item(ExistingGuildEventsSelect(guild))


class EventAvailability:
    """Represents the pairing of an event with a user's availability."""

    def __init__(self, event: Event, avail: list, full_flag: bool):
        self.event = event
        self.avail = avail
        self.full_flag = full_flag


class ExistingAvailabilitiesSelect(Select):
    """Represents a dropdown to allow a user to selection an existing availability from another event."""

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
        logger.info(f'{interaction.user.name} selected availability from {self.values[0]}')
        response = "**Failed to get your availability.**"
        for event_avail in self.event_avails:
            if event_avail.event.name == self.values[0]:
                self.participant.availability = event_avail.avail.copy()
                self.participant.full_availability_flag = event_avail.full_flag
                self.participant.answered = True
                self.participant.subscribed = True
                response = f"__**Availability for {event_avail.event.name}:**__"
                response += self.participant.get_availability_string()
                break
        await event_avail.event.update_availability_message()
        await interaction.response.send_message(content=response, ephemeral=True)


class ExistingAvailabilitiesSelectView(View):
    """Represents a view to house the existing availability dropdown."""

    def __init__(self, event_avails: list, participant: Participant):
        super().__init__()
        self.add_item(ExistingAvailabilitiesSelect(event_avails, participant))


def get_participants_from_interaction(event_name: str,
                                      interaction: Interaction,
                                      include_exclude: INCLUDE_EXCLUDE = None,
                                      usernames: str = None,
                                      roles: str = None) -> list:
    """Wrapper function for getting participants from a channel of an interaction."""
    return get_participants_from_channel(event_name=event_name,
                                         guild=interaction.guild,
                                         channel=interaction.channel,
                                         user=interaction.user,
                                         include_exclude=include_exclude,
                                         usernames=usernames,
                                         roles=roles)

def get_participants_from_channel(event_name: str,
                                  guild: Guild,
                                  channel: TextChannel,
                                  user: User = None,
                                  include_exclude: INCLUDE_EXCLUDE = INCLUDE,
                                  usernames: str = None,
                                  roles: str = None):
    """Gets participants for an event from a channel using the included guidelines.

    Arguments
    ----------
    event_name: :class:`str`
        The name of the event for logging purposes.
    guild: :class:`Guild`
        The guild that the event is ocurring in.
    channel: :class:`TextChannel`
        The text channel that the event is occurring in.
    user: :class:`User` or :class:`Member`
        The user that is scheduling the event.
    include_exclude: :class:`INCLUDE_EXCLUDE`
        Whether to include or exclude the provided usernames/ids/roles.
        Default: INCLUDE
        REQUIRES usernames or roles.
    usernames: :class:`str` or :class:`int`
        The comma separated usernames or ids to include/exclude.
    roles: :class:`str`
        A comma separated list of roles to include/exclude.

    Returns
    --------
    participants: :class:`list`
        The list of participants for the event.
    """
    participants = []
    # Add the scheduler/creator as a participant
    if user is not None:
        member = guild.get_member(user.id)
        if not member.bot:
            participants.append(Participant(member=member))

    # Add users meeting role criteria
    if roles and roles != '':
        logger.info(f"[{event_name}] Parsing roles")
        try:
            roles = roles.split(',')
            roles = [role.strip() for role in roles]
            roles = [utils.find(lambda r: r.name.lower() == role.lower(), guild.roles) for role in roles]
        except Exception as e:
            raise Exception(f'[{event_name}] Failed to parse role(s): {e}')
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
        raise Exception(f'[{event_name}] Received incompatible usernames variable type: {type(usernames)}')
    if usernames and usernames != '':
        logger.info(f"[{event_name}] Adding specific members")
        try:
            usernames = [username.strip() for username in usernames]
        except Exception as e:
            raise Exception(f'[{event_name}] Failed to parse username(s): {e}')
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
    logger.info(f"[{event_name}] Adding all members in channel")
    for member in channel.members:
        if member.bot:
            continue
        if user is not None:
            if member.id == user.id:
                continue
        participants.append(Participant(member=member))
    return participants


def location_has_active_event(location: VoiceChannel) -> bool:
    """Indicates if the provided :class:`VoiceChannel` has an active event in it.

    Arguments
    ----------
    location: :class:`VoiceChannel`
        The location to check for an active event in.

    Returns
    --------
    True
        If the voice channel has an active event.
    False
        If the voice channel does not have an active event.
    """
    for event in client.events:
        if event.voice_channel == location and event.started:
            return True
    return False


def first_start_time(event):
    """Gets the first start time of the event.

    Arguments
    ----------
    event: :class:`Event`
        The event to get the start time from.

    Returns
    --------
    time: :class:`datetime`
        The first start time of the event.
    """
    time = None
    try:
        time = event.start_times[0]
    except Exception as e:
        logger.error(f'Failed to access first start time: {e}')
    return time


def sort_events() -> None:
    """Sorts the created events by first start time and then append the uncreated events."""
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
    save()


async def update_event_timeouts() -> None:
    """Decrements the event timeout counters,
    resends availability messages after RESEND_INTERVAL_HOURS,
    and removes events that have timed out.
    """
    new_events = []
    for event in client.events:
        if event.created:
            new_events.append(event)
            continue
        event.timeout_counter -= 1
        if event.timeout_counter > 0:
            new_events.append(event)
            if not event.created:
                if (event.timeout_counter - OFFSET) % RESEND_INTERVAL == 0:
                    if event.availability_message is not None:
                        await event.availability_message.delete()
                        event.availability_message = None
                    event.avail_buttons = None
                    await event.update_availability_message()
        else:
            notification_message = f'{event.get_names_string(subscribed_only=True, mention=True)}\nScheduling for **{event}** has timed out and has been cancelled.\n'
            if event.text_channel:
                await event.text_channel.send(notification_message)
            else:
                for participant in event.participants:
                    async with participant.msg_lock:
                        await participant.member.send(notification_message)
            logger.info(f'[{event}] timed out and cancelled')
            try:
                await event.availability_message.delete()
                event.availability_message = None
            except NotFound:
                logger.warning(f"[{event}] Availability message not found")
            except Exception as e:
                logger.error(f"[{event}] Couldn't delete availability_message: {e}")
            event.availability_message = None
    client.events = new_events
    save()


async def update_client_presence() -> None:
    """Updates the client's activity on Discord."""
    if client.events:
        if client.events[0].started:
            activity = Activity(type=ActivityType.playing, name=f"{client.events[0]}")
        elif client.events[0].created:
            activity = Activity(type=ActivityType.watching, name=f"for the start of {client.events[0]}")
        else:
            activity = Activity(type=ActivityType.listening, name=f"availability for {client.events[0]}")
    else:
        activity = Activity(type=ActivityType.watching, name="for event scheduling commands")
    await client.change_presence(activity=activity)


@client.event
async def on_ready():
    logger.info(f'[{client.user}] Connected to Discord!')
    await client.retrieve_events()
    if not client.server_is_running:
        await client.start_server()
    if not update.is_running():
        update.start()
    logger.info(f'[{client.user}] Ready!')


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
                        await message.channel.send(f'Added your image to {event}.', reference=message)
                        logger.info(f'[{event}] {message.author.name} added an image')
                    except Exception as e:
                        await message.channel.send(f'Failed to add your image to {event}.\nError: {e}', reference=message)
                        logger.warning(f'[{event}] Error adding image from {message.author.name}: {e}')
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

    # Owner subscribes another user
    if message.author.id == OWNER_ID and 'scheduler: subscribe' in message.content:
        foundEvent = False
        for event in client.events:
            if event.name in message.content.split('to')[1].strip():
                foundEvent = True
                id = message.content.split('subscribe')[1].split('to')[0].strip()
                id = int(id)
                existingParticipant = False
                for participant in event.participants:
                    if participant.member.id == id:
                        existingParticipant = True
                        await message.channel.send(f"{participant} is already subscribed to {event}", reference=message)
                        logger.info(f"[{event}] Owner tried to resubscribe existing participant {participant}")
                        break
                if not existingParticipant:
                    member = event.guild.get_member(id)
                    if member is not None:
                        participant = Participant(member)
                        event.participants.append(participant)
                        await event.update_messages()
                        await message.channel.send(f"Subscribed {participant} to {event}", reference=message)
                        logger.info(f"[{event}] Owner force subscribed {participant}")
                    else:
                        await message.channel.send("Invalid ID provided", reference=message)
                        logger.info(f"[{event}] Invalid subscribe other user format from owner")
                break
        if not foundEvent:
            await message.channel.send("Event not found", reference=message)

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
                            logger.info(f'[{event}] Unsubscribed {participant}')
                            found = True
                            participant.subscribed = False
                            await message.channel.send(f"Unsubscribed {participant}", reference=message)
                            await event.update_availability_message()
                            break
                    if not found:
                        await message.channel.send(f"[{event}] participant not found", reference=message)
                except Exception as e:
                    await message.channel.send("Invalid ID provided", reference=message)
                    logger.info(f"Invalid unsubscribe other user format from owner: {e}")
                break
        if not foundEvent:
            await message.channel.send("Event not found", reference=message)


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
    logger.info(f'[{event_name}] Received event creation request from {interaction.user.name}')
    if not interaction.guild.voice_channels:
        raise Exception('The server must have at least one voice channel to schedule an event.')

    if event_name in [event.name for event in client.events]:
        await interaction.response.send_message(f'Sorry, I already have an event called "{event_name}". Please choose a different name.', ephemeral=True)
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

    scheduler = None
    participants = get_participants_from_interaction(event_name=event_name,
                                                     interaction=interaction,
                                                     include_exclude=include_exclude,
                                                     usernames=usernames,
                                                     roles=roles)
    for participant in participants:
        participant.answered = True
        if participant.member.id == interaction.user.id:
            scheduler = participant

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
    await event.save_image_to_file()
    await event.make_scheduled_events()

    try:
        await interaction.response.send_message(content='Event created!', ephemeral=True)
    except Exception as e:
        logger.error(f'Error sending interaction response for create command: {e}')
    try:
        await event.update_event_buttons_message()
    except Exception as e:
        logger.error(f'Error making event buttons or sending event buttons message in create command: {e}')
    save()


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
    logger.info(f'[{event_name}] Received event schedule request from {interaction.user.name}')
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
                   textChannel: TextChannel,
                   voiceChannel: VoiceChannel,
                   schedulerId: int = 0,
                   imageUrl: str = None,
                   includeExclude: INCLUDE_EXCLUDE = INCLUDE,
                   usernames: str = None,
                   roles: str = None,
                   duration: int = 30,
                   multiEvent: bool = False):
    """Starts the scheduling of an event.

    Arguments
    ----------
    eventName: :class:`str`
        The name of the event.
    guild: :class:`Guild`
        The guild that the event is occurring in.
    textChannel: :class:`TextChannel`
        The text channel that the event sends messages in.
    voiceChannel: :class:`VoiceChannel`
        The voice channel that the event occurs in.
    schedulerId: :class:`int`
        Optional. The ID of the member who scheduled the event.
    imageUrl: :class:`str`
        Optional. The URL for the image.
    includeExclude: :class:`INCLUDE_EXCLUDE`
        Optional. Whether to include or exclude the usernames/ids/roles.
        Default: INCLUDE
        REQUIRES usernames or roles.
    usernames: :class:`str` or :class:`int`
        Optional. Comma separated list of usernames or ids to include/exclude.
    roles: :class:`str`
        Optional. Comma separated list of roles to include/exclude.
    duration: :class:`int`
        Optional. The duration of the event in minutes.
        Default: 30
    multiEvent: :class:`bool`
        Optional. Whether or not the event is a multi event.
        Default: False

    Returns
    --------
    content: :class:`str`
        The content for the interaction response message.
    ephemeral: :class:`bool`
        Whether or not the interaction should be ephemeral.
    """
    logger.info(f"[{eventName}] Scheduling event...")
    if not guild.voice_channels:
        logger.info(f"[{eventName}] Scheduling cancelled due to no voice channel in guild")
        content = "The server must have at least one voice channel to schedule an event."
        ephemeral = True
        return content, ephemeral

    schedulerUser = None
    if schedulerId != 0:
        schedulerUser = guild.get_member(schedulerId)
    if schedulerUser is None:
        schedulerUser = guild.members[0]

    if eventName in [event.name for event in client.events]:
        logger.info(f"[{eventName}] Scheduling cancelled due to existing name")
        content = f"Sorry, I already have an event called {eventName}. Please choose a different name."
        ephemeral = True
        return content, ephemeral

    # Generate participants list
    try:
        participants = get_participants_from_channel(event_name=eventName,
                                                     guild=guild,
                                                     channel=textChannel,
                                                     user=schedulerUser,
                                                     include_exclude=includeExclude,
                                                     usernames=usernames,
                                                     roles=roles)
    except Exception as e:
        logger.error(f"[{eventName}] Error getting participants: {e}")
        content = f"Failed to generate participants list: {e}"
        ephemeral = True
        return content, ephemeral

    scheduler = None
    for participant in participants:
        if participant.member.id == schedulerId:
            scheduler = participant

    if imageUrl == "":
        imageUrl = None

    # Make event object
    try:
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
        await event.save_image_to_file()
        logger.info(f"[{eventName}] Created and saved event object")
    except Exception as e:
        logger.error(f'[{eventName}] Error making event object: {e}')
        content = f"Failed to make event object: {e}"
        ephemeral = True
        return content, ephemeral

    # Request availability and make participant response tracker message
    try:
        logger.info(f"[{eventName}] Requesting availability")
        await event.update_availability_message()
    except Exception as e:
        logger.exception(f'Error requesting availability: {e}')
    save()
    content = f"Event scheduling started for {eventName}."
    ephemeral = True
    return content, ephemeral


@client.tree.command(name='attach', description='Create an event message for an existing guild event.')
async def attach_command(interaction: Interaction):
    logger.info(f'Received attach command request from {interaction.user.name}')
    guild_events = interaction.guild.scheduled_events
    if len(guild_events) == 1:
        await interaction.response.defer(ephemeral=True)
        guild_event = guild_events[0]
        logger.info(f'[{guild_event.name}] {interaction.user.name} attached to guild scheduled event')
        existingEvent = False
        # Event exists, adding guild event to that event
        for it_event in client.events:
            if guild_event.name == it_event.name and guild_event.location == it_event.voice_channel:
                existingEvent = True
                it_event.created = True
                it_event.text_channel = interaction.channel
                event = it_event
                break
        # Event does not exist
        if not existingEvent:
            participants = get_participants_from_interaction(event_name=guild_event.name, interaction=interaction)
            scheduler = None
            for participant in participants:
                participant.answered = True
                if participant.member.id == interaction.user.id:
                    scheduler = participant
            start_times = [guild_event.start_time.astimezone()]
            image_url = None
            if guild_event.cover_image is not None:
                image_url = guild_event.cover_image.url
            event = Event(name=guild_event.name,
                          voice_channel=guild_event.location,
                          guild=interaction.guild,
                          text_channel=interaction.channel,
                          image_url=image_url,
                          scheduler=scheduler,
                          participants=participants,
                          start_times=start_times,
                          created=True)
            client.events.append(event)
            await event.save_image_to_file()
            event.scheduled_events.append(guild_event)
            event.start_times.append(guild_event.start_time)
        await event.update_event_buttons_message()
        await interaction.followup.send('Success!', ephemeral=True)
    else:
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
    await update_event_timeouts()
    await update_client_presence()

    # Participant availability checks
    for event in client.events:
        # If availability expires before the event is created, mark the participant as unanswered
        if not event.created:
            latest_date = event.get_latest_date()
            for participant in event.participants:
                participant.confirm_answered(duration=event.duration, latest_date=latest_date)
            await event.update_availability_message()
        # Remove this event from each participant's other availabilities
        else:
            for participant in event.participants:
                for other_event in client.events:
                    if other_event != event and not other_event.created:
                        for other_participant in other_event.participants:
                            if other_participant.member.id == participant.member.id:
                                other_participant.remove_availability_for_event(event_name=event.name, event_start_times=event.start_times, event_duration=event.duration)
                                break

    for event in client.events.copy():
        # Countdown to start + 5 minute warning
        if event.created and not event.started:
            # Countdown
            await event.update_event_buttons_message()
            # Send 5 minute warning
            if not event.five_minute_warning_flag:
                if datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(minutes=5) == event.start_times[0] and event.scheduled_events[0].status == EventStatus.scheduled and not event.started:
                    await event.send_five_minute_warning()
            await event.start_if_participants_in_vc()
            continue

        # Skip the rest of update() for this event if it is created or if we are waiting for answers
        if event.created:
            await event.end_if_participants_leave_vc()
            continue
        elif not event.has_everyone_answered():
            continue

        await event.update_availability_message()
        # Compare availabilities
        try:
            event.compare_availabilities()
        except Exception as e:
            logger.error(f'[{event}] Error comparing availabilities: {e}')
            continue

        # Cancel the event if no common availability was found
        if not event.ready_to_create:
            try:
                logger.info(f'[{event}] No common availability found')
                await event.cancel(reason="No common availability was found.")
            except Exception as e:
                logger.error(f'[{event}] Error messaging participants: {e}')
            continue
        # Create the event if it is ready to create
        else:
            # If start time is in the past, start it after the preset delay
            cur_time = datetime.now().astimezone().replace(second=0, microsecond=0)
            if event.start_times[0] <= cur_time:
                logger.warning(f'[{event}] Tried to create event in the past! Moving to {START_TIME_DELAY} minutes from now.')
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
                logger.error(f'[{event}] Error creating scheduled event: {e}')
                continue

            # Delete availability message, send event buttons message
            await event.update_messages()

            # Go through created events and remove availability during the event time of all shared participants
            for other_event in client.events:
                if other_event != event:
                    for participant in other_event.participants:
                        participant.remove_availability_for_event(event_name=event.name, event_start_times=event.start_times, event_duration=event.duration)

            # If there is an active event in the same location, disable the start button
            if location_has_active_event(event.voice_channel):
                event.event_buttons.start_button.disabled = True
    save()


@update.before_loop
async def before_update():
    now = datetime.now().astimezone()
    if now.second < 30:
        next_half_minute = now.replace(second=0) + timedelta(seconds=30)
    else:
        next_half_minute = now.replace(second=30) + timedelta(seconds=30)
    seconds_until_interval = (next_half_minute - now).total_seconds()
    logger.info(f'[{client.user}] Sleeping for {seconds_until_interval} seconds before update')
    await asyncio.sleep(seconds_until_interval)

client.run(DISCORD_TOKEN)
