'''Written by Cael Shoop.'''

import os
import sys
import time
import signal
import logging
import asyncio
import aiohttp
from typing import Literal
from dotenv import load_dotenv
from typing import Optional
from datetime import datetime, timedelta
from discord import (app_commands, Interaction, Intents, Client, Embed, Color, Activity,
                     ButtonStyle, EntityType, TextChannel, ActivityType, Status, EventStatus,
                     VoiceChannel, Message, SelectOption, ScheduledEvent, Member,
                     Guild, PrivacyLevel, User, utils, NotFound, DiscordServerError)
from discord.ui import View, Button, Modal, TextInput, Select
from discord.ext import tasks

from libs.persistence import Persistence
from libs.participant import Participant, TimeBlock, print_date_time, print_time_until
from libs.help import HELP_EMBEDS
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

# Time in minutes to wait before making multi-event events
AVAILABILITY_COOLDOWN_MINUTES = 1

# Time in minutes to delay "immediate" start
START_TIME_DELAY = 30

# Time in minutes before an event start time to send reminder
REMINDER_TIME_MINUTES = 15

# Time in seconds between updates
UPDATE_INTERVAL: int = 1

# Default length of events in minutes
DEFAULT_EVENT_DURATION: int = 30

# Default time between events in minutes
EVENT_BUFFER_MINUTES: int = 0

# Seconds to wait before deleting a followup message
FOLLOWUP_DELAY_SECONDS: int = 3

# Number of updates before an event is cleared
UPDATES_PER_MINUTE: int = 60 // UPDATE_INTERVAL
MINUTES_PER_HOUR: int = 60
HOURS_PER_DAY: int = 24
DEFAULT_EVENT_TIMEOUT_DAYS: int = 7
EVENT_TIMEOUT_CONSTANT_DAYS: int = UPDATES_PER_MINUTE * MINUTES_PER_HOUR * HOURS_PER_DAY
DEFAULT_EVENT_TIMEOUT: int = EVENT_TIMEOUT_CONSTANT_DAYS * DEFAULT_EVENT_TIMEOUT_DAYS

SCHEDULE_AGAIN_TIMEOUT_DAYS: int = 8
SCHEDULE_AGAIN_TIMEOUT: int = UPDATES_PER_MINUTE * MINUTES_PER_HOUR * HOURS_PER_DAY * SCHEDULE_AGAIN_TIMEOUT_DAYS

RESEND_INTERVAL_HOURS: int = 23
RESEND_INTERVAL: int = UPDATES_PER_MINUTE * MINUTES_PER_HOUR * RESEND_INTERVAL_HOURS

OFFSET = DEFAULT_EVENT_TIMEOUT % RESEND_INTERVAL


def now() -> datetime:
    return datetime.now().astimezone().replace(second=0, microsecond=0)


def save() -> None:
    """
    Saves the bot's status by writing the client's events to a file.
    """
    persist.write(client.events_dict)


