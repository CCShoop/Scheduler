'''Written by Cael Shoop.'''

import os
import time
import logging
import asyncio
import aiohttp
from typing import Literal
from dotenv import load_dotenv
from typing import Optional
from datetime import datetime, timedelta
from discord import (app_commands, Interaction, Intents, Client, Embed, Color, Activity,
                     ButtonStyle, EntityType, TextChannel, ActivityType,
                     VoiceChannel, Message, SelectOption, ScheduledEvent, Member,
                     Guild, PrivacyLevel, User, utils, NotFound, HTTPException)
from discord.ui import View, Button, Modal, TextInput, Select
from discord.ext import tasks

from libs.persistence import Persistence
from libs.participant import Participant, TimeBlock, HOURS_PAST_MIDNIGHT_CUTOFF
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

# Time in minutes to delay "immediate" start
START_TIME_DELAY = 11

# Time in minutes before an event to send warning
WARNING_TIME_MINUTES = 6

# Time in seconds between updates
UPDATE_INTERVAL: int = 1

# Default length of events in minutes
DEFAULT_EVENT_DURATION: int = 30

# Number of updates before an event is cleared
UPDATES_PER_MINUTE: int = 60 // UPDATE_INTERVAL
MINUTES_PER_HOUR: int = 60
HOURS_PER_DAY: int = 24
EVENT_TIMEOUT_DAYS: int = 3
EVENT_TIMEOUT: int = UPDATES_PER_MINUTE * MINUTES_PER_HOUR * HOURS_PER_DAY * EVENT_TIMEOUT_DAYS

RESEND_INTERVAL_HOURS: int = 23
RESEND_INTERVAL: int = UPDATES_PER_MINUTE * MINUTES_PER_HOUR * RESEND_INTERVAL_HOURS

OFFSET = EVENT_TIMEOUT % RESEND_INTERVAL


def save() -> None:
    """
    Saves the bot's status by writing the client's events to a file.
    """
    persist.write(client.events_dict)