def get_time_str_from_minutes(minutes: int) -> str:
    """
    Makes a formatted string including weeks, days, hours, and minutes.

    Arguments
    ---------
    minutes: :class:`int`
        The number of minutes to format.
    """
    if minutes < 0:
        minutes *= -1
    output = []
    weeks = int(minutes // 60 // 24 // 7)
    if weeks != 0:
        output.append(f"{weeks} weeks" if weeks != 1 else "1 week")
    days = int(minutes // 60 // 24 % 7)
    if days != 0:
        output.append(f"{days} days" if days != 1 else "1 day")
    hours = int(minutes // 60 % 24)
    if hours != 0 and weeks == 0:
        output.append(f"{hours} hours" if hours != 1 else "1 hour")
    mins = int(minutes % 60)
    if mins != 0 and weeks == 0 and days == 0:
        output.append(f"{mins} minutes" if mins != 1 else "1 minute")
    if mins == 0 and hours == 0 and days == 0 and weeks == 0:
        output.append("0 minutes")
    return ", ".join(output)


# Add a 0 if the digit is < 10
def double_digit_string(digit_string: str) -> str:
    """
    Adds 0 if a digit string is < 10.

    Arguments
    ---------
    digit_string: :class`str`
        The digit string that may need a 0 inserted at the beginning.

    Returns
    -------
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


# Parse start time string
def parse_start_time(start_time: str) -> datetime:
    try:
        start_time_obj = datetime.fromisoformat(start_time)
    except Exception as e:
        try:
            start_time = start_time.strip()
            start_time = start_time.replace(':', '')
            if len(start_time) == 1 or len(start_time) == 2:
                start_time = start_time + '00'
            if len(start_time) == 3:
                start_time = '0' + start_time
            elif len(start_time) != 4:
                logger.info(f"Start time was not in iso format: {e}")
                raise Exception("Invalid start time format. Examples: \"1630\" or \"00:30\"")
            hour = int(start_time[:2])
            minute = int(start_time[2:])
            start_time_obj = now().replace(hour=hour, minute=minute)
        except Exception:
            logger.info(f"Start time was not in iso format: {e}")
            raise Exception("Invalid start time format. Examples: \"1630\" or \"00:30\"")
    while start_time_obj <= now():
        start_time_obj += timedelta(days=1)
    return start_time_obj


class SchedulerClient(Client):
    """
    Represents the Scheduler Client.

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
        self.schedule_again_events = []
        self.cur_presence_index = -1

    async def start_server(self):
        """
        Starts the client's server for accepting event scheduling json packets.
        """
        self.server_is_running = True
        asyncio.create_task(self.server.start_server())

    async def schedule_from_dict(self, data: dict) -> None:
        """
        The callback to process an event scheduling json packet.

        Arguments
        ---------
        data: :class:`dict`
            The json packet with the information necessary for scheduling an event.
        """
        logger.info(f"[{data['name']}] Schedule from dict triggered")
        guild = self.get_guild(data["guild_id"])
        text_channel = guild.get_channel(data["text_channel_id"])
        voice_channel = guild.get_channel(data["voice_channel_id"])
        await schedule(event_name=data["name"],
                       guild=guild,
                       text_channel=text_channel,
                       voice_channel=voice_channel,
                       scheduler_id=data["scheduler_id"],
                       image_url=data["image_url"],
                       include_exclude=data["include_exclude"],
                       usernames=data["usernames"],
                       roles=data["roles"],
                       duration=data["duration"],
                       multi_event=data["multi_event"])

    async def retrieve_events(self) -> None:
        """
        Load event data from the data file to resume operations after a restart.

        If an event has an invalid field, it will receive a default value or be discarded
        dependant on which field. Availability and event control buttons are reconfigured from scratch.
        """
        if not self.loaded_json:
            self.loaded_json = True
            events_data = persist.read()
            if events_data:
                for idx, event_data in enumerate(events_data['events']):
                    if idx != 0:
                        # Prevent rate limiting when loading data
                        time.sleep(3)
                    try:
                        event = await Event.from_dict(event_data)
                        if not event:
                            raise Exception('Failed to create event object')
                        client.events.append(event)
                        logger.info(f'[{event}] event loaded and added to client event list')
                    except Exception as e:
                        logger.error(f"[{event}] Could not add event to client event list: {e}")
                    try:
                        await event.update_messages()
                    except Exception as e:
                        logger.error(f"[{event}] Error updating messages: {e}")
            else:
                logger.info('No json data found')

    @property
    def events_dict(self) -> dict:
        """
        Shove all events into a dictionary for writing to the data file.

        Returns
        -------
        events_data: :class:`dict`
            A dict containing all of the data for each event.
        """
        events_data = {}
        events_data['events'] = [event.to_dict() for event in self.events]
        return events_data

    async def setup_hook(self):
        """
        Syncs the command tree with the guilds the client is in.
        """
        await self.tree.sync()


OWNER_ID = int(os.getenv('OWNER_ID'))
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

client = SchedulerClient(intents=Intents.all())


def handle_signal(signum, frame):
    logger.info(f"Received signal {signum}")
    update.stop()
    logger.info("Saving and exiting")
    save()
    loop = asyncio.get_running_loop()
    loop.create_task(cleanup())


async def cleanup():
    for event in client.events:
        if event.availability_buttons is not None:
            event.availability_buttons.respond_button.disabled = True
            event.availability_buttons.full_button.disabled = True
            event.availability_buttons.reuse_button.disabled = True
            event.availability_buttons.unsub_button.disabled = True
            event.availability_buttons.cancel_button.disabled = True
        if event.event_buttons is not None:
            event.event_buttons.start_end_button.disabled = True
            event.event_buttons.end_and_forget_button.disabled = True
            event.event_buttons.unsubscribe_button.disabled = True
            event.event_buttons.reschedule_button.disabled = True
            event.event_buttons.cancel_button.disabled = True
        logger.info(f"[{event}] Updating message with disabled buttons")
        await event.update_messages()
    for schedule_again_event in client.schedule_again_events.copy():
        await schedule_again_event.remove()
    logger.info("Closing client")
    await client.close()
    sys.exit(0)


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


class Event:
    """
    Represents an event that the bot will manage.

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
    image_url: :class:`Optional[str]`
        The url to an image to use for the event.
    scheduler: :class:`Optional[Participant]`
        The :class:`Participant` who scheduled the event.
    rescheduler: :class:`Optional[Participant]`
        The most recent :class:`Participant` to reschedule the event.
    participants: :class:`Optional[list]`
        The list of :class:`Participant`s invited to the event.
    duration: :class:`Optional[timedelta]`
        The duration of the event.
    multi_event: :class:`Optional[bool]`
        Whether or not this :class:`Event` will have multiple guild events.
    start_times: :class:`Optional[list]`
        The list of guild event start times.
    availability_message_lock: :class:`Optional[asyncio].Lock`
        The lock to prevent the availability message update from being called twice simultaneously.
    availability_message: :class:`Optional[Message]`
        The message object that is requesting availability from participants.
    availability_buttons: :class:`Optional[AvailabilityButtons]`
        The availability buttons attached to the availability message that users can
        use to submit their availability, unsubscribe, or cancel.
    event_buttons_message_lock: :class:`Optional[asyncio].Lock`
        The lock to prevent the event buttons message update from being called twice simultaneously.
    event_buttons_message: :class:`Optional[Message]`
        The event control buttons message. States the start time, time remaining until
        the start time, when the event was started, when the event was rescheduled,
        the event's duration, and when the event was ended.
    event_buttons: :class:`Optional[EventButtons]`
        The event control buttons attached to the event_buttons_message. These allow
        for starting, ending, unsubscribing from, rescheduling, and cancelling the event.
    ready_to_create: :class:`Optional[bool]`
        Indicator of whether (a) start time(s) has been set and the event(s) is(/are) ready to create.
    created: :class:`Optional[bool]`
        Indicator of whether or not the event has had (a) guild event(s) created.
    started: :class:`Optional[bool]`
        Indicator of whether or not the first in line guild event has been started.
    ended: :class:`Optional[bool]`
        Indicator of whether or not the first in line guild event has been ended.
    scheduled_events: :class:`Optional[list]`
        List of guild scheduled event objects.
    reminder_flag: :class:`Optional[bool]`
        Indicator of whether a reminder message has been sent.
    reminder_message: :class:`Optional[Message]`
        The message object warning participants that an event is starting soon.
    timeout_counter: :class:`Optional[int]`
        The event's time to live. Also used to resend the availability message for visibility.
    """

    def __init__(self,
                 name: str,
                 voice_channel: VoiceChannel,
                 guild: Guild,
                 text_channel: TextChannel,
                 image_url: Optional[str] = None,
                 scheduler: Optional[Participant] = None,
                 rescheduler: Optional[Participant] = None,
                 participants: Optional[list[Participant]] = None,
                 duration: Optional[timedelta] = timedelta(minutes=DEFAULT_EVENT_DURATION),
                 multi_event: Optional[bool] = False,
                 start_times: Optional[list] = None,
                 availability_message: Optional[Message] = None,
                 availability_buttons=None,
                 event_buttons_message: Optional[Message] = None,
                 event_buttons=None,
                 ready_to_create: Optional[bool] = False,
                 created: Optional[bool] = False,
                 started: Optional[bool] = False,
                 scheduled_events: Optional[list] = None,
                 reminder_flag: Optional[bool] = False,
                 reminder_message: Optional[Message] = None,
                 timeout_counter: Optional[int] = DEFAULT_EVENT_TIMEOUT) -> None:
        self.name: str = name
        self.guild: Guild = guild
        self.entity_type: EntityType = EntityType.voice
        self.text_channel: TextChannel = text_channel
        self.availability_message_lock: asyncio.Lock = asyncio.Lock()
        self.availability_message: Message = availability_message
        if voice_channel:
            self.voice_channel: VoiceChannel = voice_channel
        else:
            try:
                self.voice_channel: VoiceChannel = self.guild.voice_channels[0]
            except Exception as e:
                message = f"Failed to get voice channel: {e}"
                logger.exception(message)
                self.cancel(reason=message)
                return
        self.privacy_level = PrivacyLevel.guild_only
        self.scheduler: Participant = scheduler
        self.rescheduler: Participant = rescheduler
        self.participants: list[Participant] = participants
        self.image_url: str = image_url
        self.image_path: str = f'{self.name}.png'
        self.availability_buttons: AvailabilityButtons = availability_buttons
        self.event_buttons_message_lock: asyncio.Lock = asyncio.Lock()
        self.event_buttons_message: Message = event_buttons_message
        self.event_buttons: EventButtons = event_buttons
        self.ready_to_create: bool = ready_to_create
        self.created: bool = created
        self.started: bool = started
        self.ended: bool = False
        self.scheduled_events: list[ScheduledEvent] = scheduled_events if scheduled_events is not None else []
        self.reminder_flag: bool = reminder_flag
        self.reminder_message: Message = reminder_message
        self.start_times: list[datetime] = start_times or []
        self.duration: timedelta = duration
        self.multi_event: bool = multi_event
        self.timeout_counter: int = timeout_counter
        self.availability_input_timer = None
        self.previous_countdown: int = self.timeout_counter
        self.after_buttons: AfterButtons = None

    async def update(self) -> None:
        """
        Heartbeat of the event.
        Scheduling:
            Check timeout status, cancel if timed out.
            Confirm and trim availability of participants.
            Clean up any remnant removed times of participants.
            Cancel the event if everyone responded and no common availability was found.
            Ensure start time is in the future and create the event.
        Event Created:
            Send reminder message when appropriate.
            Start the event if all participants are in the voice channel.
            End the event if nobody is in the voice channel.
        """
        # Cancel the event if the text channel has vaporized
        text_channel = self.guild.get_channel(self.text_channel.id)
        if not text_channel:
            await self.remove()
            return
        # Remove participants who are not longer in the text channel
        keep_participants = []
        for participant in self.participants:
            if participant.member in self.text_channel.members:
                keep_participants.append(participant)
        self.participants = keep_participants
        if not self.created:
            # Timeout check
            cancelled = await self.update_timeout()
            if cancelled:
                return
            # Update availability message once per minute
            if self.timeout_minutes != self.previous_countdown:
                self.previous_countdown = self.timeout_minutes
                for participant in self.participants:
                    participant.confirm_answered(duration=self.duration)
                await self.create_if_possible()
                if not self.created:
                    await self.update_availability_message()
        # Event has been created
        else:
            if not self.started:
                # Recreate the event if it was manually cancelled
                if self.scheduled_events[0].status == EventStatus.cancelled:
                    self.created = False
                    self.ready_to_create = True
                    await self.create_if_possible()
                elif self.scheduled_events[0].status == EventStatus.active:
                    await self.start()
                elif self.scheduled_events[0].status == EventStatus.ended:
                    await self.start()
                    await self.end()
                # Update event buttons message once per minute
                if self.mins_until_start != self.previous_countdown:
                    self.previous_countdown = self.mins_until_start
                    await self.update_event_buttons_message()
                # If reminder message has not been sent yet
                if not self.reminder_flag:
                    if now() + timedelta(minutes=REMINDER_TIME_MINUTES) == self.start_times[0]:
                        await self.send_reminder_message()
                # Reminder message has been sent
                else:
                    await self.start_if_participants_in_vc()
            # Event has started
            else:
                if self.scheduled_events[0].status == EventStatus.cancelled:
                    await self.cancel(reason="Event cancelled manually.")
                elif self.scheduled_events[0].status == EventStatus.ended:
                    await self.end(reason="Event ended manually.")
                else:
                    await self.end_if_participants_leave_vc()
                return

    async def update_timeout(self) -> bool:
        """
        Updates the event's timeout counter,
        resends the availability message every RESEND_INTERVAL hours,
        and cancels the event if it times out.

        Returns
        -------
        cancelled: :class:`bool`
            Whether or not the event timed out and was cancelled.
        """
        if self.created:
            return
        cancelled = False
        self.timeout_counter -= 1
        if self.timeout_counter > 0:
            # Resend availability message
            if (self.timeout_counter - OFFSET) % RESEND_INTERVAL == 0:
                await self.delete_availability_message()
                await self.update_availability_message()
        # Event has timed out
        else:
            notif_msg = f"{self.get_names_string(subscribed_only=True, mention=True)}\n"
            notif_msg += f"Scheduling for **{self}** has timed out and has been cancelled.\n"
            logger.info(f"[{self}] timed out and is being cancelled")
            await self.cancel(reason=notif_msg)
            cancelled = True
        return cancelled

    async def create_if_possible(self) -> None:
        if not self.created:
            if self.everyone_answered:
                self.compare_availabilities()
                # Create the event
                if self.ready_to_create:
                    # Create the event
                    await self.make_scheduled_events()
                    remove_times_from_availabilities_for_events()
                    self.previous_countdown = self.mins_until_start
                    for event in client.events:
                        await event.update_messages()
                    return

    def intersect_time_blocks(self, timeblocks1: list, timeblocks2: list) -> list[TimeBlock]:
        """
        Gets all timeblocks in the two availabilities that intersect.

        Arguments
        ---------
        timeblocks1: :class:`list`
            The first availability to compare.
        timeblocks2: :class:`list`
            The second availability to compare.

        Returns
        -------
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
        """
        Compares availabilites of all subscribed participants to select (a) start time(s) for the event.
        """
        if self.created or self.ready_to_create:
            return
        subbed_participants = []
        for participant in self.participants:
            if participant.subscribed:
                subbed_participants.append(participant)

        current_time = now() + timedelta(minutes=START_TIME_DELAY)

        # Get events in the same voice channel and then their timeblocks
        conflicting_events = [event for event in client.events if event.voice_channel == self.voice_channel and event.created]
        occupied_timeblocks = [
            TimeBlock(start_time=event.start_times[0], end_time=event.start_times[0] + event.duration)
            for event in conflicting_events
        ]

        # Check if the voice channel is available in [START_TIME_DELAY] minutes
        all_participants_and_vc_available = True
        self_timeblock = TimeBlock(start_time=current_time, end_time=current_time + self.duration)
        for occupied_timeblock in occupied_timeblocks:
            if occupied_timeblock.overlaps_with(self_timeblock):
                all_participants_and_vc_available = False
                break

        # Check if all participants are available in [START_TIME_DELAY] minutes
        for participant in subbed_participants:
            if not participant.is_available_at(current_time, self.duration):
                all_participants_and_vc_available = False
                break

        # Make an event if all participants are available when the voice channel is next available,
        # then exit if it's a single event or continue if it's a multi event
        dates_scheduled = []
        cur_date = current_time.date()
        if all_participants_and_vc_available:
            self.start_times.append(current_time)
            self.reminder_flag = True
            self.ready_to_create = True
            if not self.multi_event:
                return
            dates_scheduled.append(cur_date)

        # Find the earliest common availability

        # Get all availabilities
        available_timeblocks = [participant.availability for participant in subbed_participants]

        # Get intersected availability
        intersected_timeblocks = available_timeblocks[0]
        for timeblocks in available_timeblocks[1:]:
            intersected_timeblocks = self.intersect_time_blocks(intersected_timeblocks, timeblocks)

        # Remove conflicting time blocks
        filtered_timeblocks = []
        for timeblock in intersected_timeblocks:
            remaining_blocks = [timeblock]
            for occupied_timeblock in occupied_timeblocks:
                new_blocks = []
                for block in remaining_blocks:
                    new_blocks.extend(block.subtract(occupied_timeblock))
                remaining_blocks = new_blocks
            filtered_timeblocks.extend(remaining_blocks)

        # Find valid start times
        for timeblock in filtered_timeblocks:
            date_scheduled = False
            tb_date = timeblock.start_time.date()
            for date in dates_scheduled:
                if tb_date.month == date.month and tb_date.day == date.day and tb_date.year == date.year:
                    date_scheduled = True
                    break
            if timeblock.duration >= self.duration and not date_scheduled:
                self.start_times.append(timeblock.start_time)
                self.ready_to_create = True
                dates_scheduled.append(tb_date)

        # Limit to one event if not scheduled as a multi event
        if not self.multi_event and len(self.start_times) > 1:
            self.start_times = self.start_times[:1]

    async def reschedule(self, rescheduler: Participant = None) -> None:
        self.reset_timeout_counter()
        for scheduled_event in self.scheduled_events:
            try:
                if rescheduler is not None:
                    await scheduled_event.delete(reason=f"Event rescheduled by {rescheduler}.")
                else:
                    await scheduled_event.delete(reason="Event rescheduled.")
            except Exception as e:
                logger.error(f"[{self}] Error cancelling guild event to reschedule: {e}")
        self.scheduled_events.clear()
        self.start_times.clear()
        self.ready_to_create = False
        self.created = False
        self.reminder_flag = False
        if rescheduler is not None:
            rescheduler.set_no_availability()
        await self.delete_reminder_message()
        await self.update_event_buttons_message()
        if rescheduler is not None:
            await self.update_availability_message(rescheduler=rescheduler)
        else:
            await self.update_availability_message()
        # Restore removed availabilities
        for other_event in client.events:
            other_event.restore_availabilities(self)
            await other_event.update_messages()

    def get_reminder_message_content(self) -> str:
        return self.get_names_string(subscribed_only=True, mention=True, not_in_voice_channel_only=True)

    def get_reminder_message_embed(self) -> Embed:
        embed = Embed(title="Event Reminder!",
                      description=f"{self} is scheduled to start in {get_time_str_from_minutes(self.mins_until_start)}.",
                      color=Color.orange())
        embed.timestamp = self.start_times[0]
        if self.image_url:
            embed.set_thumbnail(url=self.image_url)
        embed.set_footer(text="Courtesy of Event Scheduler", icon_url=client.user.avatar.url)
        return embed

    async def send_reminder_message(self) -> None:
        """Sends the reminder message."""
        if self.reminder_flag:
            return
        self.reminder_flag = True
        # No need to send reminder message if all
        # participants are already in the voice channel
        if all(participant.member in self.voice_channel.members for participant in self.participants):
            return
        content = self.get_reminder_message_content()
        embed = self.get_reminder_message_embed()
        self.reminder_message = await self.text_channel.send(content=content,
                                                             embed=embed,
                                                             reference=self.event_buttons_message)

    async def update_reminder_message(self) -> None:
        if self.reminder_message is not None:
            content = self.get_reminder_message_content()
            try:
                await self.reminder_message.edit(content=content)
            except DiscordServerError as e:
                logger.error(f"[{self}] Discord server error while editing reminder message: {e}")
            except Exception as e:
                logger.exception(f"[{self}] Error editing reminder message: {e}")

    async def delete_reminder_message(self) -> None:
        """Deletes the reminder message."""
        if self.reminder_message is not None:
            await self.reminder_message.delete()
            self.reminder_message = None

    async def start(self, reason: Optional[str] = f"Event started by {client.user}.") -> None:
        """
        Starts the event.

        Arguments
        ---------
        reason: :class:`Optional[str]`
            Reason to provide for guild event start in audit log.
        """
        logger.info(f"[{self}] Starting, reason: {reason}")
        await self.delete_reminder_message()
        try:
            await self.scheduled_events[0].start(reason=reason)
        except Exception as e:
            logger.error(f"[{self}] Failed to start: {e}")
            return
        try:
            self.start_times[0] = now()
        except Exception as e:
            logger.warning(f"[{self}] Error getting start time: {e}")
            self.start_times.append(now())
        self.started = True
        self.event_buttons.convert()
        await self.update_event_buttons_message()
        # Push back start times of all other events that share
        # this location to start after the end of this event
        buffer_time = timedelta(minutes=EVENT_BUFFER_MINUTES)
        buffered_end = self.start_times[0] + self.duration + buffer_time
        affected_events = []
        for event in client.events:
            if event == self or not event.created:
                continue
            if event.voice_channel == self.voice_channel or event.shares_participants(self):
                affected_events.append(event)
        for event in sorted(affected_events, key=lambda e: min(e.start_times)):
            event.start_times[0] = max(event.start_times[0], buffered_end)
            buffered_end = event.start_times[0] + event.duration + buffer_time
            await event.update_event_buttons_message()
        # Disable start buttons of events scheduled for the same channel
        for event in client.events:
            if event == self or not event.created or event.voice_channel != self.voice_channel:
                continue
            event.event_buttons.start_end_button.disabled = True
            await event.update_event_buttons_message()

    async def start_if_participants_in_vc(self) -> None:
        """
        Starts the event if all of the participants are in the voice channel
        and there are no active events in that voice channel.
        """
        if now() < self.start_times[0] - timedelta(REMINDER_TIME_MINUTES):
            return
        for event in client.events:
            if event is not self and event.voice_channel is self.voice_channel and event.started:
                return
        if all(participant.member in self.voice_channel.members for participant in self.participants):
            await self.start(f"Event started by {client.user} because all users were in the voice channel.")
        else:
            await self.update_reminder_message()

    async def end(self, reason: Optional[str] = f"Event ended by {client.user}.", forget: Optional[bool] = False) -> None:
        """
        Ends the event. If there are more scheduled events in this event, shift them forward and prep them.

        Arguments
        ---------
        reason: :class:`Optional[str]`
            The reason to provide to the audit log for ending the guild event.
        """
        logger.info(f"[{self}] Ending, reason: {reason}")
        self.ended = True
        # Delete scheduled event
        try:
            await self.scheduled_events[0].delete(reason=reason)
        except Exception as e:
            logger.error(f"[{self}] Error in event control end button callback while ending scheduled event: {e}")
        # Update event buttons message
        self.event_buttons = None
        if self.event_buttons_message is not None:
            await self.event_buttons_message.unpin()
            end_time: datetime = now()
            content = self.get_event_buttons_message_content(end_time)
            embeds = self.get_event_buttons_message_embeds(end_time)
            buttons = None if (self.has_more_events or forget) else self.get_after_buttons()
            try:
                if buttons is not None:
                    logger.debug(f"[{self}] Editing event buttons message with after buttons")
                    buttons.message = await self.event_buttons_message.edit(content=content, embeds=embeds, view=buttons)
                else:
                    logger.debug(f"[{self}] Editing event buttons message to remove view")
                    await self.event_buttons_message.edit(content=content, embeds=embeds, view=None)
            except Exception as e:
                logger.error(f"[{self}] Error in event control end button callback while editing event buttons message: {e}")
            self.event_buttons_message = None
        # Re-enable start buttons of appropriate events
        for event in client.events:
            if event == self or not event.created or event.voice_channel != self.voice_channel:
                continue
            event.event_buttons.start_end_button.disabled = False
            logger.info(f'[{self}] Re-enabled start button for event with same location: {event}')
        await self.prep_next_scheduled_event()
        # Restore removed availabilities
        for event in client.events:
            if event is not self:
                event.restore_availabilities(self)
                await event.update_messages()

    async def end_if_participants_leave_vc(self) -> None:
        """
        Ends the event if all of the participants have left the voice channel.
        """
        if not any(participant.member in self.voice_channel.members for participant in self.participants):
            await self.end(f'Event ended by {client.user} because no users were in the voice channel.')

    async def prep_next_scheduled_event(self) -> None:
        """Preps the next guild scheduled event and update the event control buttons message."""
        if len(self.scheduled_events) > 1 and len(self.start_times) > 1:
            try:
                self.start_times = self.start_times[1:]
                self.scheduled_events = self.scheduled_events[1:]
            except Exception as e:
                logger.error(f"[{self}] Error shifting scheduled_events and start_times: {e}")
            self.reminder_flag = bool((now() + timedelta(minutes=REMINDER_TIME_MINUTES)) < self.start_times[0])
            if self.event_buttons_message is not None:
                await self.event_buttons_message.edit(view=None)
                self.event_buttons_message = None
            if self.reminder_message is not None:
                await self.reminder_message.delete()
                self.reminder_message = None
            self.reminder_message = False
            self.event_buttons = None
            self.started = False
            self.ended = False
            await self.update_event_buttons_message()
            logger.info(f"[{self}] Next event starts at {self.start_times[0]}")
        else:
            logger.info(f"[{self}] Last event ended")
            self.remove()

    async def make_scheduled_events(self) -> None:
        """
        Creates a scheduled event for each start time and sets the guild event's image if appropriate.
        """
        if len(self.name) > 100:
            self.name = self.name[:99]
        for i, start_time in enumerate(self.start_times):
            # Ensure start time is in the future
            if start_time <= now() + timedelta(seconds=5):
                logger.warning(f"[{self}] Tried to create event in the past, moving to {START_TIME_DELAY} minutes from now")
                self.start_times[i] = now() + timedelta(minutes=START_TIME_DELAY)
                start_time = self.start_times[i]
            scheduled_event = await self.guild.create_scheduled_event(name=self.name,
                                                                      description='Bot-generated event',
                                                                      start_time=start_time,
                                                                      entity_type=self.entity_type,
                                                                      channel=self.voice_channel,
                                                                      privacy_level=self.privacy_level)
            if scheduled_event is not None:
                await self.save_image_to_file()
                if self.has_image_saved:
                    await scheduled_event.edit(image=self.get_image())
                self.scheduled_events.append(scheduled_event)
                logger.info(f'[{self}] Created event starting {start_time.strftime("%A, %m/%d/%Y: %H:%M %Z")}')
                self.ready_to_create = False
                self.created = True
            else:
                logger.error(f"[{self}] Failed to create event!")
        self.reminder_flag = bool((now() + timedelta(minutes=REMINDER_TIME_MINUTES)) < self.start_times[0])

    def start_input_timer(self) -> None:
        """Starts the availability input timer."""
        self.availability_input_timer = datetime.now().astimezone()

    def stop_input_timer(self) -> None:
        """Stops the availability input timer."""
        self.availability_input_timer = None

    @property
    def input_timer_running(self) -> bool:
        """
        Checks if the availability input timer is running.

        Returns
        -------
        running: :class:`bool`
            True if the timer is running, otherwise False.
        """
        return self.availability_input_timer is not None

    @property
    def input_timer_elapsed(self) -> bool:
        """
        Checks if the availability input timer has expired.

        Returns
        -------
        elapsed: :class:`bool`
            True if the timer has elapsed, otherwise False.
        """
        if self.input_timer_running:
            if (self.availability_input_timer + timedelta(minutes=AVAILABILITY_COOLDOWN_MINUTES)) <= datetime.now().astimezone():
                self.stop_input_timer()
                return True
        return False

    def get_general_embed(self, end_time: Optional[datetime] = None) -> Embed:
        """
        Gets the general embed for the event with status, image thumbnail, duration, location, etc.

        Arguments
        ---------
        end_time: :class:`Optional[datetime]`
            If the event has ended, includes the provided end time in the embed.

        Returns
        -------
        embed: :class:`Embed`
            The general embed for the event.
        """
        embed = Embed(title=f"{self}",
                      description=self.scheduling_status,
                      color=Color.green())
        if self.image_url:
            embed.set_thumbnail(url=self.image_url)
        embed.add_field(name="Duration",
                        value=self.duration_string,
                        inline=False)
        embed.add_field(name="Location",
                        value=f"{self.text_channel.mention}\n{self.voice_channel.mention}",
                        inline=False)
        embed.add_field(name="Multi Event",
                        value=f"{self.multi_event}",
                        inline=False)
        if not self.created:
            embed.add_field(name="Times out in",
                            value=f"{get_time_str_from_minutes(self.timeout_minutes)}",
                            inline=False)
        else:
            if end_time is None and not self.started:
                if self.mins_until_start > 0:
                    embed.add_field(name="Starting in",
                                    value=f"{print_time_until(self.start_times[0])}",
                                    inline=False)
                elif self.mins_until_start == 0:
                    embed.add_field(name="Starting soon", value="", inline=False)
                else:
                    embed.add_field(name="Overdue by",
                                    value=f"{print_time_until(self.start_times[0])}",
                                    inline=False)
            # Event is in progress
            elif end_time is None and self.started:
                embed.timestamp = self.start_times[0]
                embed.add_field(name="Started",
                                value=f"{self.get_start_time_string()}",
                                inline=False)
            # Event has ended
            else:
                embed.add_field(name="Ended",
                                value=f'{print_date_time(end_time)}',
                                inline=False)
        if self.created:
            if len(self.start_times) > 0:
                start_times = ""
                for start_time in self.start_times:
                    start_times += f"{print_date_time(start_time)}\n"
                embed.add_field(name="Occurrences",
                                value=start_times,
                                inline=False)
            embed.timestamp = self.start_times[0]
        if self.scheduler:
            if self.scheduler.member.avatar:
                embed.set_footer(text=f"Scheduled by {self.scheduler}",
                                 icon_url=self.scheduler.member.avatar.url)
            else:
                embed.set_footer(text=f"Scheduled by {self.scheduler}")
        if self.rescheduler:
            if self.rescheduler.member.avatar:
                embed.set_footer(text=f"Rescheduled by {self.rescheduler}",
                                 icon_url=self.rescheduler.member.avatar.url)
            else:
                embed.set_footer(text=f"Rescheduled by {self.rescheduler}")
        return embed

    async def save_image_to_file(self) -> None:
        """
        Saves the image from the url to a file to allow for sending in messages.
        """
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
        """
        Gets the image from the file as bytes for use in messages.

        Returns
        -------
        image_bytes: :class:`bytes`
            The image file loaded as bytes.
        """
        return open(self.image_path, 'rb').read()

    def delete_image_file(self) -> None:
        """
        Deletes the image file if one has been downloaded for the event.
        """
        if not self.has_image_saved:
            return
        try:
            os.remove(self.image_path)
            logger.info(f"[{self}] Deleted image file")
        except Exception as e:
            logger.exception(f"[{self}] Failed to delete image: {e}")

    def get_names_string(self,
                         subscribed_only: bool = False,
                         unsubscribed_only: bool = False,
                         unanswered_only: bool = False,
                         mention: bool = False,
                         not_in_voice_channel_only: bool = False) -> str:
        """
        Gets a string of names meeting the criteria provided through arguments.

        Arguments
        ---------
        subscribed_only: :class:`bool`
            Only include subscribed participants in the string.
        unsubscribed_only: :class:`bool`
            Only include unsubscribed participants in the string.
        unanswered_only: :class:`bool`
            Only include unanswered participants in the string.
        mention: :class:`bool`
            Use mentions instead of nicknames or usernames.
        not_in_voice_channel_only: :class:`bool`
            Only include users who are not in the event's voice channel.
        """
        names = []
        mentions = ''

        if subscribed_only and unsubscribed_only:
            subscribed_only = False
            unsubscribed_only = False
        voice_channel_members = self.voice_channel.members

        for participant in self.participants:
            if mention:
                name_string = f'{participant.member.mention} '
            else:
                name_string = f'{participant}'

            # No conditions are true
            if (not subscribed_only) and (not unsubscribed_only) and (not unanswered_only):
                if not not_in_voice_channel_only or (not_in_voice_channel_only and participant.member not in voice_channel_members):
                    mentions += name_string
                    names.append(name_string)

            # One condition is true
            if (subscribed_only and participant.subscribed) and (not unsubscribed_only) and (not unanswered_only):
                if not not_in_voice_channel_only or (not_in_voice_channel_only and participant.member not in voice_channel_members):
                    mentions += name_string
                    names.append(name_string)
            if (not subscribed_only) and (unsubscribed_only and not participant.subscribed) and (not unanswered_only):
                if not not_in_voice_channel_only or (not_in_voice_channel_only and participant.member not in voice_channel_members):
                    mentions += name_string
                    names.append(name_string)
            if (not subscribed_only) and (not unsubscribed_only) and (unanswered_only and not participant.answered):
                if not not_in_voice_channel_only or (not_in_voice_channel_only and participant.member not in voice_channel_members):
                    mentions += name_string
                    names.append(name_string)

            # Two conditions are true
            if (subscribed_only and participant.subscribed) and (unanswered_only and not participant.answered):
                if not not_in_voice_channel_only or (not_in_voice_channel_only and participant.member not in voice_channel_members):
                    mentions += name_string
                    names.append(name_string)
            if (unsubscribed_only and not participant.subscribed) and (unanswered_only and not participant.answered):
                if not not_in_voice_channel_only or (not_in_voice_channel_only and participant.member not in voice_channel_members):
                    mentions += name_string
                    names.append(name_string)

        if mention:
            return f'\n{mentions}'
        return ", ".join(names)

    def add_user_as_participant(self, user: User) -> None:
        """
        Adds the user to the event as a participant if they are not one already.

        Arguments
        ---------
        user: :class:`User` or :class:`Member`
            The user to add to the event.
        """
        if user.id not in [participant.member.id for participant in self.participants]:
            member = self.guild.get_member(user.id)
            participant = Participant(member=member)
            self.participants.append(participant)

    def get_participant(self, username_or_id) -> Participant:
        """
        Gets a participant with their nickname, username, or id.

        Arguments
        ---------
        username_or_id: :class:`str` or :class:`int`
            The nickname, username, or id to get the participant object for.

        Returns
        -------
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
        if type(username_or_id) is str:
            member = self.guild.get_member_named(username_or_id)
        else:
            member = self.guild.get_member(username_or_id)
        if member:
            participant = Participant(member)
            self.participants.append(participant)
            return participant
        return None

    def shares_participants(self, event) -> bool:
        """
        Indicates whether this event shares participants with the event provided.

        Arguments
        ---------
        event: :class:`Event`
            The event to compare participants with.

        Returns
        -------
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

    def shared_participants(self, event) -> list[Participant]:
        """
        Gets the list of this event's participants shared with the provided event.

        Arguments
        ---------
        event: :class:`Event`
            The event to compare participants with.

        Returns
        -------
        participants: :class:`list`
            The list of this event's participants shared with the other event.
            Empty if the events are the same or do not share participants.
        """
        participants = []
        if event is not self:
            for participant in self.participants:
                for other_participant in event.participants:
                    if participant.member.id == other_participant.member.id:
                        participants.append(participant)
        return participants

    def other_shared_participants(self, event, subscribed_only: Optional[bool] = False) -> list[Participant]:
        """
        Gets the list of the other event's participants shared with this event.

        Arguments
        ---------
        event: :class:`Event`
            The event to compare participants with.
        subscribed_only: :class:`Optional[bool]`
            Default: False. Whether or not to only return subscribed participants.

        Returns
        -------
        other_participants: :class:`list`
            The list of the other event's participants shared with this event.
            Empty if the events are the same or do not share participants.
        """
        other_participants = []
        if event is not self:
            for participant in self.participants:
                for other_participant in event.participants:
                    if participant.member.id == other_participant.member.id:
                        if (subscribed_only and other_participant.subscribed) or not subscribed_only:
                            other_participants.append(other_participant)
                        break
        return other_participants

    def get_other_availabilities(self, participant: Participant) -> list:
        """
        Gets availability of a participant from another event that they are in.

        Arguments
        ---------
        participant: :class:`Participant`
            The participant to get availability for.

        Returns
        -------
        event_availabilities: :class:`list`
            The list of availabilities from other events the participant is in.
        """
        event_availabilities = []
        # Get availabilities from other events in the same guild
        for other_event in client.events:
            if other_event != self and other_event.guild == self.guild:
                for other_participant in other_event.participants:
                    if other_participant.member.id == participant.member.id and other_participant.answered:
                        event_avail = EventAvailability(event=other_event,
                                                        avail=other_participant.availability,
                                                        full_flag=other_participant.full_availability_flag)
                        event_availabilities.append(event_avail)
                        break
        if event_availabilities:
            return event_availabilities
        # Get availabilities from all other events
        for other_event in client.events:
            if other_event != self:
                for other_participant in other_event.participants:
                    if other_participant.member.id == participant.member.id and other_participant.answered:
                        event_avail = EventAvailability(event=other_event,
                                                        avail=other_participant.availability,
                                                        full_flag=other_participant.full_availability_flag)
                        event_availabilities.append(event_avail)
                        break
        return event_availabilities

    def reset_timeout_counter(self) -> None:
        """
        Resets the timeout counter to the default value.
        """
        self.timeout_counter = DEFAULT_EVENT_TIMEOUT

    def get_start_time_string(self, index: int = 0) -> str:
        """
        Gets the string for the start time at the provided index.

        Arguments
        ---------
        index: :class:`int`
            Optional. Index of the start time to get the string for.
            Default: 0

        Returns
        -------
        start_time: :class:`str`
            The string for the start time.
        """
        if index >= 0 and len(self.start_times) > index:
            return f'{print_date_time(self.start_times[index])}'
        return ''

    def get_availability_request_content(self) -> str:
        """
        Gets the content string for the availability message.

        Returns
        -------
        output: :class:`str`
            The content string for the availability message.
        """
        output = ""
        if not self.everyone_answered:
            mentions = self.get_names_string(subscribed_only=True, unanswered_only=True, mention=True)
            if mentions.strip() == "" and self.multi_event and self.input_timer_running:
                output += f"\n\nWaiting {AVAILABILITY_COOLDOWN_MINUTES} minute(s) for additional multi event availabilities."
            else:
                output += f"\n\nWaiting for a response from:{mentions}"
        else:
            output += "\n\nEveryone has responded."
        return output

    def get_availability_request_embeds(self) -> list[Embed]:
        """
        Gets the embeds for the availability message.

        Returns
        -------
        embeds: :class:`list[Embed]`
            The list of embeds for the availability message.
        """
        # Event info embed
        embeds = [self.get_general_embed()]
        # Availabilities embed
        embeds.append(self.get_availability_embed())
        return embeds

    def get_availability_embed(self) -> Embed:
        """
        Gets the availability embed.

        Returns
        -------
        embed: :class:`Embed`
            The embed containing each participant's availability.
        """
        embed = Embed(title='Availabilities', color=Color.blue())
        status = self.scheduling_status
        if status == "No common availability" or status == "Awaiting availability":
            embed.description = status
        for participant in self.participants:
            participantName = f'{participant}'
            availString = participant.availability_string
            if availString != "":
                embed.add_field(name=participantName, value=availString, inline=False)
            if not participant.subscribed:
                embed.add_field(name=participantName, value="[Unsubscribed]", inline=False)
        return embed

    def restore_availabilities(self, event) -> None:
        """
        Restores availabilities that were modified by the provided event's creation.

        Arguments
        ---------
        event: :class:`Event`
            The name of the event to restore availability for shared participants.
        """
        if event == self:
            return
        for participant in self.participants:
            participant.restore_availability_for_event(event.name)
            participant.confirm_answered(duration=self.duration)

    def update_availabilities_to(self, participant: Participant) -> None:
        """
        Updates end time of full flag availabilities to the latest time.

        Arguments
        ---------
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

    def get_event_buttons_message_content(self, end_time: Optional[datetime] = None) -> str:
        """
        Gets the content for the event buttons message.

        Returns
        -------
        content: :class:`str`
            The content for the event buttons message.
        """
        if not self.started:
            if self.reminder_flag:
                return self.get_names_string(subscribed_only=True, mention=True, not_in_voice_channel_only=True)
            else:
                return self.get_names_string(subscribed_only=True, mention=True)
        else:
            return ""

    def get_event_buttons_message_embeds(self, end_time: Optional[datetime] = None) -> list[Embed]:
        """
        Gets the embeds for the event buttons message.

        Arguments
        ---------
        end_time: :class:`Optional[datetime]`
            The end time of the event to put in the embed.

        Returns
        -------
        embed: :class:`list[Embed]`
            The embeds for the event buttons message.
        """
        if end_time is not None:
            # Replace duration with actual duration
            self.duration: timedelta = end_time - self.start_times[0]
        embeds = [self.get_general_embed(end_time=end_time)]
        return embeds

    async def update_messages(self) -> None:
        """
        Update the availability and event buttons messages.
        """
        await self.update_availability_message()
        await self.update_event_buttons_message()

    async def update_availability_message(self, rescheduler: Optional[Participant] = None) -> None:
        """
        Update the availability message.

        Arguments
        ---------
        rescheduler: :class:`Participant`
            Optional. The participant who rescheduled the event.
            Default: None
        """
        async with self.availability_message_lock:
            # Delete the message if the event was created
            if self.created:
                await self.delete_availability_message()
                return
            if rescheduler is not None:
                self.scheduler = None
                self.rescheduler = rescheduler
            content = self.get_availability_request_content()
            embeds = self.get_availability_request_embeds()
            if self.availability_buttons is None:
                self.availability_buttons = AvailabilityButtons(event=self)
            # Send a new message
            if self.availability_message is None:
                self.availability_message = await self.text_channel.send(content=content,
                                                                         embeds=embeds,
                                                                         view=self.availability_buttons)
                try:
                    await self.availability_message.pin()
                except DiscordServerError.HTTPException:
                    pass
                except Exception as e:
                    logger.error(f'[{self}] Failed to pin availability message: {e}')
            # Update existing message
            else:
                try:
                    await self.availability_message.edit(content=content,
                                                         embeds=embeds,
                                                         view=self.availability_buttons)
                except NotFound as e:
                    logger.warning(f'[{self}] Availability message not found, sending a new one: {e}')
                    self.availability_message = await self.text_channel.send(content=content,
                                                                             embeds=embeds,
                                                                             view=self.availability_buttons)
                    try:
                        await self.availability_message.pin()
                    except DiscordServerError.HTTPException:
                        pass
                    except Exception as e:
                        logger.error(f'[{self}] Failed to pin availability message: {e}')
                except Exception as e:
                    logger.error(f'[{self}] Failed to edit availability message: {e}')

    async def delete_availability_message(self) -> None:
        if self.availability_message is not None:
            await self.availability_message.delete()
            self.availability_message = None
        self.availability_buttons = None

    async def update_event_buttons_message(self) -> None:
        """Updates the event buttons message."""
        async with self.event_buttons_message_lock:
            # Delete the message if the event was rescheduled
            if not self.created:
                await self.delete_event_buttons_message()
                return
            content = self.get_event_buttons_message_content()
            embeds = self.get_event_buttons_message_embeds()
            if not self.event_buttons:
                self.event_buttons = EventButtons(self)
            # Send a new message
            if self.event_buttons_message is None:
                self.event_buttons_message = await self.text_channel.send(content=content,
                                                                          embeds=embeds,
                                                                          view=self.event_buttons)
                try:
                    await self.event_buttons_message.pin()
                except DiscordServerError.HTTPException:
                    pass
                except Exception as e:
                    logger.error(f'[{self}] Failed to pin event buttons message: {e}')
            # Edit existing message
            else:
                try:
                    await self.event_buttons_message.edit(content=content,
                                                          embeds=embeds,
                                                          view=self.event_buttons)
                except NotFound as e:
                    logger.warning(f'[{self}] Event buttons message not found, sending a new one: {e}')
                    self.event_buttons_message = await self.text_channel.send(content=content,
                                                                              embeds=embeds,
                                                                              view=self.event_buttons)
                    try:
                        await self.event_buttons_message.pin()
                    except DiscordServerError.HTTPException:
                        pass
                    except Exception as e:
                        logger.error(f'[{self}] Failed to pin availability message: {e}')
                except Exception as e:
                    logger.error(f'[{self}] Failed to edit event buttons message: {e}')

    async def delete_event_buttons_message(self) -> None:
        """Deletes the event buttons message."""
        if self.event_buttons_message is not None:
            await self.event_buttons_message.delete()
            self.event_buttons_message = None
        self.event_buttons = None

    def get_cancel_embed(self, reason: Optional[str] = "", canceller: Optional[str] = "") -> Embed:
        """
        Get the embed for the cancel message.

        Arguments
        ---------
        reason: :class:`str`
            The reason the event is being cancelled.
        canceller: :class:`str`
            The name of the canceller of the event.

        Returns
        -------
        embed: :class:`Embed`
            The cancel message embed.
        """
        embed = Embed(title="Event Cancelled",
                      description=f"{self} has been cancelled.",
                      color=0xFF0000)
        if self.image_url:
            embed.set_thumbnail(url=self.image_url)
        if reason != "":
            embed.add_field(name="Reason for Cancellation", value=reason, inline=False)
        if canceller != "":
            for participant in self.participants:
                participant_name = participant.member.name
                if participant.member.nick:
                    participant_name = participant.member.nick
                if participant_name == canceller:
                    if participant.member.avatar:
                        embed.set_footer(text=f"Cancelled by {participant}",
                                         icon_url=participant.member.avatar.url)
                    else:
                        embed.set_footer(text=f"Cancelled by {participant}")
                    break
        return embed

    def get_after_buttons(self) -> View:
        if not self.ended:
            return None
        if not self.after_buttons:
            self.after_buttons = AfterButtons(self)
        return self.after_buttons

    async def cancel(self, reason: Optional[str] = "", canceller: Optional[str] = "") -> None:
        """
        Cancels the event.

        Arguments
        ---------
        reason: :class:`str`
            The reason for the cancellation of the event.
        canceller: :class:`str`
            The name of the canceller of the event.
        """
        content = self.get_names_string(subscribed_only=True, mention=True)
        embed = self.get_cancel_embed(reason, canceller)
        buttons = self.get_after_buttons()
        if buttons:
            buttons.message = await self.text_channel.send(content=content, embed=embed, view=buttons)
        else:
            await self.text_channel.send(content=content, embed=embed)
        if not self.created:
            await self.delete_availability_message()
        else:
            await self.delete_event_buttons_message()
            await self.delete_reminder_message()
        try:
            if len(self.scheduled_events) > 0:
                await self.scheduled_events[0].delete(reason=f'Cancel button pressed by {canceller}: {reason}')
        except Exception as e:
            logger.error(f'[{self}] Error in cancel while deleting scheduled event: {e}')
        await self.prep_next_scheduled_event()
        # Restore removed availabilities
        for event in client.events:
            event.restore_availabilities(self)
            await event.update_messages()

    def remove(self) -> None:
        """
        Deletes the event's image file and removes the event from the client's event list.
        """
        self.delete_image_file()
        client.events.remove(self)
        logger.info(f'[{self}] Removed from client events list')

    def get_limited_name(self, length: int) -> str:
        if length < 3:
            raise Exception("Invalid name length; must be at least 3.")
        return self.name if len(self.name) <= length else f"{self.name[:length-3]}..."

    @property
    def has_any_events(self) -> bool:
        """
        Returns
        -------
        more_events: :class:`bool`
            True if the event has future occurences.
            False if this is the last occurence.
        """
        return len(self.scheduled_events) >= 1

    @property
    def has_more_events(self) -> bool:
        """
        Returns
        -------
        more_events: :class:`bool`
            True if the event has future occurences.
            False if this is the last occurence.
        """
        return self.multi_event and len(self.scheduled_events) > 1

    @property
    def scheduling_status(self) -> str:
        """
        Gets the current event status.

        Returns
        -------
        status: :class:`str`
            A string describing the current status of the event.
        """
        if self.ended:
            return "Event ended"
        if self.started:
            return "Event started"
        if self.created:
            return "Event created"
        if self.ready_to_create:
            return "Creating event"
        if self.everyone_answered:
            return "No common availability"
        elif self.multi_event and self.input_timer_running:
            return "Waiting for additional availabilities"
        return "Awaiting availability"

    @property
    def everyone_answered(self) -> bool:
        """
        Indicates whether or not all participants have responded.

        Returns
        -------
        True
            If all participants have responded.
        False
            If at least one participant has not yet responded.
        """
        if self.multi_event:
            if self.input_timer_running:
                if not self.input_timer_elapsed:
                    return False
        for participant in self.participants:
            if participant.subscribed:
                participant.confirm_answered(duration=self.duration)
                if not participant.answered:
                    return False
        return True

    @property
    def has_image_saved(self) -> bool:
        """
        Indicates whether the event has an image saved.

        Returns
        -------
        True
            If an image is saved.
        False
            If an image is not saved.
        """
        return os.path.exists(self.image_path)

    @property
    def number_of_responded(self) -> int:
        """
        Gets the number of participants who are subscribed and have responded to the event.

        Returns
        -------
        responded: :class:`int`
            The number of participants who are subscribed and have responded to the event.
        """
        responded = 0
        for participant in self.participants:
            if participant.subscribed and participant.answered:
                responded += 1
        return responded

    @property
    def location_has_active_event(self) -> bool:
        """
        Indicates if the event's :class:`VoiceChannel` has a different active event in it.

        Returns
        -------
        True
            If the voice channel has an active event.
        False
            If the voice channel does not have an active event.
        """
        for event in client.events:
            if event is self or not event.started:
                continue
            if event.voice_channel == self.voice_channel:
                return True
        return False

    @property
    def timeblock(self) -> TimeBlock:
        return TimeBlock(start_time=self.start_times[0],
                         end_time=self.start_times[0] + self.duration)

    @property
    def timeblocks(self) -> list[TimeBlock]:
        return [TimeBlock(start_time=start_time,
                          end_time=start_time + self.duration)
                for start_time in self.start_times]

    @property
    def mins_until_start(self) -> int:
        time_until_start: timedelta = self.start_times[0] - now()
        return int(time_until_start.total_seconds() // 60)

    @property
    def duration_minutes(self) -> int:
        return int(self.duration.total_seconds() // 60)

    @property
    def duration_string(self) -> str:
        return get_time_str_from_minutes(self.duration_minutes)

    @property
    def timeout_minutes(self) -> int:
        return self.timeout_counter // UPDATES_PER_MINUTE

    @classmethod
    async def from_dict(cls, data):
        """
        Constructs an :class:`Event` from a data dict.

        Arguments
        ---------
        data: :class:`dict`
            The data to create the :class:`Event` from.

        Returns
        -------
        class: :class:`Event`
            The :class:`Event` object.
        """
        # Name
        event_name = data["name"]
        if event_name == '':
            raise Exception('Event has no name, discarding event')
        elif event_name in [event.name for event in client.events]:
            raise Exception(f'[{event_name}] Event name already in use, discarding repeat event')

        # Guild
        event_guild = client.get_guild(data["guild_id"])
        if not event_guild:
            raise Exception(f'[{event_name}] Could not find guild, discarding event')

        # Text channel
        event_text_channel = event_guild.get_channel(data["text_channel_id"])
        if not event_text_channel:
            raise Exception(f'[{event_name}] Could not find text channel, discarding event')

        # Voice channel
        event_voice_channel = utils.get(event_guild.voice_channels, id=data["voice_channel_id"])
        if not event_voice_channel:
            raise Exception(f'[{event_name}] Could not find voice channel, discarding event')

        # Participants
        event_participants = [Participant.from_dict(event_guild, participant) for participant in data["participants"]]
        for participant in event_participants.copy():
            try:
                if participant is None:
                    event_participants.remove(participant)
            except Exception as e:
                logger.warning(f"[{event_name}] Exception while adding participant: {e}")
                event_participants.remove(participant)
        if not event_participants:
            event_participants = get_participants_from_channel(event_name=event_name,
                                                               guild=event_guild,
                                                               channel=event_guild.get_channel(event_text_channel))
            logger.warning(f'[{event_name}] no participant(s) found, added everyone in the text channel')

        # Scheduler
        event_scheduler_id = data['scheduler_id']
        event_scheduler = None
        if event_scheduler_id != 0:
            for participant in event_participants:
                if participant.member.id == event_scheduler_id:
                    event_scheduler = participant
                    break

        # Rescheduler
        event_rescheduler_id = data['rescheduler_id']
        event_rescheduler = None
        if event_rescheduler_id != 0:
            for participant in event_participants:
                if participant.member.id == event_rescheduler_id:
                    event_rescheduler = participant
                    break

        # Availability message id
        event_availability_message_id = data["availability_message_id"]
        event_availability_message = None
        if event_availability_message_id != 0:
            try:
                event_availability_message = await event_text_channel.fetch_message(event_availability_message_id)
            except NotFound:
                event_availability_message_id = 0
                logger.warning(f"[{event_name}] availability message not found")
            except Exception as e:
                event_availability_message_id = 0
                logger.error(f"[{event_name}] error retrieving availability message: {e}")

        # Event buttons message id
        event_event_buttons_message_id = data["event_buttons_message_id"]
        event_event_buttons_message = None
        if event_event_buttons_message_id != 0:
            try:
                event_event_buttons_message = await event_text_channel.fetch_message(event_event_buttons_message_id)
            except NotFound:
                event_event_buttons_message_id = 0
                logger.warning(f"[{event_name}] event buttons message not found")
            except Exception as e:
                event_event_buttons_message_id = 0
                logger.error(f"[{event_name}] error retrieving event buttons message: {e}")

        # Image url
        event_image_url = data["image_url"]

        # Ready to create
        event_ready_to_create = data["ready_to_create"]

        # Created
        event_created = data["created"]

        # Started
        event_started = data["started"]

        # Scheduled event
        event_scheduled_events = []
        try:
            scheduled_event_ids = data["scheduled_event_ids"]
            for scheduled_event_id in scheduled_event_ids:
                for guild_scheduled_event in event_guild.scheduled_events:
                    if guild_scheduled_event.id == scheduled_event_id:
                        event_scheduled_events.append(guild_scheduled_event)
                        break
        except Exception as e:
            logger.warning(f'[{event_name}] error getting guild scheduled events: {e}')

        # Reminder flag
        event_reminder_flag = data["reminder_flag"]

        # Reminder message
        event_reminder_message_id = data["reminder_message_id"]
        event_reminder_message = None
        if event_reminder_message_id != 0:
            try:
                event_reminder_message = await event_text_channel.fetch_message(event_reminder_message_id)
            except NotFound:
                event_reminder_message_id = 0
                logger.warning(f"[{event_name}] reminder message not found")
            except Exception as e:
                event_reminder_message_id = 0
                logger.error(f"[{event_name}] error retrieving reminder message: {e}")

        # Start time
        try:
            event_start_times = [datetime.fromisoformat(start_time) for start_time in data["start_times"]]
        except Exception:
            event_start_times = []

        # Duration
        event_duration = timedelta(minutes=data["duration"])

        # Multi-event
        try:
            event_multi_event = data["multi_event"]
        except Exception as e:
            logger.warning(f'Failed to read multi_event data: {e}')
            event_multi_event = False

        # Timeout counter
        try:
            event_timeout_counter = data["timeout_counter"]
        except Exception as e:
            logger.warning(f'Failed to read timeout counter data: {e}')
            event_timeout_counter = DEFAULT_EVENT_TIMEOUT

        return cls(
            name=event_name,
            guild=event_guild,
            text_channel=event_text_channel,
            availability_message=event_availability_message,
            availability_buttons=None,
            voice_channel=event_voice_channel,
            scheduler=event_scheduler,
            rescheduler=event_rescheduler,
            participants=event_participants,
            image_url=event_image_url,
            event_buttons_message=event_event_buttons_message,
            event_buttons=None,
            ready_to_create=event_ready_to_create,
            created=event_created,
            started=event_started,
            scheduled_events=event_scheduled_events,
            reminder_flag=event_reminder_flag,
            reminder_message=event_reminder_message,
            start_times=event_start_times,
            duration=event_duration,
            multi_event=event_multi_event,
            timeout_counter=event_timeout_counter
        )

    def to_dict(self) -> dict:
        """
        Packs the event into a dict for saving.

        Returns
        -------
        data: :class:`dict`
            The event data dict.
        """
        try:
            availability_message_id = self.availability_message.id
        except Exception:
            availability_message_id = 0
        try:
            event_buttons_message_id = self.event_buttons_message.id
        except Exception:
            event_buttons_message_id = 0
        try:
            reminder_message_id = self.reminder_message.id
        except Exception:
            reminder_message_id = 0
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
            'ready_to_create': self.ready_to_create,
            'created': self.created,
            'started': self.started,
            'scheduled_event_ids': scheduled_event_ids,
            'reminder_flag': self.reminder_flag,
            'reminder_message_id': reminder_message_id,
            'start_times': start_times,
            'duration': self.duration_minutes,
            'multi_event': self.multi_event,
            'timeout_counter': self.timeout_counter
        }

    def __repr__(self) -> str:
        """
        Gets the name of the event for string formatting purposes.

        Returns
        -------
        name: :class:`str`
            The name of the event.
        """
        return f'{self.name}'


class ScheduleAgainModal(Modal):
    """
    Represents a modal for scheduling an event again.

    Attributes
    ----------
    event_name: :class:`str`
        The name for the reused event.
    event_duration: :class:`str`
        The duration for the reused event.
    image_url: :class:`str`
        The image url for the reused event.
    """

    def __init__(self, event: Event, after_buttons: View = None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.event = event
        self.after_buttons = after_buttons
        self.event_name = TextInput(label="Name",
                                    default=event.name,
                                    placeholder=event.get_limited_name(100))
        self.event_duration = TextInput(label="Duration",
                                        default=str(event.duration_minutes),
                                        placeholder=str(event.duration_minutes)[:100])
        if event.image_url:
            image_url = event.image_url
        else:
            image_url = ""
        self.event_image_url = TextInput(label="Image URL",
                                         default=image_url,
                                         placeholder=image_url[:100],
                                         required=False)
        self.event_start_time = TextInput(label="Start Time",
                                          placeholder="ISO 8601 format or a 24-hour time",
                                          required=False)
        self.add_item(self.event_name)
        self.add_item(self.event_duration)
        self.add_item(self.event_image_url)
        self.add_item(self.event_start_time)

    async def on_submit(self, interaction: Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        logger.info(f"[{self.event_name.value}] {interaction.user.name} scheduled again")
        event_name = self.event_name.value
        try:
            event_duration = int(self.event_duration.value)
        except Exception as e:
            logger.exception(f"[{event_name}] Error scheduling again: {e}")
            await interaction.followup.send(content=f"Error scheduling again: {e}",
                                            ephemeral=True)
            return
        event_image_url = self.event_image_url.value
        if self.event_start_time.value != "":
            try:
                event_start_time: datetime = parse_start_time(self.event_start_time.value)
                await create(event_name=event_name,
                             guild=self.event.guild,
                             text_channel=self.event.text_channel,
                             voice_channel=self.event.voice_channel,
                             start_time=event_start_time,
                             scheduler_id=interaction.user.id,
                             image_url=event_image_url,
                             usernames=self.event.participants,
                             duration=event_duration)
                followup = await interaction.followup.send(content="Event created!",
                                                           silent=True,
                                                           ephemeral=True)
                await followup.delete(delay=3)
            except Exception as e:
                content = f"Error: {e}\n"
                content += "You can get the correct format from https://time.lol."
                await interaction.followup.send(content=content, ephemeral=True)
        else:
            await schedule(event_name=event_name,
                           guild=self.event.guild,
                           text_channel=self.event.text_channel,
                           voice_channel=self.event.voice_channel,
                           scheduler_id=interaction.user.id,
                           image_url=event_image_url,
                           usernames=", ".join([str(p.member.id) for p in self.event.participants]),
                           duration=event_duration)
            followup = await interaction.followup.send(content="Event scheduling started!",
                                                       silent=True,
                                                       ephemeral=True)
            await followup.delete(delay=3)
        if self.after_buttons:
            await self.after_buttons.remove()

    async def on_error(self, interaction: Interaction, error: Exception) -> None:
        await interaction.followup.send(content=f"Error scheduling again: {error}",
                                        ephemeral=True)
        logger.exception(f"[{self.event_name.value}] Error scheduling again: {error}")


class CancelModal(Modal):
    """
    Represents a modal for cancelling an event.

    Attributes
    ----------
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
        await interaction.response.defer(ephemeral=True)
        logger.info(f"[{self.event}] {interaction.user} cancelled event with reason: {self.reason.value}")
        canceller = interaction.user.name
        if interaction.user.nick:
            canceller = interaction.user.nick
        await self.event.cancel(reason=self.reason.value, canceller=canceller)

    async def on_error(self, interaction: Interaction, error: Exception) -> None:
        await interaction.response.send_message(content=f"Error cancelling event: {error}",
                                                ephemeral=True)
        logger.exception(f"[{self.event}] Error cancelling event through modal: {error}")


class AvailabilityModal(Modal):
    """
    Represents a modal for inputting availability for an event.

    Attributes
    ----------
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

    def __init__(self, event, participant, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.event = event
        self.participant = participant
        date = now().strftime('%m/%d/%Y')
        self.timeslot1 = TextInput(label='Timeslot 1', placeholder='8-11, 1pm-3pm (i.e. Available 0800-1100, 1300-1500)', default='', required=False)
        self.timeslot2 = TextInput(label='Timeslot 2', placeholder='15:30-17 (i.e. Available 1530-1700)', default='', required=False)
        self.note = TextInput(label='Note', placeholder='A note to show with your availability', default=self.participant.note, required=False)
        self.date = TextInput(label='Date', placeholder='MM/DD/YYYY', default=date)
        self.timezone = TextInput(label='Timezone', placeholder='AT|AST|ADT|ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|PT|PST|PDT', default='ET')
        self.add_item(self.timeslot1)
        self.add_item(self.timeslot2)
        self.add_item(self.note)
        self.add_item(self.date)
        self.add_item(self.timezone)

    async def on_submit(self, interaction: Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        embed = None
        # Participant availability
        avail_string = f'{self.timeslot1.value}, {self.timeslot2.value} {self.timezone.value}'
        try:
            logger.info(f'[{self.event}] Received availability from {interaction.user.name}')
            self.participant.note = self.note.value
            self.participant.set_specific_availability(avail_string, self.date.value)
            self.participant.confirm_answered(duration=self.event.duration)
            self.event.start_input_timer()
            embed = get_participants_other_unanswered_events_embed(self.event, self.participant)
            remove_times_from_availabilities_for_events()
            await self.event.update_availability_message()
        except Exception as e:
            embed = Embed(title="Error",
                          color=Color.red(),
                          description=e.__str__())
            logger.info(f"[{self.event}] Failure setting specific availability: {e}")
        if embed is not None:
            await interaction.followup.send(embed=embed,
                                            ephemeral=True)

    async def on_error(self, interaction: Interaction, error: Exception) -> None:
        await interaction.response.send_message(f"Error getting availability: {error}", ephemeral=True)
        logger.exception(f"[{self.event}] Error getting availability from {interaction.user.name} (AvailabilityModal): {error}")


class AvailabilityButtons(View):
    """
    Represents the availability buttons tied to an availability message.

    Attributes
    ----------
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
        """
        Sets up and gets the Respond button.

        Returns
        -------
        button: :class:`Button`
            The Respond button.
        """
        button = Button(label=self.respond_label, style=ButtonStyle.green)

        async def respond_button_callback(interaction: Interaction):
            if self.event not in client.events:
                client.events.append(self.event)
            am_title = f'Availability for {self.event}'
            if len(am_title) >= 45:
                am_title = f"{am_title[:41]}..."
            participant = self.event.get_participant(interaction.user.name)
            if participant is None:
                member = self.event.guild.get_member(interaction.user.id)
                participant = Participant(member=member)
                self.event.participants.append(participant)
            await interaction.response.send_modal(AvailabilityModal(event=self.event,
                                                                    participant=participant,
                                                                    title=am_title))
        button.callback = respond_button_callback
        self.add_item(button)
        return button

    def add_full_button(self) -> Button:
        """
        Sets up and gets the Full Availability button.

        Returns
        -------
        button: :class:`Button`
            The Full Availability button.
        """
        button = Button(label=self.full_label, style=ButtonStyle.green)

        async def full_button_callback(interaction: Interaction):
            if self.event not in client.events:
                client.events.append(self.event)
            await interaction.response.defer(ephemeral=True)
            participant = self.event.get_participant(interaction.user.name)
            if participant is None:
                await interaction.followup.send(content="Could not add you as a participant!",
                                                ephemeral=True)
                return
            participant.subscribed = True
            # Participant has full availability
            if not participant.full_availability_flag:
                logger.info(f'[{self.event}] {participant} selected full availability')
                self.event.start_input_timer()
                participant.set_full_availability()
                self.event.update_availabilities_to(participant)
                remove_times_from_availabilities_for_events()
                await self.event.update_availability_message()
            # Participant no longer has full availability
            else:
                logger.info(f'[{self.event}] {participant} deselected full availability')
                participant.set_no_availability()
                await self.event.update_availability_message()
            embed = get_participants_other_unanswered_events_embed(self.event, participant)
            if embed:
                await interaction.followup.send(embed=embed,
                                                ephemeral=True)
        button.callback = full_button_callback
        self.add_item(button)
        return button

    def add_reuse_button(self) -> Button:
        """
        Sets up and gets the Reuse Availability button.

        Returns
        -------
        button: :class:`Button`
            The Reuse Availability button.
        """
        button = Button(label=self.reuse_label, style=ButtonStyle.blurple)

        async def reuse_button_callback(interaction: Interaction):
            if self.event not in client.events:
                client.events.append(self.event)
            await interaction.response.defer(ephemeral=True)
            participant = self.event.get_participant(interaction.user.name)
            if participant is None:
                await interaction.followup.send(content="Could not add you as a participant.",
                                                ephemeral=True)
                return
            found_availabilities = self.event.get_other_availabilities(participant)
            if not found_availabilities:
                await interaction.followup.send(content="No existing availabilities found.",
                                                ephemeral=True)
                return
            if len(found_availabilities) == 1:
                participant.set_no_availability()
                participant.availability = found_availabilities[0].avail.copy()
                participant.full_availability_flag = found_availabilities[0].full_flag
                participant.answered = True
                participant.subscribed = True
                await self.event.update_availability_message()
            else:
                await interaction.followup.send(content="Select another event from which to grab your availability.",
                                                view=ExistingAvailabilitiesSelectView(found_availabilities, participant),
                                                ephemeral=True)
            embed = get_participants_other_unanswered_events_embed(self.event, participant)
            if embed:
                await interaction.followup.send(embed=embed,
                                                ephemeral=True)
        button.callback = reuse_button_callback
        self.add_item(button)
        return button

    def add_unsub_button(self) -> Button:
        """
        Sets up and gets the Unsubscribe button.

        Returns
        -------
        button: :class:`Button`
            The Unsubscribe button.
        """
        button = Button(label=self.unsub_label, style=ButtonStyle.red)

        async def unsub_button_callback(interaction: Interaction):
            if self.event not in client.events:
                client.events.append(self.event)
            await interaction.response.defer(ephemeral=True)
            if interaction.user.id not in [participant.member.id for participant in self.event.participants]:
                await interaction.followup.send(content="You are already not part of this event!",
                                                ephemeral=True)
                return
            participant = self.event.get_participant(interaction.user.name)
            if participant.subscribed:
                logger.info(f'[{self.event}] {interaction.user.name} unsubscribed')
                participant.subscribed = False
                participant.answered = True
                followup = await interaction.followup.send(content=f"You have been unsubscribed from {self.event}.",
                                                           silent=True,
                                                           ephemeral=True)
                await followup.delete(delay=3)
            else:
                logger.info(f'[{self.event}] {interaction.user.name} resubscribed')
                participant.subscribed = True
                participant.confirm_answered(duration=self.event.duration)
                followup = await interaction.followup.send(content=f"You have been resubscribed to {self.event}.",
                                                           silent=True,
                                                           ephemeral=True)
                await followup.delete(delay=3)
            await self.event.update_availability_message()
        button.callback = unsub_button_callback
        self.add_item(button)
        return button

    def add_cancel_button(self) -> Button:
        """
        Sets up and gets the Cancel button.

        Returns
        -------
        button: :class:`Button`
            The Cancel button.
        """
        button = Button(label=self.cancel_label, style=ButtonStyle.red)

        async def cancel_button_callback(interaction: Interaction):
            if self.event not in client.events:
                client.events.append(self.event)
            title = f"Cancel {self.event.get_limited_name(38)}"
            await interaction.response.send_modal(CancelModal(event=self.event,
                                                              title=title))
        button.callback = cancel_button_callback
        self.add_item(button)
        return button


class EventButtons(View):
    """
    Represents the event buttons attached to an event control message.

    Attributes
    ----------
    event: :class:`Event`
        The event that the buttons are for.
    start_label: :class:`str`
        The label for the Start version of the Start/End button.
    end_label: :class:`str`
        The label for the End version of the Start/End button.
    unsubscribe_label: :class:`str`
        The label for the Unsubscribe button.
    reschedule_label: :class:`str`
        The label for the Reschedule button.
    cancel_label: :class:`str`
        The label for the Cancel button.
    start_end_button: :class:`Button`
        The Start/End button.
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
        self.end_and_forget_label = "End and Forget"
        self.unsubscribe_label = "Unsubscribe"
        self.reschedule_label = "Reschedule Event"
        self.cancel_label = "Cancel Event"
        self.start_callback = None
        self.end_callback = None
        self.start_end_button = Button(label=self.start_label, style=ButtonStyle.blurple)
        self.end_and_forget_button = Button(label=self.end_and_forget_label, style=ButtonStyle.blurple)
        self.unsubscribe_button = Button(label=self.unsubscribe_label, style=ButtonStyle.red)
        self.reschedule_button = Button(label=self.reschedule_label, style=ButtonStyle.red)
        self.cancel_button = Button(label=self.cancel_label, style=ButtonStyle.red)
        self.add_start_end_button()
        self.add_end_and_forget_button()
        self.add_unsubscribe_button()
        self.add_reschedule_button()
        self.add_cancel_button()

    def add_start_end_button(self) -> None:
        """Sets up the Start/End button."""
        async def end_button_callback(interaction: Interaction):
            if self.event not in client.events:
                client.events.append(self.event)
            await interaction.response.defer(ephemeral=True)
            if interaction.user.id not in [participant.member.id for participant in self.event.participants]:
                await interaction.followup.send(content="You are not a participant of this event.",
                                                ephemeral=True)
                return
            self.remove_item(self.start_end_button)
            self.remove_item(self.end_and_forget_button)
            self.remove_item(self.unsubscribe_button)
            await self.event.end(f"Event ended by {interaction.user} pressing end button.")
        self.end_callback = end_button_callback

        async def start_button_callback(interaction: Interaction):
            if self.event not in client.events:
                client.events.append(self.event)
            await interaction.response.defer(ephemeral=True)
            self.event.add_user_as_participant(interaction.user)
            if interaction.user.id not in [member.id for member in self.event.voice_channel.members]:
                logger.info(f"[{self.event}] {interaction.user} tried to press start button while not in the event's voice channel")
                content = f"You must be in {self.event.voice_channel.mention} to start {self.event}!"
                await interaction.followup.send(content=content, ephemeral=True)
                return
            logger.info(f"[{self.event}] {interaction.user} started by button press")
            await self.event.start(reason=f"Event started by {interaction.user} pressing start button.")
        self.start_callback = start_button_callback

        if not self.event.started:
            self.start_end_button.callback = start_button_callback
            self.start_end_button.disabled = self.event.location_has_active_event
        else:
            self.start_end_button.label = self.end_label
            self.start_end_button.callback = end_button_callback
        self.add_item(self.start_end_button)

    def add_end_and_forget_button(self) -> None:
        """Sets up the End and Forget button."""
        async def end_and_forget_button_callback(interaction: Interaction):
            if self.event not in client.events:
                client.events.append(self.event)
            await interaction.response.defer(ephemeral=True)
            if interaction.user.id not in [participant.member.id for participant in self.event.participants]:
                await interaction.followup.send(content="You are not a participant of this event.",
                                                ephemeral=True)
                return
            self.remove_item(self.start_end_button)
            self.remove_item(self.end_and_forget_button)
            self.remove_item(self.unsubscribe_button)
            await self.event.end(f"Event ended by {interaction.user} pressing end button.", forget=True)
            after_buttons = self.event.get_after_buttons()
            if after_buttons:
                await after_buttons.remove()
        self.end_and_forget_button.callback = end_and_forget_button_callback
        if self.event.started:
            self.add_item(self.end_and_forget_button)

    def add_unsubscribe_button(self) -> None:
        """
        Sets up the Unsubscribe button.

        Returns
        -------
        button: :class:`Button`
            The Unsubscribe button.
        """
        async def unsubscribe_button_callback(interaction: Interaction):
            if self.event not in client.events:
                client.events.append(self.event)
            await interaction.response.defer(ephemeral=True)
            if interaction.user.id not in [participant.member.id for participant in self.event.participants]:
                await interaction.followup.send(content="You are already not part of this event.",
                                                ephemeral=True)
                return
            participant = self.event.get_participant(interaction.user.name)
            if participant.subscribed:
                logger.info(f'[{self.event}] {interaction.user.name} unsubscribed')
                participant.subscribed = False
                followup = await interaction.followup.send(content=f"You have been unsubscribed from {self.event}.",
                                                           silent=True,
                                                           ephemeral=True)
                await followup.delete(delay=3)
                await self.event.create_if_possible()
            else:
                logger.info(f'[{self.event}] {interaction.user.name} resubscribed')
                participant.subscribed = True
                followup = await interaction.followup.send(content=f"You have been resubscribed to {self.event}.",
                                                           silent=True,
                                                           ephemeral=True)
                await followup.delete(delay=3)

        self.unsubscribe_button.callback = unsubscribe_button_callback
        self.add_item(self.unsubscribe_button)

    def add_reschedule_button(self) -> None:
        """
        Sets up the Reschedule button.

        Returns
        -------
        button: :class:`Button`
            The Reschedule button.
        """
        if self.event.started:
            return

        async def reschedule_button_callback(interaction: Interaction):
            if self.event not in client.events:
                client.events.append(self.event)
            await interaction.response.defer(ephemeral=True)
            logger.info(f'[{self.event}] {interaction.user} rescheduled by button press')
            for participant in self.event.participants:
                participant.confirm_answered(duration=self.event.duration)
            participant = self.event.get_participant(interaction.user.id)
            participant.set_no_availability()
            participant.subscribed = True
            await self.event.reschedule(rescheduler=participant)

        self.reschedule_button.callback = reschedule_button_callback
        self.add_item(self.reschedule_button)

    def add_cancel_button(self) -> None:
        """
        Sets up the Cancel button.

        Returns
        -------
        button: :class:`Button`
            The Cancel button.
        """
        if self.event.started:
            return

        async def cancel_button_callback(interaction: Interaction):
            if self.event not in client.events:
                client.events.append(self.event)
            if interaction.user.id not in [participant.member.id for participant in self.event.participants]:
                await interaction.response.send_message(content="You are not a participant of this event!",
                                                        ephemeral=True)
                return
            title = f"Cancel {self.event}"
            if len(title) >= 38:
                title = f"{title[:34]}..."
            await interaction.response.send_modal(CancelModal(event=self.event,
                                                              title=title))

        self.cancel_button.disabled = self.event.started
        self.cancel_button.callback = cancel_button_callback
        self.add_item(self.cancel_button)

    def convert(self) -> None:
        self.start_end_button.label = self.end_label
        self.start_end_button.callback = self.end_callback
        self.remove_item(self.unsubscribe_button)
        self.remove_item(self.reschedule_button)
        self.remove_item(self.cancel_button)
        self.add_item(self.end_and_forget_button)
        self.add_item(self.unsubscribe_button)


class AfterButtons(View):
    """
    Represents the buttons attached to the cancellation message.

    Attributes
    ----------
    event: :class:`Event`
        The event that the buttons will reference.
    schedule_again_button: :class:`Button`
        A button that allows for scheduling of an event mimicking the provided event.
    """

    def __init__(self, event):
        super().__init__(timeout=None)
        self.event = event
        self.schedule_again_label = "Schedule Again"
        self.forget_label = "Forget"
        self.schedule_again_button = self.add_schedule_again_button()
        self.forget_button = self.add_forget_button()
        self.schedule_again_timeout = SCHEDULE_AGAIN_TIMEOUT
        self.message = None
        client.schedule_again_events.append(self)

    async def update(self):
        self.schedule_again_timeout -= 1
        if self.schedule_again_timeout == 0:
            logger.info(f"[{self.event}] schedule again timed out, forgetting")
            await self.remove()

    def add_schedule_again_button(self):
        """
        Sets up and gets the Schedule Again button.

        Returns
        -------
        button: :class:`Button`
            The Schedule Again button.
        """
        button = Button(label=self.schedule_again_label, style=ButtonStyle.blurple)

        async def schedule_again_button_callback(interaction: Interaction):
            logger.info(f"[{self.event}] scheduled again by {interaction.user.name}")
            await interaction.response.send_modal(ScheduleAgainModal(event=self.event,
                                                                     after_buttons=self,
                                                                     title="Schedule Event Again"))
        button.callback = schedule_again_button_callback
        self.add_item(button)
        return button

    def add_forget_button(self):
        """
        Sets up and gets the Forget button.

        Returns
        -------
        button: :class:`Button`
            The Forget button.
        """
        button = Button(label=self.forget_label, style=ButtonStyle.red)

        async def forget_button_callback(interaction: Interaction):
            logger.info(f"[{self.event}] forget button pressed by {interaction.user.name}")
            await interaction.response.send_message(content=f"{self.event} forgotten!",
                                                    silent=True,
                                                    ephemeral=True,
                                                    delete_after=3)
            await self.remove()
        button.callback = forget_button_callback
        self.add_item(button)
        return button

    async def remove(self):
        logger.info(f"[{self.event}] forgotten")
        client.schedule_again_events.remove(self)
        self.schedule_again_button.disabled = True
        self.forget_button.disabled = True
        self.clear_items()
        self.stop()
        if self.message:
            try:
                await self.message.unpin()
            except Exception as e:
                logger.error(f"[{self}] Error in AfterButtons remove while unpinning message: {e}")
            try:
                await self.message.edit(view=None)
            except Exception as e:
                logger.error(f"[{self}] Error in AfterButtons remove while editing message: {e}")


class ExistingGuildEventsSelect(Select):
    """
    Represents a dropdown of existing guild scheduled events for a user to attach to.

    Attributes
    -----------
    guild: :class:`Guild`
        The guild to get the events from.
    """

    def __init__(self, guild: Guild):
        self.guild = guild
        options = [
            SelectOption(label=guild_event.get_limited_name(25), description=guild_event.description[:50], value=str(guild_event.id))
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
                              voice_channel=selected_guild_event.channel,
                              guild=self.guild,
                              text_channel=interaction.channel,
                              image_url=image_url,
                              scheduler=scheduler,
                              participants=participants,
                              start_times=start_times,
                              created=True)
                client.events.append(event)
            remove_times_from_availabilities_for_events()
            for guild_event in self.guild.scheduled_events:
                if guild_event.name == selected_guild_event.name and guild_event.location == selected_guild_event.location:
                    event.start_times.append(guild_event.start_time.astimezone())
                    if event.has_image_saved:
                        await guild_event.edit(image=event.get_image())
                    event.scheduled_events.append(guild_event)
            await event.update_event_buttons_message()
            followup = await interaction.followup.send(content="Success!",
                                                       silent=True,
                                                       ephemeral=True)
            await followup.delete(delay=3)
        else:
            await interaction.response.send_message(content="Error getting guild scheduled event.")
            logger.exception(f"Error getting guild scheduled event selected by {interaction.user.name}")


class ExistingGuildEventsSelectView(View):
    """
    Represents a view to house the guild scheduled events dropdown.
    """

    def __init__(self, guild: Guild):
        super().__init__()
        self.add_item(ExistingGuildEventsSelect(guild))


class EventAvailability:
    """
    Represents the pairing of an event with a user's availability.
    """

    def __init__(self, event: Event, avail: list, full_flag: bool):
        self.event = event
        self.avail = avail
        self.full_flag = full_flag


class ExistingAvailabilitiesSelect(Select):
    """
    Represents a dropdown to allow a user to selection an existing availability from another event.
    """

    def __init__(self, event_avails: list, participant: Participant):
        self.event_avails = event_avails
        self.participant = participant
        options = []
        for event_avail in self.event_avails:
            name = event_avail.event.get_limited_name(99)
            options.append(SelectOption(label=name, value=name))
        super().__init__(placeholder="Event Availabilities", options=options)

    # Select an availability to attach
    async def callback(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        for event_avail in self.event_avails:
            name = event_avail.event.get_limited_name(99)
            if name == self.values[0]:
                logger.info(f'{interaction.user.name} reused availability from {self.values[0]}')
                self.participant.availability = event_avail.avail.copy()
                self.participant.full_availability_flag = event_avail.full_flag
                self.participant.answered = True
                self.participant.subscribed = True
                followup = await interaction.followup.send(content="**Availability retrieved!**",
                                                           silent=True,
                                                           ephemeral=True)
                await followup.delete(delay=3)
                await event_avail.event.update_availability_message()
                await event_avail.event.create_if_possible()
                return
        await interaction.followup.send(content="**Failed to get your availability.**",
                                        ephemeral=True)


class ExistingAvailabilitiesSelectView(View):
    """
    Represents a view to house the existing availability dropdown.
    """

    def __init__(self, event_avails: list, participant: Participant):
        super().__init__()
        self.add_item(ExistingAvailabilitiesSelect(event_avails, participant))


def get_participants_from_interaction(event_name: str,
                                      interaction: Interaction,
                                      include_exclude: Optional[INCLUDE_EXCLUDE] = None,
                                      usernames: Optional[str] = None,
                                      roles: Optional[str] = None) -> list[Participant]:
    """
    Wrapper function for getting participants from a channel of an interaction.
    """
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
                                  user: Optional[User] = None,
                                  include_exclude: Optional[INCLUDE_EXCLUDE] = INCLUDE,
                                  usernames: Optional[str] = None,
                                  roles: Optional[str] = None):
    """
    Gets participants for an event from a channel using the included guidelines.

    Arguments
    ---------
    event_name: :class:`str`
        The name of the event for logging purposes.
    guild: :class:`Guild`
        The guild that the event is ocurring in.
    channel: :class:`TextChannel`
        The text channel that the event is occurring in.
    user: :class:`Optional[User]` or :class:`Optional[Member]`
        The user that is scheduling the event.
    include_exclude: :class:`Optional[INCLUDE_EXCLUDE]`
        Whether to include or exclude the provided usernames/ids/roles.
        Default: INCLUDE
        REQUIRES usernames or roles.
    usernames: :class:`Optional[str]`
        The comma separated usernames or ids to include/exclude.
    roles: :class:`Optional[str]`
        A comma separated list of roles to include/exclude.

    Returns
    -------
    participants: :class:`list[Participant]`
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
    for member in channel.members:
        if member.bot:
            continue
        if user is not None:
            if member.id == user.id:
                continue
        participants.append(Participant(member=member))
    return participants


async def edit_event(event: Event,
                     name: Optional[str] = None,
                     voice_channel: Optional[VoiceChannel] = None,
                     image_url: Optional[str] = None,
                     duration: Optional[int] = None,
                     multi_event: Optional[bool] = None,
                     timeout_days: Optional[int] = None) -> None:
    embed = Embed(title=f"{event} Edited",
                  description=f"{event} has been edited.",
                  color=Color.orange())
    # Name
    if name is not None:
        old_name = event.name
        event.name = name
        if old_name == event.name:
            embed.add_field(name="Name (Unchanged)",
                            value="The new name is the same as the old name",
                            inline=False)
        else:
            for other_event in client.events:
                if other_event is not event:
                    other_event.restore_availabilities(event)
            remove_times_from_availabilities_for_events()
            if event.created:
                for scheduled_event in event.scheduled_events:
                    await scheduled_event.edit(name=event.name)
            embed.add_field(name="Name",
                            value=f"{old_name[:20]} -> {event.get_limited_name(20)}",
                            inline=False)
    # Voice Channel
    if voice_channel is not None:
        old_vc = event.voice_channel
        event.voice_channel = voice_channel
        if old_vc == event.voice_channel:
            embed.add_field(name="Voice Channel (Unchanged)",
                            value=f"Voice channel is already {event.voice_channel.mention}",
                            inline=False)
        else:
            if event.created:
                for scheduled_event in event.scheduled_events:
                    await scheduled_event.edit(channel=event.voice_channel)
            embed.add_field(name="Voice Channel",
                            value=f"{old_vc.mention} -> {event.voice_channel.mention}",
                            inline=False)
    # Image URL
    if image_url is not None:
        old_image_url = event.image_url
        event.delete_image_file()
        event.image_url = image_url
        if event.image_url:
            if old_image_url == event.image_url:
                embed.add_field(name="Image (Unchanged)",
                                value=f"image is already {event.image_url}",
                                inline=False)
            else:
                if event.created:
                    await event.save_image_to_file()
                    if event.has_image_saved:
                        for scheduled_event in event.scheduled_events:
                            await scheduled_event.edit(image=event.get_image())
                embed.add_field(name="Image",
                                value=f"{old_image_url} -> {event.image_url}",
                                inline=False)
        else:
            event.image_url = old_image_url
            embed.add_field(name="Image (Unchanged)",
                            value="The new image could not be downloaded",
                            inline=False)
    # Duration
    if duration is not None:
        old_duration = event.duration
        event.duration = timedelta(minutes=duration)
        if old_duration.total_seconds() == event.duration.total_seconds():
            embed.add_field(name="Duration (Unchanged)",
                            value="The new duration is the same as the old duration",
                            inline=False)
        else:
            remove_times_from_availabilities_for_events()
            embed.add_field(name="Duration",
                            value=f"{get_time_str_from_minutes(old_duration.total_seconds() // 60)}"
                            f" -> {get_time_str_from_minutes(event.duration.total_seconds() // 60)}",
                            inline=False)
    # Multi event
    if multi_event is not None:
        if event.started:
            embed.add_field(name="Multi Event (Unchanged)",
                            value="Multi Event cannot be changed after starting the event",
                            inline=False)
        else:
            old_multi_event = event.multi_event
            event.multi_event = multi_event
            if old_multi_event == event.multi_event:
                embed.add_field(name="Multi Event (Unchanged)",
                                value=f"Multi Event already {event.multi_event}",
                                inline=False)
            else:
                embed.add_field(name="Multi Event",
                                value=f"{old_multi_event} -> {event.multi_event}",
                                inline=False)
                if event.multi_event and event.created:
                    await event.reschedule()
                    await event.create_if_possible()

    # Timeout
    if timeout_days is not None:
        timeout = timeout_days * EVENT_TIMEOUT_CONSTANT_DAYS
        old_timeout = event.timeout_counter
        if old_timeout == timeout:
            embed.add_field(name="Timeout (Unchanged)",
                            value="The new timeout is the same as the old timeout",
                            inline=False)
        else:
            event.timeout_counter = timeout
            embed.add_field(name="Timeout",
                            value=f"{get_time_str_from_minutes(old_timeout // UPDATES_PER_MINUTE)}"
                            f" -> {get_time_str_from_minutes(timeout // UPDATES_PER_MINUTE)}",
                            inline=False)

    if event.image_url:
        embed.set_thumbnail(url=event.image_url)
    return embed


@client.event
async def on_ready():
    await client.change_presence(activity=Activity(type=ActivityType.watching, state="Loading", name="a loading screen"), status=Status.offline)
    logger.info('Connected to Discord')
    await client.retrieve_events()
    if not client.server_is_running:
        await client.start_server()
    if not update.is_running():
        update.start()
    logger.info('Ready!')
    await client.change_presence(activity=Activity(type=ActivityType.watching, state="Online", name="for event scheduling commands"), status=Status.online)


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
    if message.author.id == OWNER_ID and 'scheduler: events' in message.content:
        logger.info(f"User {message.author.name} listed all events")
        embed = Embed(title="All events", color=Color.blue())
        for event in client.events:
            eventStatus = event.scheduling_status
            embed.add_field(name=event.get_limited_name(25), value=eventStatus, inline=True)
        await message.channel.send(embed=embed, reference=message)

    # Owner requests a recount
    if message.author.id == OWNER_ID and 'scheduler: check' in message.content:
        content = ""
        if len(client.events) > 0:
            for event in client.events:
                await event.create_if_possible()
                content += f"Checked {event}\n"
        else:
            content = "No events found."
        await message.channel.send(content=content, reference=message)

    # Owner toggles debug
    if message.author.id == OWNER_ID and 'scheduler: debug' in message.content:
        if logger.isEnabledFor(logging.DEBUG):
            logger.setLevel(logging.INFO)
            await message.channel.send(content="Debugging disabled.", reference=message)
        else:
            logger.setLevel(logging.DEBUG)
            await message.channel.send(content="Debugging enabled.", reference=message)

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
@app_commands.describe(duration=f'Event duration in minutes ({DEFAULT_EVENT_DURATION} minutes default).')
async def create_command(interaction: Interaction,
                         event_name: str,
                         voice_channel: VoiceChannel,
                         start_time: str,
                         image_url: Optional[str] = None,
                         include_exclude: Optional[INCLUDE_EXCLUDE] = INCLUDE,
                         usernames: Optional[str] = None,
                         roles: Optional[str] = None,
                         duration: Optional[int] = DEFAULT_EVENT_DURATION):
    await interaction.response.defer(ephemeral=True)
    logger.info(f"[{event_name}] Received event creation request from {interaction.user.name}")
    if not interaction.guild.voice_channels:
        raise Exception("The server must have at least one voice channel to schedule an event.")

    participants = get_participants_from_interaction(event_name=event_name,
                                                     interaction=interaction,
                                                     include_exclude=include_exclude,
                                                     usernames=usernames,
                                                     roles=roles)
    try:
        await create(event_name=event_name,
                     guild=interaction.guild,
                     text_channel=interaction.channel,
                     voice_channel=voice_channel,
                     start_time=start_time,
                     scheduler_id=interaction.user.id,
                     image_url=image_url,
                     include_exclude=include_exclude,
                     usernames=participants,
                     roles=roles,
                     duration=duration)
    except Exception as e:
        await interaction.followup.send(content=f"Error creating event: {e}",
                                        ephemeral=True)
        return
    followup = await interaction.followup.send(content=f"Created event {event_name}.",
                                               silent=True,
                                               ephemeral=True)
    await followup.delete(delay=3)


async def create(event_name: str,
                 guild: Guild,
                 text_channel: TextChannel,
                 voice_channel: VoiceChannel,
                 start_time: datetime or str,
                 scheduler_id: Optional[int] = 0,
                 image_url: Optional[str] = None,
                 include_exclude: Optional[INCLUDE_EXCLUDE] = INCLUDE,
                 usernames: Optional[str] or Optional[list[str]] or Optional[list[int]] or Optional[list[Participant]] = None,
                 roles: Optional[str] = None,
                 duration: Optional[int] = DEFAULT_EVENT_DURATION,
                 multi_event: Optional[bool] = False):
    """
    Creates an event with a specified start time.

    Arguments
    ---------
    event_name: :class:`str`
        The name of the event.
    guild: :class:`Guild`
        The guild that the event is occurring in.
    text_channel: :class:`TextChannel`
        The text channel that the event sends messages in.
    voice_channel: :class:`VoiceChannel`
        The voice channel that the event occurs in.
    start_time: :class:`datetime` or :class:`str`
        The start time for the event.
    scheduler_id: :class:`Optional[int]`
        The ID of the member who scheduled the event.
    image_url: :class:`Optional[str]`
        The URL for the image.
    include_exclude: :class:`Optional[INCLUDE_EXCLUDE]`
        Whether to include or exclude the usernames/ids/roles.
        Default: INCLUDE
        REQUIRES usernames or roles.
    usernames: :class:`Optional[str]`
        Comma separated list of usernames or ids to include/exclude.
    roles: :class:`Optional[str]`
        Comma separated list of roles to include/exclude.
    duration: :class:`Optional[int]`
        The duration of the event in minutes.
        Default: 30
    multi_event: :class:`Optional[bool]`
        Whether or not the event is a multi event.
        Default: False

    Returns
    -------
    event: :class:`Event`
        The event object created for scheduling.

    Exceptions
    -----------
    Exception: :class:`Exception`
        A string message describing the error.
     """
    # Voice channel
    if not guild.voice_channels:
        logger.info(f"[{event_name}] Scheduling cancelled due to no voice channel in guild")
        raise Exception("The server must have at least one voice channel to schedule an event.")

    # Event name
    if event_name in [event.name for event in client.events]:
        raise Exception(f"Sorry, I already have an event called \"{event_name}\". Please choose a different name.")

    # Start time
    if isinstance(start_time, datetime):
        start_time_obj = start_time
        if start_time_obj <= now():
            raise Exception("Start time must be in the future!")
    elif isinstance(start_time, str):
        try:
            start_time_obj = parse_start_time(start_time)
        except Exception as e:
            raise Exception(f"Error while creating event: {e}")
    else:
        raise Exception(f"This is a code error, please inform the developer!\nInvalid start_time type: {type(start_time)}")

    # Scheduler
    scheduler_user = None
    if scheduler_id != 0:
        scheduler_user = guild.get_member(scheduler_id)
    if scheduler_user is None:
        scheduler_user = guild.members[0]

    # Participants
    if isinstance(usernames, str):
        try:
            participants = get_participants_from_channel(event_name=event_name,
                                                         guild=guild,
                                                         channel=text_channel,
                                                         user=scheduler_user,
                                                         include_exclude=include_exclude,
                                                         usernames=usernames,
                                                         roles=roles)
        except Exception as e:
            logger.error(f"[{event_name}] Error getting participants: {e}")
            raise Exception(f"Failed to generate participants list: {e}")
    elif isinstance(usernames, list) and (
            all(isinstance(un, str) for un in usernames) or (
            all(isinstance(un, int) for un in usernames))):
        try:
            participants = get_participants_from_channel(event_name=event_name,
                                                         guild=guild,
                                                         channel=text_channel,
                                                         user=scheduler_user,
                                                         include_exclude=include_exclude,
                                                         usernames=", ".join(usernames),
                                                         roles=roles)
        except Exception as e:
            logger.error(f"[{event_name}] Error getting participants: {e}")
            raise Exception(f"Failed to generate participants list: {e}")
    elif isinstance(usernames, list) and all(isinstance(un, Participant) for un in usernames):
        participants = usernames
    elif usernames is None or not usernames:
        participants = get_participants_from_channel(event_name=event_name,
                                                     guild=guild,
                                                     channel=text_channel,
                                                     user=scheduler_user)
    else:
        logger.error(f"Invalid usernames type in schedule: {type(usernames)}")
        raise Exception(f"This is a code error, please inform the developer!\nInvalid usernames type in create: {type(usernames)}")

    # Scheduler
    scheduler = None
    for participant in participants:
        participant.answered = True
        if participant.member.id == scheduler_id:
            scheduler = participant
    if scheduler is None:
        scheduler = participants[0]

    # Image URL
    if image_url == "":
        image_url = None

    # Check event won't overlap with another event in the same voice channel
    # or another event with a shared participant
    for other_event in client.events:
        if not other_event.created:
            continue
        for other_participant in other_event.participants:
            for participant in participants:
                if other_participant.member.id == participant.member.id:
                    participant.remove_availability_for_event(event_name=other_event.name,
                                                              event_timeblocks=other_event.timeblocks)
                    break
            else:
                continue
            break
    duration = timedelta(minutes=duration)
    start_times = [start_time_obj]
    for start_time in start_times:
        timeblock = TimeBlock(start_time=start_time, end_time=start_time + duration)
        for other_event in client.events:
            if not other_event.created:
                continue
            for other_start_time in other_event.start_times:
                other_timeblock = TimeBlock(start_time=other_start_time,
                                            end_time=other_start_time + other_event.duration)
                if other_event.voice_channel == voice_channel:
                    if timeblock.overlaps_with(other_timeblock):
                        content = f"**Specified time overlaps with** ***{other_event}*** **in the same location!**"
                        raise Exception(content)
                for other_participant in other_event.participants:
                    if other_participant.member.id in [participant.member.id for participant in participants]:
                        for other_participant_removed_time in other_participant.removed_times:
                            if timeblock.overlaps_with(other_participant_removed_time):
                                content = f"**Specified time overlaps with an event that** ***{other_participant}*** **is in!**"
                                raise Exception(content)

    # Make event
    event = Event(name=event_name,
                  voice_channel=voice_channel,
                  scheduler=scheduler,
                  participants=participants,
                  guild=guild,
                  text_channel=text_channel,
                  image_url=image_url,
                  duration=duration,
                  start_times=start_times)
    await event.make_scheduled_events()
    client.events.append(event)

    remove_times_from_availabilities_for_events()
    await event.update_event_buttons_message()

    other_events = get_events_that_share_participants(event)
    for other_event in other_events:
        await other_event.update_messages()
    return event


@client.tree.command(name='schedule', description='Schedule an event.')
@app_commands.describe(event_name='Name for the event.')
@app_commands.describe(voice_channel='Voice channel for the event.')
@app_commands.describe(image_url="URL to an image for the event.")
@app_commands.describe(include_exclude='Whether to include or exclude users specified.')
@app_commands.describe(usernames='Comma separated usernames of users to include/exclude.')
@app_commands.describe(roles='Comma separated roles of users to include/exclude.')
@app_commands.describe(duration=f'Event duration in minutes ({DEFAULT_EVENT_DURATION} minutes default).')
@app_commands.describe(multi_event='Create an event on each date that everyone is available.')
@app_commands.describe(timeout=f'Number of days that the event should wait for responses ({DEFAULT_EVENT_TIMEOUT_DAYS} days default).')
async def schedule_command(interaction: Interaction,
                           event_name: str,
                           voice_channel: VoiceChannel,
                           image_url: Optional[str] = None,
                           include_exclude: Optional[INCLUDE_EXCLUDE] = INCLUDE,
                           usernames: Optional[str] = None,
                           roles: Optional[str] = None,
                           duration: Optional[int] = DEFAULT_EVENT_DURATION,
                           multi_event: Optional[bool] = False,
                           timeout: Optional[int] = DEFAULT_EVENT_TIMEOUT_DAYS):
    await interaction.response.defer(ephemeral=True)
    logger.info(f'[{event_name}] Received event schedule request from {interaction.user.name}')
    try:
        await schedule(event_name=event_name,
                       guild=interaction.guild,
                       text_channel=interaction.channel,
                       voice_channel=voice_channel,
                       scheduler_id=interaction.user.id,
                       image_url=image_url,
                       include_exclude=include_exclude,
                       usernames=usernames,
                       roles=roles,
                       duration=duration,
                       multi_event=multi_event,
                       timeout_days=timeout)
        followup = await interaction.followup.send(content=f"Scheduling started for {event_name}.",
                                                   silent=True,
                                                   ephemeral=True)
        await followup.delete()
    except Exception as e:
        content = f"[{event_name}] Failed to schedule event: {e}"
        logger.error(content)
        await interaction.followup.send(content=content, ephemeral=True)


async def schedule(event_name: str,
                   guild: Guild,
                   text_channel: TextChannel,
                   voice_channel: VoiceChannel,
                   scheduler_id: Optional[int] = 0,
                   image_url: Optional[str] = None,
                   include_exclude: Optional[INCLUDE_EXCLUDE] = INCLUDE,
                   usernames: Optional[str] = None,
                   roles: Optional[str] = None,
                   duration: Optional[int] = DEFAULT_EVENT_DURATION,
                   multi_event: Optional[bool] = False,
                   timeout_days: Optional[int] = DEFAULT_EVENT_TIMEOUT_DAYS):
    """
    Starts the scheduling of an event.

    Arguments
    ---------
    event_name: :class:`str`
        The name of the event.
    guild: :class:`Guild`
        The guild that the event is occurring in.
    text_channel: :class:`TextChannel`
        The text channel that the event sends messages in.
    voice_channel: :class:`VoiceChannel`
        The voice channel that the event occurs in.
    scheduler_id: :class:`Optional[int]`
        The ID of the member who scheduled the event.
    image_url: :class:`Optional[str]`
        The URL for the image.
    include_exclude: :class:`Optional[INCLUDE_EXCLUDE]`
        Whether to include or exclude the usernames/ids/roles.
        Default: INCLUDE
        REQUIRES usernames or roles.
    usernames: :class:`Optional[str]` or :class:`Optional[list[str]]` or :class:`Optional[list[int]]` or :class:`Optional[list[Participant]]`
        Comma separated list of usernames or ids to include/exclude.
    roles: :class:`Optional[str]`
        Comma separated list of roles to include/exclude.
    duration: :class:`Optional[int]`
        The duration of the event in minutes.
        Default: DEFAULT_EVENT_DURATION
    multi_event: :class:`Optional[bool]`
        Whether or not the event is a multi event.
        Default: False
    timeout_days: :class:'Optional[int]'
        The number of days to make the event's timeout.
        Default: DEFAULT_EVENT_TIMEOUT_DAYS

    Returns
    -------
    event: :class:`Event`
        The event object created for scheduling.

    Exceptions
    -----------
    Exception: :class:`Exception`
        A string message describing the error.
    """
    # Voice channel
    if not guild.voice_channels:
        logger.info(f"[{event_name}] Scheduling cancelled due to no voice channel in guild")
        raise Exception("The server must have at least one voice channel to schedule an event.")

    # Event name
    if event_name in [event.name for event in client.events]:
        logger.info(f"[{event_name}] Scheduling cancelled due to existing name")
        raise Exception(f"Sorry, I already have an event called {event_name}. Please choose a different name.")

    # Scheduler user
    scheduler_user = None
    if scheduler_id != 0:
        scheduler_user = guild.get_member(scheduler_id)

    # Participants
    if isinstance(usernames, str):
        try:
            participants = get_participants_from_channel(event_name=event_name,
                                                         guild=guild,
                                                         channel=text_channel,
                                                         user=scheduler_user,
                                                         include_exclude=include_exclude,
                                                         usernames=usernames,
                                                         roles=roles)
        except Exception as e:
            logger.error(f"[{event_name}] Error getting participants: {e}")
            raise Exception(f"Failed to generate participants list: {e}")
    elif isinstance(usernames, list) and (
            all(isinstance(un, str) for un in usernames) or (
            all(isinstance(un, int) for un in usernames))):
        try:
            participants = get_participants_from_channel(event_name=event_name,
                                                         guild=guild,
                                                         channel=text_channel,
                                                         user=scheduler_user,
                                                         include_exclude=include_exclude,
                                                         usernames=", ".join(usernames),
                                                         roles=roles)
        except Exception as e:
            logger.error(f"[{event_name}] Error getting participants: {e}")
            raise Exception(f"Failed to generate participants list: {e}")
    elif isinstance(usernames, list) and all(isinstance(un, Participant) for un in usernames):
        participants = usernames
    elif usernames is None or not usernames:
        participants = get_participants_from_channel(event_name=event_name,
                                                     guild=guild,
                                                     channel=text_channel,
                                                     user=scheduler_user)
    else:
        logger.error(f"Invalid usernames type in schedule: {type(usernames)}")
        raise Exception(f"This is a code error, please inform the developer!\nInvalid usernames type in schedule: {type(usernames)}")

    # Scheduler participant
    scheduler = None
    for participant in participants:
        if participant.member.id == scheduler_user.id:
            logger.debug(f"[{event_name}] Scheduler participant found: {participant}")
            scheduler = participant
    if scheduler is None:
        logger.debug(f"[{event_name}] Scheduler participant not found, grabbing first participant: {participants[0]}")
        scheduler = participants[0]

    # Image URL
    if image_url == "":
        image_url = None

    # Timeout
    timeout = timeout_days * EVENT_TIMEOUT_CONSTANT_DAYS

    # Make event object
    duration = timedelta(minutes=duration)
    event = Event(name=event_name,
                  voice_channel=voice_channel,
                  scheduler=scheduler,
                  participants=participants,
                  guild=guild,
                  text_channel=text_channel,
                  image_url=image_url,
                  duration=duration,
                  multi_event=multi_event,
                  timeout_counter=timeout)
    client.events.append(event)

    remove_times_from_availabilities_for_events()
    for other_event in client.events:
        await other_event.update_messages()
    return event


@client.tree.command(name='edit', description='Edit an existing event.')
@app_commands.describe(name='Name for the event.')
@app_commands.describe(voice_channel='Voice channel for the event.')
@app_commands.describe(image_url='URL to an image for the event.')
@app_commands.describe(duration=f'Event duration in minutes ({DEFAULT_EVENT_DURATION} minutes default).')
@app_commands.describe(multi_event='Create an event on each date that everyone is available.')
@app_commands.describe(timeout=f'Number of days that the event should wait for responses ({DEFAULT_EVENT_TIMEOUT_DAYS} days default).')
async def edit_command(interaction: Interaction,
                       name: Optional[str] = None,
                       voice_channel: Optional[VoiceChannel] = None,
                       image_url: Optional[str] = None,
                       duration: Optional[int] = None,
                       multi_event: Optional[bool] = None,
                       timeout: Optional[int] = None):
    await interaction.response.defer(ephemeral=True, thinking=True)
    same_text_channel_events = []
    same_guild_events = []
    events = []
    for event in client.events:
        if event.text_channel == interaction.channel:
            same_text_channel_events.append(event)
        if event.guild == interaction.guild:
            same_guild_events.append(event)
    if same_text_channel_events:
        events = same_text_channel_events
    else:
        if same_guild_events:
            events = same_guild_events
        else:
            await interaction.followup.send("**No events were found in this guild.**",
                                            ephemeral=True)
            return
    # Only one event in this text channel/guild, edit it
    if len(events) == 1:
        event = events[0]
        embed = await edit_event(event=event,
                                 name=name,
                                 voice_channel=voice_channel,
                                 image_url=image_url,
                                 duration=duration,
                                 multi_event=multi_event,
                                 timeout_days=timeout)
        if interaction.user.avatar:
            embed.set_footer(text=f"Edited by {interaction.user}", icon_url=interaction.user.avatar.url)
        else:
            embed.set_footer(text=f"Edited by {interaction.user}")
        remove_times_from_availabilities_for_events()
        for event in client.events:
            await event.update_messages()
        await interaction.followup.send(embed=embed)
    # Multiple events in text channel/guild, select one to edit from a dropdown
    else:
        options = [SelectOption(label=event.get_limited_name(25), value=event.get_limited_name(25)) for event in events]
        select = Select(placeholder="Select an event to edit", options=options)

        async def select_callback(interaction: Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            for event in events:
                if event.get_limited_name(25) == select.values[0]:
                    embed = await edit_event(event=event,
                                             name=name,
                                             voice_channel=voice_channel,
                                             image_url=image_url,
                                             duration=duration,
                                             multi_event=multi_event,
                                             timeout_days=timeout)
                    if interaction.user.avatar:
                        embed.set_footer(text=f"Edited by {interaction.user}", icon_url=interaction.user.avatar.url)
                    else:
                        embed.set_footer(text=f"Edited by {interaction.user}")
                    remove_times_from_availabilities_for_events()
                    for event in client.events:
                        await event.update_messages()
                    await interaction.followup.send(embed=embed)
                    return

        select.callback = select_callback
        view = View()
        view.add_item(select)
        await interaction.followup.send(view=view, ephemeral=True)


@client.tree.command(name='attach', description='Create an event message for an existing guild event.')
async def attach_command(interaction: Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    logger.info(f'Received attach command request from {interaction.user.name}')
    guild_events = interaction.guild.scheduled_events
    if len(guild_events) == 1:
        guild_event = guild_events[0]
        logger.info(f'[{guild_event.name}] {interaction.user.name} attached to guild scheduled event')
        existingEvent = False
        # Event exists, adding guild event to that event
        for it_event in client.events:
            if guild_event.name == it_event.name:
                existingEvent = True
                it_event.text_channel = interaction.channel
                it_event.created = True
                if len(it_event.scheduled_events) == 0:
                    it_event.scheduled_events.append(guild_event)
                else:
                    it_event.scheduled_events[0] = guild_event
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
                          voice_channel=guild_event.channel,
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
            logger.info(f'[{event.name}] attached to event')
        remove_times_from_availabilities_for_events()
        for event in client.events:
            await event.update_messages()
        followup = await interaction.followup.send(content="Success!",
                                                   silent=True,
                                                   ephemeral=True)
        await followup.delete(delay=3)
    else:
        await interaction.followup.send(content="Select an existing guild event from the dropdown menu.",
                                        view=ExistingGuildEventsSelectView(interaction.guild),
                                        ephemeral=True)


@client.tree.command(name='listevents', description='List all events in this server.')
async def listevents_command(interaction: Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    foundEvents = False
    content = ""
    embeds = [Embed(title=f"All events in {interaction.guild.name}", color=Color.blue())]
    for event in client.events:
        if event.guild == interaction.guild:
            foundEvents = True
            embed = event.get_general_embed()
            embed.color = Color.dark_green()
            embeds.append(embed)
    if foundEvents:
        await interaction.followup.send(embeds=embeds, ephemeral=True)
    else:
        content = "**No events found for this server.**"
        await interaction.followup.send(content=content, ephemeral=True)


@client.tree.command(name='listmyevents', description='List all events that you are in.')
async def listmyevents_command(interaction: Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    foundEvents = False
    content = ""
    embeds = [Embed(title="All events you are in", color=Color.blue())]
    for event in client.events:
        if interaction.user.id in [p.member.id for p in event.participants]:
            foundEvents = True
            embed = event.get_general_embed()
            embed.color = Color.dark_green()
            embeds.append(embed)
    if foundEvents:
        await interaction.followup.send(embeds=embeds, ephemeral=True)
    else:
        content = "**You are not in any events.**"
        await interaction.followup.send(content=content, ephemeral=True)


@client.tree.command(name='availability', description='Show availabilities of an event.')
async def availability_command(interaction: Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    events = []
    for event in client.events:
        if event.text_channel is interaction.channel:
            events.append(event)
    if len(events) == 0:
        await interaction.followup.send(content="**No events were found using this text channel.**", ephemeral=True)
    elif len(events) == 1:
        embed = events[0].get_availability_embed()
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        options = [SelectOption(label=event.get_limited_name(25), value=event.get_limited_name(25)) for event in client.events]
        select = Select(placeholder="Select an event", options=options)

        async def select_callback(interaction: Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            for event in events:
                if event.get_limited_name(25) == select.values[0]:
                    embed = event.get_availability_embed()
                    await interaction.followup.send(embed=embed)
                    return

        select.callback = select_callback
        view = View()
        view.add_item(select)
        await interaction.followup.send(view=view, ephemeral=True)


@client.tree.command(name='offset', description='Set the midnight offset value.')
@app_commands.describe(offset='The offset in hours after midnight to automatically extend Full Availability to.')
async def offset_command(interaction: Interaction, offset: int = 2):
    global HOURS_PAST_MIDNIGHT_CUTOFF
    HOURS_PAST_MIDNIGHT_CUTOFF = offset
    await interaction.response.send_message(content=f"Midnight offset has been set to {offset}.")


@client.tree.command(name='help', description='Show helpful information.')
async def help_command(interaction: Interaction):
    await interaction.response.send_message(embeds=HELP_EMBEDS, ephemeral=True)


def first_start_time(event):
    """
    Gets the first start time of the event.

    Arguments
    ---------
    event: :class:`Event`
        The event to get the start time from.

    Returns
    -------
    time: :class:`datetime`
        The first start time of the event.
    """
    time = None
    try:
        time = event.start_times[0]
    except Exception as e:
        logger.error(f'Failed to access first start time: {e}')
    return time


def get_events_that_share_participants(event: Event) -> list[Event]:
    events = []
    for other_event in client.events:
        if other_event != event:
            for participant in event.participants:
                if participant.member.id in [p.member.id for p in other_event.participants]:
                    events.append(other_event)
    return events


def get_participants_other_events(event: Event, participant: Participant) -> list[Event]:
    events = []
    for other_event in client.events:
        if other_event != event and participant.member.id in [p.member.id for p in other_event.participants]:
            events.append(other_event)
    return events


def get_participants_other_unanswered_events_embed(event: Event, participant: Participant) -> list[Embed]:
    valid = False
    embed = Embed(title="Your Other Events",
                  description="Other events that you are in that require your availability.",
                  color=Color.yellow())
    for other_event in get_participants_other_events(event, participant):
        for other_participant in other_event.participants:
            if other_participant.member.id == participant.member.id:
                if not other_participant.answered:
                    valid = True
                    embed.add_field(name=other_event.get_limited_name(25),
                                    value=other_event.text_channel.mention,
                                    inline=False)
                break
    return embed if valid else None


def remove_times_from_availabilities_for_events() -> None:
    """Removes and saves timeblocks from shared participants with other events."""
    for event in client.events:
        for other_event in client.events:
            if other_event == event:
                continue
            for shared_participant in event.other_shared_participants(other_event, True):
                shared_participant.remove_availability_for_event(event_name=event.name,
                                                                 event_timeblocks=event.timeblocks)


@tasks.loop(seconds=UPDATE_INTERVAL)
async def update():
    for event in client.events.copy():
        # This looks silly, but it may prevent bugs
        # such as the Forget button being pressed
        # while it is looping through the events.
        if event in client.events:
            await event.update()
            for participant in event.participants:
                for removed_time in participant.removed_times.copy():
                    if removed_time.event_name not in [event.name for event in client.events]:
                        participant.restore_availability_for_event(removed_time.event_name)
    for schedule_again_event in client.schedule_again_events.copy():
        # This looks silly, but it may prevent bugs
        # such as the Forget button being pressed
        # while it is looping through the events.
        if schedule_again_event in client.schedule_again_events:
            await schedule_again_event.update()
    save()


client.run(DISCORD_TOKEN)