def get_time_str_from_minutes(minutes: int) -> str:
    """
    Makes a formatted string including weeks, days, hours, and minutes.

    Arguments
    ----------
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
    if hours != 0:
        output.append(f"{hours} hours" if hours != 1 else "1 hour")
    mins = int(minutes % 60)
    if mins != 0:
        output.append(f"{mins} minutes" if mins != 1 else "1 minute")
    if mins == 0 and hours == 0 and days == 0 and weeks == 0:
        output.append("0 minutes")
    return ", ".join(output)


# Add a 0 if the digit is < 10
def double_digit_string(digit_string: str) -> str:
    """
    Adds 0 if a digit string is < 10.

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
                       multiEvent=data["multiEvent"],
                       sendAvailabilityMessage=True)

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

    @property
    def events_dict(self) -> dict:
        """
        Shove all events into a dictionary for writing to the data file.

        Returns
        --------
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
    availability_message_lock: :class:`asyncio.Lock`
        The lock to prevent the availability message update from being called twice simultaneously.
    availability_message: :class:`Message`
        The message object that is requesting availability from participants.
    avail_buttons: :class:`AvailabilityButtons`
        The availability buttons attached to the availability_message that users can
        use to submit their availability, unsubscribe, or cancel.
    event_buttons_message_lock: :class:`asyncio.Lock`
        The lock to prevent the event buttons message update from being called twice simultaneously.
    event_buttons_message: :class:`Message`
        The event control buttons message. States the start time, time remaining until
        the start time, when the event was started, when the event was rescheduled,
        the event's duration, and when the event was ended.
    event_buttons: :class:`EventButtons`
        The event control buttons attached to the event_buttons_message. These allow
        for starting, ending, unsubscribing from, rescheduling, and cancelling the event.
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
    five_minute_warning_flag: :class:`bool`
        Indicator of whether a five minute warning message has been sent.
    five_minute_warning_message: :class:`Message`
        The message object warning participants that an event is starting in 5 minutes.
    timeout_counter: :class:`int`
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
                 participants: Optional[list] = None,
                 duration: Optional[timedelta] = timedelta(minutes=DEFAULT_EVENT_DURATION),
                 multi_event: Optional[bool] = False,
                 start_times: Optional[list] = None,
                 availability_message: Optional[Message] = None,
                 avail_buttons=None,
                 event_buttons_message: Optional[Message] = None,
                 event_buttons=None,
                 ready_to_create: Optional[bool] = False,
                 created: Optional[bool] = False,
                 started: Optional[bool] = False,
                 scheduled_events: Optional[list] = None,
                 changed: Optional[bool] = False,
                 five_minute_warning_flag: Optional[bool] = False,
                 five_minute_warning_message: Optional[Message] = None,
                 timeout_counter: Optional[int] = EVENT_TIMEOUT) -> None:
        self.name = name
        self.guild = guild
        self.entity_type = EntityType.voice
        self.text_channel = text_channel
        self.availability_message_lock: asyncio.Lock = asyncio.Lock()
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
        self.event_buttons_message_lock: asyncio.Lock = asyncio.Lock()
        self.event_buttons_message: Message = event_buttons_message
        self.event_buttons: EventButtons = event_buttons
        self.ready_to_create = ready_to_create
        self.created = created
        self.started = started
        self.scheduled_events: list = scheduled_events if scheduled_events is not None else []
        self.changed = changed
        self.five_minute_warning_flag = five_minute_warning_flag
        self.five_minute_warning_message = five_minute_warning_message
        self.start_times: list = start_times or []
        self.duration = duration
        self.multi_event = multi_event
        self.timeout_counter: int = timeout_counter
        self.previous_countdown: int = self.timeout_counter

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
            Send 5 minute warning when appropriate.
            Start the event if all participants are in the voice channel.
            End the event if nobody is in the voice channel.
        """
        if not self.created:
            # Timeout check
            cancelled = await self.update_timeout()
            if cancelled:
                return
            # Update availability message once per minute
            if self.timeout_minutes != self.previous_countdown:
                self.previous_countdown = self.timeout_minutes
                await self.update_availability_message()
        # Event has been created
        else:
            if not self.started:
                # Update event buttons message once per minute
                if self.mins_until_start != self.previous_countdown:
                    self.previous_countdown = self.mins_until_start
                    await self.update_event_buttons_message()
                # If 5 minute warning has not been sent yet
                if not self.five_minute_warning_flag:
                    cur_time = datetime.now().astimezone().replace(second=0, microsecond=0)
                    if cur_time + timedelta(minutes=WARNING_TIME_MINUTES) == self.start_times[0]:
                        await self.send_five_minute_warning()
                # 5 minute warning has been sent
                else:
                    await self.start_if_participants_in_vc()
            # Event has started
            else:
                await self.end_if_participants_leave_vc()
                return

    async def update_timeout(self) -> bool:
        """
        Updates the event's timeout counter,
        resends the availability message every RESEND_INTERVAL hours,
        and cancels the event if it times out.

        Returns
        --------
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
            cur_time = datetime.now().astimezone().replace(second=0, microsecond=0)
            for participant in self.participants:
                participant.confirm_answered(duration=self.duration, latest_date=self.latest_date)
            if self.everyone_answered:
                self.compare_availabilities()
                # Create the event
                if self.ready_to_create:
                    # Ensure start time is in the future
                    if self.start_times[0] <= cur_time:
                        logger.warning(f"[{self}] Tried to create event in the past, moving to {START_TIME_DELAY} minutes from now")
                        self.start_times[0] = cur_time + timedelta(minutes=START_TIME_DELAY)
                    # Create the event
                    await self.make_scheduled_events()
                    self.previous_countdown = self.mins_until_start
                    remove_times_from_availabilities_for_events()
                    for event in client.events:
                        await event.update_messages()
                    return
            await self.update_availability_message()

    def clean_participants_removed_times(self) -> None:
        """
        Restores removed times for no longer active events.
        """
        for participant in self.participants:
            for removed_time in participant.removed_times.copy():
                if removed_time.event_name not in [event.name for event in client.events]:
                    participant.restore_availability_for_event(event_name=removed_time.event_name)

    def intersect_time_blocks(self, timeblocks1: list, timeblocks2: list) -> list[TimeBlock]:
        """
        Gets all timeblocks in the two availabilities that intersect.

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
        """
        Compares availabilites of all subscribed participants to select (a) start time(s) for the event.
        """
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
        available_timeblocks = [participant.availability for participant in subbed_participants]

        # Get intersected availability
        intersected_timeblocks = available_timeblocks[0]
        for timeblocks in available_timeblocks[1:]:
            intersected_timeblocks = self.intersect_time_blocks(intersected_timeblocks, timeblocks)

        # Get events in the same voice channel
        conflicting_events = [event for event in client.events if event.voice_channel == self.voice_channel]
        occupied_timeblocks = [
            TimeBlock(event.start_time, event.start_time + event.duration)
            for event in conflicting_events
        ]

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

    async def reschedule(self, rescheduler: Participant) -> None:
        self.reset_timeout_counter()
        for scheduled_event in self.scheduled_events:
            try:
                await scheduled_event.delete(reason=f"Reschedule button pressed by {rescheduler}.")
            except Exception as e:
                logger.error(f"[{self}] Error cancelling guild event to reschedule: {e}")
        self.scheduled_events.clear()
        self.start_times.clear()
        self.ready_to_create = False
        self.created = False
        self.five_minute_warning_flag = False
        await self.update_event_buttons_message()
        await self.update_availability_message(rescheduler=rescheduler)
        # Restore removed availabilities
        for other_event in client.events:
            other_event.restore_availabilities(self)
            other_event.clean_participants_removed_times()
            await other_event.update_messages()

    async def send_five_minute_warning(self) -> None:
        """
        Sends the 5 minute warning for the event.
        """
        if self.five_minute_warning_flag:
            return
        self.five_minute_warning_flag = True
        try:
            message = f'{self.get_names_string(subscribed_only=True, mention=True)}'
            embed = Embed(title="5 Minute Warning!",
                          description=f"{self} is scheduled to start in 5 minutes.",
                          color=Color.orange())
            embed.timestamp = self.start_times[0]
            if self.image_url:
                embed.set_thumbnail(url=self.image_url)
            embed.set_footer(text="Courtesy of Event Scheduler", icon_url=client.user.avatar.url)
            self.five_minute_warning_message = await self.text_channel.send(content=message,
                                                                            embed=embed,
                                                                            reference=self.event_buttons_message)
        except Exception as e:
            logger.error(f'Error sending 5 minute warning: {e}')

    async def start(self, reason: Optional[str] = f"Event started by {client.user}.") -> None:
        """
        Starts the event.

        Arguments
        ----------
        reason: :class:`Optional[str]`
            Reason to provide for guild event start in audit log.
        """
        logger.info(f"[{self}] Starting, reason: {reason}")
        if self.five_minute_warning_message is not None:
            self.five_minute_warning_message.delete()
            self.five_minute_warning_message = None
        try:
            await self.scheduled_events[0].start(reason=reason)
        except Exception as e:
            logger.exception(f'[{self}] Failed to start: {e}')
        try:
            self.start_times[0] = datetime.now().astimezone().replace(second=0, microsecond=0)
        except Exception as e:
            logger.warning(f'[{self}] Error getting start time: {e}')
            self.start_times.append(datetime.now().astimezone().replace(second=0, microsecond=0))
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

    async def start_if_participants_in_vc(self) -> None:
        """
        Starts the event if all of the participants are in the voice channel
        and there are no active events in that voice channel.
        """
        if datetime.now().astimezone() < self.start_times[0] - timedelta(WARNING_TIME_MINUTES):
            return
        for event in client.events:
            if event is not self and event.voice_channel is self.voice_channel and event.started:
                return
        if all(participant.member in self.voice_channel.members for participant in self.participants):
            await self.start(f'Event started by {client.user} because all users were in the voice channel.')
            logger.info(f"[{self}] Started guild event because everyone was in the voice channel")

    async def end(self, reason: Optional[str] = f"Event ended by {client.user}.") -> None:
        """
        Ends the event. If there are more scheduled events in this event, shift them forward and prep them.

        Arguments
        ----------
        reason: :class:`Optional[str]`
            The reason to provide to the audit log for ending the guild event.
        """
        logger.info(f"[{self}] Ending, reason: {reason}")
        # Delete scheduled event
        try:
            await self.scheduled_events[0].delete(reason=reason)
        except Exception as e:
            logger.error(f"[{self}] Error in event control end button callback while ending scheduled event: {e}")
        # Update event buttons message
        end_time: datetime = datetime.now().astimezone().replace(second=0, microsecond=0)
        content = self.get_event_buttons_message_content(end_time)
        embeds = self.get_event_buttons_message_embeds(end_time)
        try:
            self.event_buttons = None
            await self.event_buttons_message.edit(content=content, embeds=embeds, view=None)
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
        # Restore removed availabilities
        for event in client.events:
            event.restore_availabilities(self)
            event.clean_participants_removed_times()
            await event.update_messages()
        save()

    async def end_if_participants_leave_vc(self) -> None:
        """
        Ends the event if all of the participants have left the voice channel.
        """
        if not any(participant.member in self.voice_channel.members for participant in self.participants):
            await self.end(f'Event ended by {client.user} because no users were in the voice channel.')

    async def prep_next_scheduled_event(self) -> bool:
        """
        Preps the next guild scheduled event and update the event control buttons message.

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
        """
        Creates a scheduled event for each start time and sets the guild event's image if appropriate.
        """
        for start_time in self.start_times:
            scheduled_event = await self.guild.create_scheduled_event(name=self.name,
                                                                      description='Bot-generated event',
                                                                      start_time=start_time,
                                                                      entity_type=self.entity_type,
                                                                      channel=self.voice_channel,
                                                                      privacy_level=self.privacy_level)
            await self.save_image_to_file()
            if self.has_image_saved:
                await scheduled_event.edit(image=self.get_image())
            self.scheduled_events.append(scheduled_event)
            logger.info(f'[{self}] Created event starting {start_time.strftime("%A, %m/%d/%Y: %H:%M %Z")}')
            if not self.multi_event:
                break
        self.ready_to_create = False
        self.created = True
        save()

    def get_general_embed(self, end_time: Optional[datetime] = None) -> Embed:
        """
        Gets the general embed for the event with status, image thumbnail, duration, location, etc.

        Arguments
        ----------
        end_time: :class:`Optional[datetime]`
            If the event has ended, includes the provided end time in the embed.

        Returns
        --------
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
                        value=self.voice_channel.mention,
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
                                    value=f"{get_time_str_from_minutes(self.mins_until_start)}",
                                    inline=False)
                elif self.mins_until_start == 0:
                    embed.add_field(name="Starting soon", value="", inline=False)
                else:
                    embed.add_field(name="Overdue by",
                                    value=f"{get_time_str_from_minutes(self.mins_until_start)}",
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
                                value=f'{end_time.strftime("%A, %m/%d at %H:%M %Z")}',
                                inline=False)
        if self.created:
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
            save()
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
        --------
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
        ----------
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
        ----------
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

    def shared_participants(self, event) -> list[Participant]:
        """
        Gets the list of this event's participants shared with the provided event.

        Arguments
        ----------
        event: :class:`Event`
            The event to compare participants with.

        Returns
        --------
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

    def other_shared_participants(self, event) -> list[Participant]:
        """
        Gets the list of the other event's participants shared with this event.

        Arguments
        ----------
        event: :class:`Event`
            The event to compare participants with.

        Returns
        --------
        other_participants: :class:`list`
            The list of the other event's participants shared with this event.
            Empty if the events are the same or do not share participants.
        """
        other_participants = []
        if event is not self:
            for participant in self.participants:
                for other_participant in event.participants:
                    if participant.member.id == other_participant.member.id:
                        other_participants.append(other_participant)
        return other_participants

    def get_other_availability(self, participant: Participant) -> list:
        """
        Gets availability of a participant from another event that they are in.

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
        self.timeout_counter = EVENT_TIMEOUT

    def get_start_time_string(self, index: int = 0) -> str:
        """
        Gets the string for the start time at the provided index.

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
        if index >= 0 and len(self.start_times) > index:
            return f'{self.start_times[index].strftime("%A, %m/%d at %H:%M %Z")}'
        return ''

    def get_availability_request_content(self) -> str:
        """
        Gets the content string for the availability message.

        Returns
        --------
        output: :class:`str`
            The content string for the availability message.
        """
        output = ""
        if not self.everyone_answered:
            cur_date = datetime.now().astimezone().date()
            latest_date = self.latest_date
            if latest_date and cur_date < latest_date:
                output += f'\n\n**Input availability with start time on latest availability date: {latest_date.strftime("%m/%d")}**'
            mentions = self.get_names_string(subscribed_only=True, unanswered_only=True, mention=True)
            output += f'\n\nWaiting for a response from:{mentions}'
        else:
            output += '\n\nEveryone has responded.'
        return output

    def get_availability_request_embeds(self) -> list[Embed]:
        """
        Gets the embeds for the availability message.

        Returns
        --------
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
        --------
        embed: :class:`Embed`
            The embed containing each participant's availability.
        """
        embed = Embed(title='Availabilities', color=Color.blue())
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
        Restores availabilities that were modified by provided event's creation.

        Arguments
        ----------
        event: :class:`Event`
            The name of the event to restore availability for shared participants.
        """
        if event == self:
            return
        logger.info(f"[{event}] Restored availabilities for {self}")
        # for other_participant in self.other_shared_participants(event):
        for participant in self.participants:
            participant.restore_availability_for_event(event.name)
            participant.confirm_answered(duration=self.duration,
                                         latest_date=self.latest_date)

    def update_availabilities_to(self, participant: Participant) -> None:
        """
        Updates end time of full flag availabilities to the latest time.

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

    def get_event_buttons_message_content(self, end_time: Optional[datetime] = None) -> str:
        """
        Gets the content for the event buttons message.

        Returns
        --------
        content: :class:`str`
            The content for the event buttons message.
        """
        if not self.started:
            return self.get_names_string(subscribed_only=True, mention=True, not_in_voice_channel_only=True)
        else:
            return ""

    def get_event_buttons_message_embeds(self, end_time: Optional[datetime] = None) -> list[Embed]:
        """
        Gets the embeds for the event buttons message.

        Arguments
        ----------
        end_time: :class:`Optional[datetime]`
            The end time of the event to put in the embed.

        Returns
        --------
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

    async def create_availability_message(self, interaction: Interaction) -> None:
        async with self.availability_message_lock:
            content = self.get_availability_request_content()
            embeds = self.get_availability_request_embeds()
            if self.avail_buttons is None:
                self.avail_buttons = AvailabilityButtons(event=self)
            self.availability_message = await interaction.followup.send(content=content,
                                                                        embeds=embeds,
                                                                        view=self.avail_buttons)

    async def update_availability_message(self, rescheduler: Optional[Participant] = None) -> None:
        """
        Update the availability message.

        Arguments
        ----------
        rescheduler: :class:`Participant`
            Optional. The participant who rescheduled the event.
            Default: None
        """
        async with self.availability_message_lock:
            # Delete the message if the event was created
            if self.created:
                if self.availability_message is not None:
                    await self.availability_message.delete()
                    self.availability_message = None
                self.avail_buttons = None
                return
            if rescheduler is not None:
                self.scheduler = None
                self.rescheduler = rescheduler
            content = self.get_availability_request_content()
            embeds = self.get_availability_request_embeds()
            if self.avail_buttons is None:
                self.avail_buttons = AvailabilityButtons(event=self)
            # Send a new message
            if self.availability_message is None:
                if self.rescheduler is not None:
                    self.rescheduler.set_no_availability()
                self.availability_message = await self.text_channel.send(content=content,
                                                                         embeds=embeds,
                                                                         view=self.avail_buttons)
            # Update existing message
            else:
                try:
                    await self.availability_message.edit(content=content,
                                                         embeds=embeds,
                                                         view=self.avail_buttons)
                except Exception as e:
                    logger.exception(f'[{self}] Failed to edit availability message in update: {e}')

    async def delete_availability_message(self) -> None:
        async with self.availability_message_lock:
            if self.availability_message is not None:
                await self.availability_message.delete()
                self.availability_message = None

    async def create_event_buttons_message(self, interaction: Interaction) -> None:
        async with self.event_buttons_message_lock:
            content = self.get_event_buttons_message_content()
            embeds = self.get_event_buttons_message_embeds()
            if not self.event_buttons:
                self.event_buttons = EventButtons(self)
            self.event_buttons_message = await interaction.followup.send(content=content,
                                                                         embeds=embeds,
                                                                         view=self.event_buttons)

    async def update_event_buttons_message(self) -> None:
        """
        Updates the event buttons message.
        """
        async with self.event_buttons_message_lock:
            if not self.created:
                # Delete the message if the event was rescheduled
                if self.event_buttons_message is not None:
                    await self.event_buttons_message.delete()
                    self.event_buttons_message = None
                self.event_buttons = None
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
            # Edit existing message
            else:
                await self.event_buttons_message.edit(content=content,
                                                      embeds=embeds,
                                                      view=self.event_buttons)

    def get_cancel_embed(self, reason: Optional[str] = "", canceller: Optional[str] = "") -> Embed:
        """
        Get the embed for the cancel message.

        Arguments
        ----------
        reason: :class:`str`
            The reason the event is being cancelled.
        canceller: :class:`str`
            The name of the canceller of the event.

        Returns
        --------
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

    async def cancel(self, reason: Optional[str] = "", canceller: Optional[str] = "") -> None:
        """
        Cancels the event.

        Arguments
        ----------
        reason: :class:`str`
            The reason for the cancellation of the event.
        canceller: :class:`str`
            The name of the canceller of the event.
        """
        content = self.get_names_string(subscribed_only=True, mention=True)
        embed = self.get_cancel_embed(reason, canceller)
        await self.text_channel.send(content=content, embed=embed)
        if self.availability_message is not None:
            await self.availability_message.delete()
            self.availability_message = None
        if self.event_buttons_message is not None:
            await self.event_buttons_message.delete()
            self.event_buttons_message = None
        if self.five_minute_warning_message is not None:
            self.five_minute_warning_message.delete()
            self.five_minute_warning_message = None
        try:
            if len(self.scheduled_events) > 0:
                await self.scheduled_events[0].delete(reason=f'Cancel button pressed by {canceller}: {reason}')
        except Exception as e:
            logger.error(f'[{self}] Error in cancel while deleting scheduled event: {e}')
        try:
            anotherEvent = await self.prep_next_scheduled_event()
        except Exception as e:
            logger.error(f'[{self}] Error in cancel while prepping next scheduled event: {e}')
        if not anotherEvent:
            self.remove()
        # Restore removed availabilities
        for event in client.events:
            event.restore_availabilities(self)
            event.clean_participants_removed_times()
            await event.update_messages()

    def remove(self) -> None:
        """
        Deletes the event's image file and removes the event from the client's event list.
        """
        self.delete_image_file()
        client.events.remove(self)
        logger.info(f'[{self}] Removed from client events list')

    @property
    def scheduling_status(self) -> str:
        """
        Gets the current event status.

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
        if self.everyone_answered:
            return "No common availability"
        return "Awaiting availability"

    @property
    def everyone_answered(self) -> bool:
        """
        Indicates whether or not all participants have responded.

        Returns
        --------
        True
            If all participants have responded.
        False
            If at least one participant has not yet responded.
        """
        latest_date = self.latest_date
        for participant in self.participants:
            if participant.subscribed:
                participant.confirm_answered(duration=self.duration, latest_date=latest_date)
                if not participant.answered:
                    return False
        return True

    @property
    def has_image_saved(self) -> bool:
        """
        Indicates whether the event has an image saved.

        Returns
        --------
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
        --------
        responded: :class:`int`
            The number of participants who are subscribed and have responded to the event.
        """
        responded = 0
        for participant in self.participants:
            if participant.subscribed and participant.answered:
                responded += 1
        return responded

    @property
    def latest_date(self):
        """
        Gets the latest date of all start times in all participants' availabilities.

        Returns
        --------
        latest_date: :class:`datetime.date`
            The latest date of all start times in all participants' availabilities.
            None if this event is not a multi-event.
        """
        if not self.multi_event:
            return None
        current_time = datetime.now().astimezone().replace(second=0, microsecond=0) + timedelta(minutes=START_TIME_DELAY)
        latest_date = current_time.date()
        for participant in self.participants:
            for timeblock in participant.availability:
                latest_date = max((timeblock.start_time - timedelta(hours=HOURS_PAST_MIDNIGHT_CUTOFF)).date(), latest_date)
        return latest_date

    @property
    def location_has_active_event(self) -> bool:
        """
        Indicates if the event's :class:`VoiceChannel` has a different active event in it.

        Returns
        --------
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
    def mins_until_start(self) -> int:
        time_until_start: timedelta = self.start_times[0] - datetime.now().astimezone()
        return int(time_until_start.total_seconds() // 60)

    @property
    def duration_minutes(self) -> int:
        return self.duration.total_seconds() // 60

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

        # 5 minute warning flag
        try:
            five_minute_warning_flag = data["five_minute_warning_flag"]
        except Exception:
            five_minute_warning_flag = False

        # 5 minute warning message
        try:
            five_minute_warning_message = await event_text_channel.fetch_message(data["five_minute_warning_message_id"])
        except Exception:
            five_minute_warning_message = None

        # Start time
        try:
            event_start_times = [datetime.fromisoformat(start_time) for start_time in data["start_times"]]
            for event_start_time in event_start_times:
                logger.info(f'[{event_name}] start time found: {event_start_time.strftime("%a, %m/%d/%Y %H:%M %Z")}')
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
            ready_to_create=event_ready_to_create,
            created=event_created,
            started=event_started,
            scheduled_events=event_scheduled_events,
            changed=event_changed,
            five_minute_warning_flag=five_minute_warning_flag,
            five_minute_warning_message=five_minute_warning_message,
            start_times=event_start_times,
            duration=event_duration,
            multi_event=event_multi_event,
            timeout_counter=event_timeout_counter
        )

    def to_dict(self) -> dict:
        """
        Packs the event into a dict for saving.

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
            five_minute_warning_message_id = self.five_minute_warning_message.id
        except Exception:
            five_minute_warning_message_id = 0
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
            'changed': self.changed,
            'five_minute_warning_flag': self.five_minute_warning_flag,
            'five_minute_warning_message_id': five_minute_warning_message_id,
            'start_times': start_times,
            'duration': self.duration_minutes,
            'multi_event': self.multi_event,
            'timeout_counter': self.timeout_counter
        }

    def __repr__(self) -> str:
        """
        Gets the name of the event for string formatting purposes.

        Returns
        --------
        name: :class:`str`
            The name of the event.
        """
        return f'{self.name}'


class CancelModal(Modal):
    """
    Represents a modal for cancelling an event.

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
        self.timeslot1 = TextInput(label='Timeslot 1', placeholder='8-11, 1pm-3pm (i.e. Available 0800-1100, 1300-1500)', default='', required=False)
        self.timeslot2 = TextInput(label='Timeslot 2', placeholder='15:30-17 (i.e. Available 1530-1700)', default='', required=False)
        self.note = TextInput(label='Note', placeholder='A note to show with your availability', default='', required=False)
        self.date = TextInput(label='Date', placeholder='MM/DD/YYYY', default=date)
        self.timezone = TextInput(label='Timezone', placeholder='AT|AST|ADT|ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|PT|PST|PDT', default='ET')
        self.add_item(self.timeslot1)
        self.add_item(self.timeslot2)
        self.add_item(self.note)
        self.add_item(self.date)
        self.add_item(self.timezone)

    async def on_submit(self, interaction: Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        # Participant availability
        participant = self.event.get_participant(interaction.user.name)
        if participant is None:
            member = self.event.guild.get_member(interaction.user.id)
            participant = Participant(member=member)
            self.event.participants.append(participant)
        participant.subscribed = True
        avail_string = f'{self.timeslot1.value}, {self.timeslot2.value} {self.timezone.value}'
        try:
            logger.info(f'[{self.event}] Received availability from {interaction.user.name}')
            logger.info(f'[{self.event}] Raw input: "{avail_string}"')
            participant.set_specific_availability(avail_string, self.date.value, self.note.value)
            participant.confirm_answered(duration=self.event.duration, latest_date=self.event.latest_date)
            remove_times_from_availabilities_for_events()
            await self.event.create_if_possible()
        except Exception as e:
            logger.exception(f"[{self.event}] Error setting specific availability: {e}")

    async def on_error(self, interaction: Interaction, error: Exception) -> None:
        await interaction.response.send_message(f"Error getting availability: {error}", ephemeral=True)
        logger.exception(f"[{self.event}] Error getting availability from {interaction.user.name} (AvailabilityModal): {error}")


class AvailabilityButtons(View):
    """
    Represents the availability buttons tied to an availability message.

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
        """
        Sets up and gets the Respond button.

        Returns
        --------
        button: :class:`Button`
            The Respond button.
        """
        button = Button(label=self.respond_label, style=ButtonStyle.green)

        async def respond_button_callback(interaction: Interaction):
            am_title = f'Availability for {self.event}'
            if len(am_title) >= 45:
                am_title = f"{am_title[:41]}..."
            await interaction.response.send_modal(AvailabilityModal(event=self.event,
                                                                    title=am_title))
            save()
        button.callback = respond_button_callback
        self.add_item(button)
        return button

    def add_full_button(self) -> Button:
        """
        Sets up and gets the Full Availability button.

        Returns
        --------
        button: :class:`Button`
            The Full Availability button.
        """
        button = Button(label=self.full_label, style=ButtonStyle.green)

        async def full_button_callback(interaction: Interaction):
            await interaction.response.defer(ephemeral=True)
            participant = self.event.get_participant(interaction.user.name)
            if participant is None:
                await interaction.followup.send(content="Could not add you as a participant!")
                return
            participant.subscribed = True
            # Participant has full availability
            if not participant.full_availability_flag:
                logger.info(f'[{self.event}] {participant} selected full availability')
                participant.set_full_availability()
                self.event.update_availabilities_to(participant)
                await self.event.create_if_possible()
            # Participant no longer has full availability
            else:
                logger.info(f'[{self.event}] {participant} deselected full availability')
                participant.set_no_availability()
                await self.event.update_availability_message()
            save()
        button.callback = full_button_callback
        self.add_item(button)
        return button

    def add_reuse_button(self) -> Button:
        """
        Sets up and gets the Reuse Availability button.

        Returns
        --------
        button: :class:`Button`
            The Reuse Availability button.
        """
        button = Button(label=self.reuse_label, style=ButtonStyle.blurple)

        async def reuse_button_callback(interaction: Interaction):
            await interaction.response.defer(ephemeral=True)
            participant = self.event.get_participant(interaction.user.name)
            if participant is None:
                await interaction.followup.send("Could not add you as a participant!")
                return
            found_availabilities = self.event.get_other_availability(participant)
            if not found_availabilities:
                logger.info(f'[{self.event}] No existing availability found for {interaction.user.name}')
                await interaction.followup.send("No existing availability found.", ephemeral=True)
                return
            logger.info(f'[{self.event}] Found existing availability for {interaction.user.name}')
            if len(found_availabilities) == 1:
                participant.availability = found_availabilities[0].avail.copy()
                participant.answered = True
                await self.event.update_availability_message()
            else:
                await interaction.followup.send(content="Select another event to grab your availability from.",
                                                view=ExistingAvailabilitiesSelectView(found_availabilities, participant),
                                                ephemeral=True)
            save()
        button.callback = reuse_button_callback
        self.add_item(button)
        return button

    def add_unsub_button(self) -> Button:
        """
        Sets up and gets the Unsubscribe button.

        Returns
        --------
        button: :class:`Button`
            The Unsubscribe button.
        """
        button = Button(label=self.unsub_label, style=ButtonStyle.red)

        async def unsub_button_callback(interaction: Interaction):
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
                await interaction.followup.send(content=f"You have been unsubscribed from {self.event}.",
                                                ephemeral=True)
            else:
                logger.info(f'[{self.event}] {interaction.user.name} resubscribed')
                participant.subscribed = True
                participant.confirm_answered(duration=self.event.duration,
                                             latest_date=self.event.latest_date)
                await interaction.followup.send(content=f"You have been resubscribed to {self.event}.",
                                                        ephemeral=True)
            await self.event.update_availability_message()
            save()
        button.callback = unsub_button_callback
        self.add_item(button)
        return button

    def add_cancel_button(self) -> Button:
        """
        Sets up and gets the Cancel button.

        Returns
        --------
        button: :class:`Button`
            The Cancel button.
        """
        button = Button(label=self.cancel_label, style=ButtonStyle.red)

        async def cancel_button_callback(interaction: Interaction):
            title = f"Cancel {self.event.name}"
            if len(title) >= 38:
                title = f"{title[:34]}..."
            await interaction.response.send_modal(CancelModal(event=self.event,
                                                              title=title))
            save()
        button.callback = cancel_button_callback
        self.add_item(button)
        return button


class EventButtons(View):
    """
    Represents the event buttons attached to an event control message.

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
        """
        Sets up the Start button.

        Returns
        --------
        button: :class:`Button`
            The Start button.
        """
        async def start_button_callback(interaction: Interaction):
            await interaction.response.defer(ephemeral=True)
            self.event.add_user_as_participant(interaction.user)
            if interaction.user.id not in [member.id for member in self.event.voice_channel.members]:
                logger.info(f"[{self.event}] {interaction.user} tried to press start button while not in the event's voice channel")
                content = f"You must be in {self.event.voice_channel.mention} to start {self.event}!"
                await interaction.followup.send(content=content, ephemeral=True)
                return
            logger.info(f"[{self.event}] {interaction.user} started by button press")
            await self.event.start(reason=f"Event started by {interaction.user} pressing start button.")
        self.start_button.callback = start_button_callback
        if self.event.location_has_active_event:
            self.start_button.disabled = True
        self.add_item(self.start_button)

    def add_end_button(self) -> None:
        """
        Sets up the End button.

        Returns
        --------
        button: :class:`Button`
            The End button.
        """
        self.end_button.disabled = True

        async def end_button_callback(interaction: Interaction):
            await interaction.response.defer(ephemeral=True)
            if interaction.user.id not in [participant.member.id for participant in self.event.participants]:
                await interaction.followup.send(content="You are not a participant of this event!",
                                                ephemeral=True)
                return
            await self.event.end(f"Event ended by {interaction.user} pressing end button.")
        self.end_button.callback = end_button_callback
        self.add_item(self.end_button)

    def add_unsubscribe_button(self) -> None:
        """
        Sets up the Unsubscribe button.

        Returns
        --------
        button: :class:`Button`
            The Unsubscribe button.
        """
        async def unsubscribe_button_callback(interaction: Interaction):
            await interaction.response.defer(ephemeral=True)
            if interaction.user.id not in [participant.member.id for participant in self.event.participants]:
                await interaction.followup.send(content="You are already not part of this event.",
                                                ephemeral=True)
                return
            participant = self.event.get_participant(interaction.user.name)
            if participant.subscribed:
                logger.info(f'[{self.event}] {interaction.user.name} unsubscribed')
                participant.subscribed = False
                await interaction.followup.send(f"You have been unsubscribed from {self.event}.",
                                                ephemeral=True)
            else:
                logger.info(f'[{self.event}] {interaction.user.name} resubscribed')
                participant.subscribed = True
                await interaction.followup.send(f"You have been resubscribed to {self.event}.",
                                                ephemeral=True)
        self.unsubscribe_button.callback = unsubscribe_button_callback
        self.add_item(self.unsubscribe_button)

    def add_reschedule_button(self) -> None:
        """
        Sets up the Reschedule button.

        Returns
        --------
        button: :class:`Button`
            The Reschedule button.
        """
        async def reschedule_button_callback(interaction: Interaction):
            await interaction.response.defer(ephemeral=True)
            logger.info(f'[{self.event}] {interaction.user} rescheduled by button press')
            for participant in self.event.participants:
                participant.confirm_answered(duration=self.event.duration,
                                             latest_date=self.event.latest_date)
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
        --------
        button: :class:`Button`
            The Cancel button.
        """
        async def cancel_button_callback(interaction: Interaction):
            if interaction.user.id not in [participant.member.id for participant in self.event.participants]:
                await interaction.response.send_message(content="You are not a participant of this event!",
                                                        ephemeral=True)
                return
            title = f"Cancel {self.event}"
            if len(title) >= 38:
                title = f"{title[:34]}..."
            await interaction.response.send_modal(CancelModal(event=self.event,
                                                              title=title))
        self.cancel_button.callback = cancel_button_callback
        self.add_item(self.cancel_button)


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
                              voice_channel=selected_guild_event.channel,
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
                    if event.has_image_saved:
                        guild_event.edit(image=event.get_image())
                    event.scheduled_events.append(guild_event)
            await event.update_event_buttons_message()
            await interaction.followup.send('Success!', ephemeral=True)
            save()
        else:
            await interaction.response.send_message('Error getting guild scheduled event.')
            logger.exception(f'Error getting guild scheduled event selected by {interaction.user.name}')


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
        options = [
            SelectOption(label=event_avail.event.name, value=event_avail.event.name)
            for event_avail in self.event_avails
        ]
        super().__init__(placeholder="Event Availabilities", options=options)

    # Select an availability to attach
    async def callback(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        logger.info(f'{interaction.user.name} reused availability from {self.values[0]}')
        for event_avail in self.event_avails:
            if event_avail.event.name == self.values[0]:
                self.participant.availability = event_avail.avail.copy()
                self.participant.full_availability_flag = event_avail.full_flag
                self.participant.answered = True
                self.participant.subscribed = True
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
    ----------
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
    --------
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
                     multi_event: Optional[bool] = None) -> None:
    embed = Embed(title=f"{event} Edited",
                  description=f"{event} has been edited.",
                  color=Color.orange())
    # Name
    if name is not None:
        old_name = event.name
        event.name = name
        if old_name == event.name:
            embed.add_field(name="Name",
                            value="Unchanged",
                            inline=False)
        else:
            embed.add_field(name="Name",
                            value=f"{old_name} -> {event.name}",
                            inline=False)
    # Voice Channel
    if voice_channel is not None:
        old_vc = event.voice_channel
        event.voice_channel = voice_channel
        if old_vc == event.voice_channel:
            embed.add_field(name="Voice Channel",
                            value="Unchanged",
                            inline=False)
        else:
            embed.add_field(name="Voice Channel",
                            value=f"{old_vc.mention} -> {event.voice_channel.mention}",
                            inline=False)
    # Image URL
    if image_url is not None:
        old_image_url = event.image_url
        event.delete_image_file()
        event.image_url = image_url
        await event.save_image_to_file()
        if event.image_url:
            if old_image_url == event.image_url:
                embed.add_field(name="Image",
                                value="Unchanged",
                                inline=False)
            else:
                embed.add_field(name="Image",
                                value=f"{old_image_url} -> {event.image_url}",
                                inline=False)
        else:
            event.image_url = old_image_url
            embed.add_field(name="ERROR: Image",
                            value="The new image could not be downloaded.\nThe old one was kept.",
                            inline=False)
    # Duration
    if duration is not None:
        old_duration = event.duration
        event.duration = timedelta(minutes=duration)
        if old_duration.total_seconds() == event.duration.total_seconds():
            embed.add_field(name="Duration",
                            value="Unchanged",
                            inline=False)
        else:
            embed.add_field(name="Duration",
                            value=f"{get_time_str_from_minutes(old_duration.total_seconds() // 60)}"
                            f" -> {get_time_str_from_minutes(event.duration.total_seconds() // 60)}",
                            inline=False)
    # Multi event
    if multi_event is not None:
        old_multi_event = event.multi_event
        event.multi_event = multi_event
        if old_multi_event == event.multi_event:
            embed.add_field(name="Multi Event",
                            value="Unchanged",
                            inline=False)
        else:
            embed.add_field(name="Multi Event",
                            value=f"{old_multi_event} -> {event.multi_event}",
                            inline=False)
    if event.image_url:
        embed.set_thumbnail(url=event.image_url)
    await event.update_messages()
    save()
    return embed


@client.event
async def on_ready():
    logger.info('Connected to Discord')
    await client.retrieve_events()
    if not client.server_is_running:
        await client.start_server()
    if not update.is_running():
        update.start()
    logger.info('Ready!')


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
            eventStatus = event.scheduling_status
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
    await interaction.response.defer()
    logger.info(f'[{event_name}] Received event creation request from {interaction.user.name}')
    if not interaction.guild.voice_channels:
        raise Exception('The server must have at least one voice channel to schedule an event.')

    if event_name in [event.name for event in client.events]:
        await interaction.followup.send(f'Sorry, I already have an event called "{event_name}". Please choose a different name.')
        return

    # Parse start time
    try:
        start_time_obj = datetime.fromisoformat(start_time)
    except Exception as e:
        logger.info(f"[{event_name}] Start time was not in iso format: {e}")
        start_time = start_time.strip()
        start_time = start_time.replace(':', '')
        if len(start_time) == 1 or len(start_time) == 2:
            start_time = start_time + '00'
        if len(start_time) == 3:
            start_time = '0' + start_time
        elif len(start_time) != 4:
            await interaction.followup.send('Invalid start time format. Examples: "1630" or "00:30"')
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
    await event.save_image_to_file()
    await event.make_scheduled_events()
    client.events.append(event)
    remove_times_from_availabilities_for_events()
    await event.create_event_buttons_message(interaction=interaction)
    other_events = get_events_that_share_participants(event)
    for other_event in other_events:
        await other_event.update_messages()
    save()


@client.tree.command(name='schedule', description='Schedule an event.')
@app_commands.describe(event_name='Name for the event.')
@app_commands.describe(voice_channel='Voice channel for the event.')
@app_commands.describe(image_url="URL to an image for the event.")
@app_commands.describe(include_exclude='Whether to include or exclude users specified.')
@app_commands.describe(usernames='Comma separated usernames of users to include/exclude.')
@app_commands.describe(roles='Comma separated roles of users to include/exclude.')
@app_commands.describe(duration=f"Event duration in minutes ({DEFAULT_EVENT_DURATION} minutes default).")
@app_commands.describe(multi_event='Create an event on each date that everyone is available.')
async def schedule_command(interaction: Interaction,
                           event_name: str,
                           voice_channel: VoiceChannel,
                           image_url: Optional[str] = None,
                           include_exclude: Optional[INCLUDE_EXCLUDE] = INCLUDE,
                           usernames: Optional[str] = None,
                           roles: Optional[str] = None,
                           duration: Optional[int] = DEFAULT_EVENT_DURATION,
                           multi_event: Optional[bool] = False):
    await interaction.response.defer()
    logger.info(f'[{event_name}] Received event schedule request from {interaction.user.name}')
    try:
        event = await schedule(eventName=event_name,
                               guild=interaction.guild,
                               textChannel=interaction.channel,
                               voiceChannel=voice_channel,
                               schedulerId=interaction.user.id,
                               imageUrl=image_url,
                               includeExclude=include_exclude,
                               usernames=usernames,
                               roles=roles,
                               duration=duration,
                               multiEvent=multi_event,
                               sendAvailabilityMessage=False)
        await event.create_availability_message(interaction)
    except Exception as e:
        content = f"[{event_name}] Failed to schedule event: {e}"
        logger.error(content)
        await interaction.followup.send(content=content)
    save()


async def schedule(eventName: str,
                   guild: Guild,
                   textChannel: TextChannel,
                   voiceChannel: VoiceChannel,
                   schedulerId: Optional[int] = 0,
                   imageUrl: Optional[str] = None,
                   includeExclude: Optional[INCLUDE_EXCLUDE] = INCLUDE,
                   usernames: Optional[str] = None,
                   roles: Optional[str] = None,
                   duration: Optional[int] = DEFAULT_EVENT_DURATION,
                   multiEvent: Optional[bool] = False,
                   sendAvailabilityMessage: Optional[bool] = True):
    """
    Starts the scheduling of an event.

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
    sendAvailabilityMessage: :class:`bool`
        Optional. Whether or not to send an availability message in this function.
        Default: True

    Returns
    --------
    event: :class:`Event`
        The event object created for scheduling.

    Exceptions
    -----------
    Exception: :class:`Exception`
        A string message describing the error.
    """
    if not guild.voice_channels:
        logger.info(f"[{eventName}] Scheduling cancelled due to no voice channel in guild")
        raise Exception("The server must have at least one voice channel to schedule an event.")

    schedulerUser = None
    if schedulerId != 0:
        schedulerUser = guild.get_member(schedulerId)
    if schedulerUser is None:
        schedulerUser = guild.members[0]

    if eventName in [event.name for event in client.events]:
        logger.info(f"[{eventName}] Scheduling cancelled due to existing name")
        raise Exception(f"Sorry, I already have an event called {eventName}. Please choose a different name.")

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
        raise Exception(f"Failed to generate participants list: {e}")

    scheduler = None
    for participant in participants:
        if participant.member.id == schedulerId:
            scheduler = participant

    if imageUrl == "":
        imageUrl = None

    # Make event object
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
    try:
        await event.save_image_to_file()
    except Exception as e:
        logger.error(f'[{eventName}] Error saving image: {e}')
        raise Exception(f"Failed to save image: {e}")

    if sendAvailabilityMessage:
        await event.update_availability_message()
    return event


@client.tree.command(name='edit', description='Edit an existing event.')
@app_commands.describe(name='Name for the event.')
@app_commands.describe(voice_channel='Voice channel for the event.')
@app_commands.describe(image_url="URL to an image for the event.")
@app_commands.describe(duration=f"Event duration in minutes ({DEFAULT_EVENT_DURATION} minutes default).")
@app_commands.describe(multi_event='Create an event on each date that everyone is available.')
async def edit_command(interaction: Interaction,
                       name: Optional[str] = None,
                       voice_channel: Optional[VoiceChannel] = None,
                       image_url: Optional[str] = None,
                       duration: Optional[int] = None,
                       multi_event: Optional[bool] = None):
    await interaction.response.defer(ephemeral=True, thinking=True)
    events = []
    for event in client.events:
        if event.text_channel == interaction.channel:
            events.append(event)
    # No events found in this guild
    if len(events) == 0:
        await interaction.followup.send("**No events were found in this guild.**", ephemeral=True)
    # Only one event in this guild, edit it
    elif len(events) == 1:
        event = events[0]
        embed = await edit_event(event=event,
                                 name=name,
                                 voice_channel=voice_channel,
                                 image_url=image_url,
                                 duration=duration,
                                 multi_event=multi_event)
        if interaction.user.avatar:
            embed.set_footer(text=f"Edited by {interaction.user}", icon_url=interaction.user.avatar.url)
        else:
            embed.set_footer(text=f"Edited by {interaction.user}")
        remove_times_from_availabilities_for_events()
        for event in client.events:
            await event.update_messages()
        await interaction.followup.send(embed=embed)
    # Multiple events in guild, select one to edit from a dropdown
    else:
        options = [SelectOption(label=event.name, value=event.name) for event in events]
        select = Select(placeholder="Select an event to edit", options=options)

        async def select_callback(interaction: Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            for event in events:
                if event.name == select.values[0]:
                    embed = await edit_event(event=event,
                                             name=name,
                                             voice_channel=voice_channel,
                                             image_url=image_url,
                                             duration=duration,
                                             multi_event=multi_event)
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
            save()
            logger.info(f'[{event.name}] attached to event')
        remove_times_from_availabilities_for_events()
        for event in client.events:
            await event.update_messages()
        await interaction.followup.send(content='Success!',
                                        ephemeral=True)
    else:
        await interaction.followup.send(content='Select an existing guild event from the dropdown menu.',
                                        view=ExistingGuildEventsSelectView(interaction.guild),
                                        ephemeral=True)


@client.tree.command(name='listevents', description='List all events in this server.')
async def listevents_command(interaction: Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    logger.info(f"Received list events command request from {interaction.user.name}")
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


@client.tree.command(name='availability', description='Show availabilities of an event.')
async def availability_command(interaction: Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    logger.info(f"{interaction.user.name} used availability command")
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
        options = [SelectOption(label=event.name, value=event.name) for event in client.events]
        select = Select(placeholder="Select an event", options=options)

        async def select_callback(interaction: Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            for event in events:
                if event.name == select.values[0]:
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
    logger.info(f"{interaction.user.name} used help command")
    await interaction.response.send_message(embeds=HELP_EMBEDS, ephemeral=True)


def first_start_time(event):
    """
    Gets the first start time of the event.

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


def remove_times_from_availabilities_for_events() -> None:
    """
    Removes and saves timeblocks from uncreated events for created events.
    Also cleans removed availabilities of forgotten events.
    """
    # Remove blocks of time from participant availability for events
    for event in client.events:
        if not event.created:
            continue
        for other_event in client.events:
            if other_event == event or other_event.created:
                continue
            for shared_participant in event.other_shared_participants(other_event):
                shared_participant.remove_availability_for_event(event_name=event.name,
                                                                 event_start_times=event.start_times,
                                                                 event_duration=event.duration)


@tasks.loop(seconds=UPDATE_INTERVAL)
async def update():
    await client.change_presence(activity=Activity(type=ActivityType.watching, name="for event scheduling commands"))
    for event in client.events:
        await event.update()
    save()


client.run(DISCORD_TOKEN)
