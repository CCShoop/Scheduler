import re
from discord import Guild, Member
from asyncio import Lock
from datetime import date, datetime, timedelta
from calendar import monthrange
from typing import Optional


DEFAULT_HOURS_PAST_MIDNIGHT_CUTOFF = 0
# Negative cutoffs end Full Availability before midnight, e.g. -2 is 10 PM
MIN_HOURS_PAST_MIDNIGHT_CUTOFF = -6
MAX_HOURS_PAST_MIDNIGHT_CUTOFF = 23
# Hours past midnight that each user's Full Availability extends to, set with /cutoff.
# Users without an entry use DEFAULT_HOURS_PAST_MIDNIGHT_CUTOFF.
user_cutoffs: dict[int, int] = {}


def get_cutoff(user_id: int) -> int:
    """Gets how many hours past midnight the user's Full Availability extends to."""
    return user_cutoffs.get(user_id, DEFAULT_HOURS_PAST_MIDNIGHT_CUTOFF)


def format_cutoff(hours: int) -> str:
    """Gets the time of day a cutoff ends at, e.g. "midnight", "2 AM", or "10 PM"."""
    hour = hours % 24
    if hour == 0:
        return "midnight"
    if hour == 12:
        return "noon"
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"


def parse_time_string(time_string: str, label: str) -> str:
    """
    Converts a user-entered time (e.g. "9", "930", "1:12pm", "12:30am") to a 4-digit 24-hour "HHMM" string.
    Returns an empty string if no time was entered.
    """
    is_pm = 'pm' in time_string
    is_am = 'am' in time_string
    digits = re.sub(r"\D", "", time_string)
    if digits == '':
        return ''
    if len(digits) <= 2:
        hour, minute = int(digits), 0
    elif len(digits) <= 4:
        hour, minute = int(digits[:-2]), int(digits[-2:])
    else:
        raise Exception(f'Invalid {label} time provided by user: {time_string}')
    if is_am or is_pm:
        if hour < 1 or hour > 12:
            raise Exception(f'Invalid {label} time provided by user: {time_string}')
        hour = hour % 12 + (12 if is_pm else 0)
    if hour > 23 or minute > 59:
        raise Exception(f'Invalid {label} time provided by user: {time_string}')
    return f"{hour:02d}{minute:02d}"


def to_local(time: datetime) -> datetime:
    """
    Gives a local wall-clock time the UTC offset in effect on its own date.
    datetime.now().astimezone() has a fixed offset, so replacing its date or adding days to it
    keeps today's offset, which is an hour off past a DST change.
    """
    return time.replace(tzinfo=None).astimezone()


def add_local_days(time: datetime, days: int) -> datetime:
    """Moves a time by whole days on the local wall clock, keeping its time of day across DST changes."""
    return to_local(time.astimezone() + timedelta(days=days))


def print_time_until(time: datetime) -> str:
    return f"<t:{int(time.timestamp())}:R>"


def print_date_time(time: datetime) -> str:
    return f"<t:{int(time.timestamp())}:F>"


def print_date(time: datetime) -> str:
    return f"<t:{int(time.timestamp())}:d>"


def print_time(time: datetime) -> str:
    return f"<t:{int(time.timestamp())}:t>"


class TimeBlock():
    """
    Represents a block of time.

    Attributes
    -----------
    start_time: :class:`datetime`
        The start time of the timeblock.
    end_time: :class:`datetime`
        The end time of the timeblock.
    duration: :class:`timedelta`
        The duration of the timeblock.
    """

    def __init__(self, start_time: datetime, end_time: datetime) -> None:
        self.start_time: datetime = start_time
        self.end_time: datetime = end_time

    @property
    def duration(self) -> timedelta:
        return self.end_time - self.start_time

    @property
    def string(self) -> str:
        return f"[Free] {self}"

    @property
    def log_string(self) -> str:
        return f"{self.start_time.strftime('%Y-%m-%d %H:%M:%S')} - {self.end_time.strftime('%Y-%m-%d %H:%M:%S')}"

    def subtract(self, timeblock) -> list:
        """Subtracts another timeblock and returns the remaining block(s)."""
        # No overlap
        if self.end_time <= timeblock.start_time or self.start_time >= timeblock.end_time:
            return [self]

        timeblocks = []
        if self.start_time < timeblock.start_time:
            timeblocks.append(TimeBlock(self.start_time, timeblock.start_time))
        if self.end_time > timeblock.end_time:
            timeblocks.append(TimeBlock(timeblock.end_time, self.end_time))
        return timeblocks

    def overlaps_with(self, timeblock) -> bool:
        """Returns True if the provided timeblock overlaps with this timeblock, False if it does not overlap."""
        if timeblock.end_time <= self.start_time or self.end_time <= timeblock.start_time:
            return False
        return True

    @classmethod
    def from_dict(cls, data: dict):
        """
        Creates a :class:`TimeBlock` from a data dict.

        Arguments
        ----------
        data: :class:`dict`
            The data to create the timeblock from.

        Returns
        --------
        cls: :class:`TimeBlock`
            The created timeblock object.
        """
        if data is None:
            return None
        return cls(
            start_time=datetime.fromisoformat(data["start_time"]),
            end_time=datetime.fromisoformat(data["end_time"])
        )

    def to_dict(self) -> dict:
        """
        Stores the timeblock as a dict.

        Returns
        --------
        data: :class:`dict`
            The timeblock dict.
        """
        return {
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat()
        }

    def __repr__(self):
        return f'{print_date_time(self.start_time)} - {print_time(self.end_time)}'


class RemovedTime:
    """
    Represents a combination of event name and timeblock
    for time removed from a participant's availability for an event.

    Attributes
    -----------
    event_name: :class:`str`
        The name of the event being accounted for.
    event_timeblock: :class:`TimeBlock`
        The timeblock representing the event.
    removed_timeblocks: :class:`list[TimeBlock]`
        The timeblocks of removed availability, one for each availability timeblock the event overlapped.
    """

    def __init__(self, event_name: str,
                 event_timeblock: TimeBlock,
                 removed_timeblocks: Optional[list[TimeBlock]] = None):
        self.event_name = event_name
        self.event_timeblock = event_timeblock
        self.removed_timeblocks = removed_timeblocks or []

    @property
    def start_time(self) -> datetime:
        return self.event_timeblock.start_time

    @property
    def end_time(self) -> datetime:
        return self.event_timeblock.end_time

    @classmethod
    def from_dict(cls, data: dict):
        if 'removed_timeblocks' in data:
            removed_timeblocks = [TimeBlock.from_dict(timeblock) for timeblock in data['removed_timeblocks']]
        else:
            # Saved before an event could remove more than one timeblock
            removed_timeblock = TimeBlock.from_dict(data.get('removed_timeblock'))
            removed_timeblocks = [removed_timeblock] if removed_timeblock is not None else []
        return cls(
            event_name=data['event_name'],
            event_timeblock=TimeBlock.from_dict(data['timeblock']),
            removed_timeblocks=removed_timeblocks
        )

    def to_dict(self) -> dict:
        return {
            'event_name': self.event_name,
            'timeblock': self.event_timeblock.to_dict(),
            'removed_timeblocks': [timeblock.to_dict() for timeblock in self.removed_timeblocks]
        }

    def __repr__(self) -> str:
        return f"[Busy] [{self.event_name[:20]}] {self.event_timeblock}"


class Participant:
    """
    Represents the participant of an event.

    Attributes
    -----------
    member: :class:`Member`
        The participant's Discord member object.
    availability: :class:`list`
        The participant's availability, a list of timeblocks.
    subscribed: :class:`bool`
        Whether or not the participant is subscribed to the event.
    unavailable: :class:`bool`
        Whether or not the participant is unavailable for the event.
    removed_times: :class:`list`
        The list of removed times for other events.
    full_availability_flag: :class:`bool`
        The full availability flag for the participant.
    blocked_until: :class:`Optional[datetime]`
        When the member's block from interacting with the event ends, None if they aren't blocked.
    """

    def __init__(self,
                 member: Member,
                 availability: list = None,
                 answered: bool = False,
                 subscribed: bool = True,
                 unavailable: bool = False,
                 removed_times: list = None,
                 full_availability_flag: bool = False,
                 note: str = "",
                 blocked_until: Optional[datetime] = None) -> None:
        self.member = member
        self.availability = availability or []
        self.answered = answered
        self.subscribed = subscribed
        self.unavailable = unavailable
        self.removed_times = removed_times or []
        self.full_availability_flag = full_availability_flag
        self.note = note
        self.blocked_until = blocked_until
        self.msg_lock = Lock()

    def is_available_at(self, time: datetime, duration: timedelta) -> bool:
        """
        Indicates whether or not the participant is available at a certain time with the provided duration.

        Arguments
        ----------
        time: :class:`datetime`
            The time to check for the participant's avilability.
        duration: :class:`timedelta`
            The duration for which to check the participant's availability.

        Returns
        --------
        True
            If the participant is available at the given time for the given duration.
        False
            If the participant is not available at that time for that duration.
        """
        for timeblock in self.availability:
            if (timeblock.start_time <= time) and ((time + duration) <= timeblock.end_time):
                return True
        return False

    def get_full_availability_end(self, date: datetime = None) -> datetime:
        """
        Gets when Full Availability on the date ends, at the user's cutoff past that day's midnight.

        Arguments
        ----------
        date: :class:`datetime`
            Optional. The date of the Full Availability.
            Default: Today
        """
        day = date or datetime.now().astimezone()
        midnight = datetime(day.year, day.month, day.day)
        return to_local(midnight + timedelta(days=1, hours=get_cutoff(self.member.id)))

    def set_full_availability(self, date: datetime = None, end_time: datetime = None) -> None:
        """
        Sets the participant to have full availability.

        Arguments
        ----------
        date: :class:`datetime`
            Optional. Current entered date.
            Default: None
        end_time: class:`datetime`
            Optional. Current end time.
            Default: None
        """
        try:
            cur_time = datetime.now().astimezone().replace(second=0, microsecond=0)
            month = cur_time.month if not date else date.month
            day = cur_time.day if not date else date.day
            year = cur_time.year if not date else date.year
            start_time = cur_time.replace(day=day,
                                          month=month,
                                          year=year)
            # Full availability on a future day starts at midnight
            if start_time.date() != cur_time.date():
                start_time = to_local(datetime(year, month, day))
            if not end_time:
                # Midnight at the end of the day, shifted by the cutoff
                end_time = self.get_full_availability_end(start_time)
                if end_time <= start_time:
                    cutoff = get_cutoff(self.member.id)
                    raise Exception(f"Your Full Availability ends at {format_cutoff(cutoff)} (set with /cutoff), which has already passed.")
            self.add_to_availability(TimeBlock(start_time, end_time))
            self.answered = True
            self.full_availability_flag = True
            self.clean_availability()
        except Exception as e:
            raise e

    def remove_availability_ending_before(self, time: datetime) -> list[TimeBlock]:
        """
        Removes the participant's availability that ends before the time.

        Returns
        -------
        removed: :class:`list[TimeBlock]`
            The removed timeblocks.
        """
        removed = [timeblock for timeblock in self.availability if timeblock.end_time < time]
        self.availability = [timeblock for timeblock in self.availability if timeblock.end_time >= time]
        return removed

    def set_no_availability(self, date: date = None) -> None:
        """
        Sets the participant to have no availability.

        Arguments
        ----------
        date: :class:`date`
            Optional. Current entered date.
            Default: None
        """
        self.full_availability_flag = False
        if date is None:
            self.availability.clear()
        else:
            self.availability = [tb for tb in self.availability if tb.start_time.date() != date]

    def set_specific_availability(self, avail_string: str, date_string: str) -> None:
        """
        Sets a specific availability for the user with string parsing.

        Arguments
        ----------
        avail_string: :class:`str`
            The combined string from the Discord TextInputs.
        date_string: :class:`str`
            The date that the availability is for.
        """
        if avail_string == '':
            return
        avail_string = avail_string.lower()

        # Date parsing
        cur_date = datetime.now().astimezone().date()
        year_given = True
        date_string = date_string.replace('/', '-')
        date_parts = date_string.split('-')
        # Catch the old MM/DD/YYYY format so users aren't told their year is an invalid day
        if len(date_parts) == 3 and len(date_parts[2]) == 4 and len(date_parts[0]) != 4:
            raise Exception(f'Dates must be in YYYY-MM-DD format (e.g. {cur_date:%Y-%m-%d}), got: {date_string}')
        try:
            year, month, day = date_parts
        except Exception:
            try:
                month, day = date_string.split('-')
                year = cur_date.year
                year_given = False
            except Exception:
                try:
                    day = int(date_string)
                    month = cur_date.month
                    year = cur_date.year
                    year_given = False
                except Exception:
                    raise Exception(f'Invalid date format provided by user: {date_string}')
        try:
            month = int(month)
        except Exception:
            raise Exception(f'Invalid month: {month}')
        try:
            day = int(day)
        except Exception:
            raise Exception(f'Invalid day: {day}')
        try:
            year = int(year)
        except Exception:
            raise Exception(f'Invalid year: {year}')

        # Convert YY to YYYY
        if year < 100:
            year += cur_date.year - (cur_date.year % 100)
        # Date validity check
        if month < 1 or month > 12:
            raise Exception(f'Invalid month provided by user: {month}')
        if day < 1 or day > monthrange(year, month)[1]:
            raise Exception(f'Invalid day provided by user: {day}')
        if date(year, month, day) < cur_date:
            # A past date without a year refers to next year
            if year_given or day > monthrange(year + 1, month)[1]:
                raise Exception(f'Cannot set availability for a past date: {year:04}-{month:02}-{day:02}')
            year += 1

        # Check if the entered date is today
        entered_date = date(year, month, day)
        date_is_today = entered_date == cur_date

        # Keyword shortcuts
        if 'full' in avail_string:
            self.set_full_availability(entered_date)
            return
        if 'clear' in avail_string:
            extend = 1
            if 'x' in avail_string:
                # The timezone follows the extension, e.g. "clear x3 et"
                match = re.search(r"x\s*(\d+)", avail_string)
                if match is None:
                    raise Exception(f"Invalid extension provided by user: {avail_string}")
                extend = int(match.group(1))
            extend_date = entered_date
            while extend > 0:
                self.set_no_availability(extend_date)
                extend_date += timedelta(days=1)
                extend -= 1
            return
        if 'none' in avail_string:
            self.set_no_availability()
            return

        # Timezone parsing
        timezone_offset = 0
        avail_string = avail_string.replace('s', '')
        avail_string = avail_string.replace('d', '')
        if 'at' in avail_string:
            timezone_offset += -1
            avail_string = avail_string.replace('at', '')
        elif 'et' in avail_string:
            timezone_offset += 0
            avail_string = avail_string.replace('et', '')
        elif 'ct' in avail_string:
            timezone_offset += 1
            avail_string = avail_string.replace('ct', '')
        elif 'mt' in avail_string:
            timezone_offset += 2
            avail_string = avail_string.replace('mt', '')
        elif 'pt' in avail_string:
            timezone_offset += 3
            avail_string = avail_string.replace('pt', '')

        avail_string = avail_string.replace('.', '')

        # Make timeblock string list
        timeblock_strings = avail_string.split(',')

        # Parse each timeblock
        for timeblock in timeblock_strings.copy():
            # Stripping
            timeblock = timeblock.replace(' ', '')
            if timeblock == '':
                continue
            timeblock = timeblock.replace(':', '')
            timeblock = timeblock.replace(';', '')
            if '--' in timeblock:
                raise Exception("Invalid time provided by user: cannot double hyphen (--)")
            timeblock = timeblock.replace('X', 'x')
            # Handle extensions, e.g. 22-x2, where the day specified plus
            # the next day will have timeblocks of 2200-0000 applied.
            extend = 1
            if 'x' in timeblock:
                timeblock, part, extend = timeblock.partition('x')
                try:
                    extend = int(extend)
                except Exception as e:
                    raise Exception(f"Invalid extension provided by user: {e}")
            start_time, part, end_time = timeblock.partition('-')

            # Start/end time keywords
            # The current time is already in the bot's timezone, so the entered timezone doesn't shift it
            start_is_now = 'now' in start_time or 'cur' in start_time or 'curr' in start_time or 'current' in start_time
            if start_is_now:
                start_time = datetime.now().astimezone().replace(second=0, microsecond=0).strftime("%H%M")
            if 'now' in end_time or 'cur' in end_time or 'curr' in end_time or 'current' in end_time:
                raise Exception("Invalid end time provided by user: cannot use current time as end time")

            start_time = parse_time_string(start_time, 'start')
            end_time = parse_time_string(end_time, 'end')

            # Convert to datetime objects, as naive local wall-clock times until they're stored
            # so that adding days past a DST change keeps the entered time of day
            start_time_string = start_time
            end_time_string = end_time
            # Start time is now if today, midnight if not today
            if start_time_string == '':
                if date_is_today:
                    start_time = datetime.now().replace(second=0, microsecond=0)
                else:
                    start_time = datetime(year, month, day)
            # Start time is defined
            else:
                start_hr = int(start_time_string[:2])
                start_min = int(start_time_string[2:])
                start_time = datetime(year, month, day, start_hr, start_min)
                if not start_is_now:
                    start_time += timedelta(hours=timezone_offset)
            # End time is midnight
            if end_time_string == '':
                end_time = datetime(year, month, day) + timedelta(days=1)
            # End time is defined
            else:
                end_hr = int(end_time_string[:2])
                end_min = int(end_time_string[2:])
                end_time = datetime(year, month, day, end_hr, end_min)
                end_time += timedelta(hours=timezone_offset)
                while end_time < start_time:
                    end_time += timedelta(days=1)

            # Currency check
            while to_local(end_time) < datetime.now().astimezone():
                start_time += timedelta(days=1)
                end_time += timedelta(days=1)

            self.add_to_availability(TimeBlock(to_local(start_time), to_local(end_time)))
            while extend > 1:
                start_time += timedelta(days=1)
                end_time += timedelta(days=1)
                self.add_to_availability(TimeBlock(to_local(start_time), to_local(end_time)))
                extend -= 1

    def clean_availability(self) -> None:
        """Cleans the participant's availability by sorting and then combining overlapping/touching timeblocks."""
        # Sort the availability by start time (and by end time if start times are the same)
        self.availability.sort(key=lambda x: (x.start_time, x.end_time))

        merged_availability = []
        for timeblock in self.availability:
            if not merged_availability:
                merged_availability.append(timeblock)
            else:
                last = merged_availability[-1]
                # overlapping or touching timeblocks
                if timeblock.start_time <= last.end_time:
                    last.end_time = max(last.end_time, timeblock.end_time)
                else:
                    merged_availability.append(timeblock)
        self.availability = merged_availability
        if self.availability:
            self.answered = True
        self.update_removed_times()

    def get_availability_overlaps(self, event_timeblock: TimeBlock) -> list[TimeBlock]:
        """
        Gets the overlap between an event and each of the participant's availability timeblocks.
        Returns an empty list if there is no overlap.

        Arguments
        ---------
        event_timeblock: :class:`TimeBlock`
            The timeblock with which to get its overlap with availability.
        """
        overlaps = []
        for timeblock in self.availability:
            # Timeblock ends before or when event starts
            if timeblock.end_time <= event_timeblock.start_time:
                continue
            # Event ends before or when timeblock starts
            if event_timeblock.end_time <= timeblock.start_time:
                break
            # Overlap
            overlaps.append(TimeBlock(start_time=max(event_timeblock.start_time, timeblock.start_time),
                                      end_time=min(event_timeblock.end_time, timeblock.end_time)))
        return overlaps

    def add_to_availability(self, timeblock: TimeBlock) -> None:
        if timeblock is not None:
            self.availability.append(timeblock)
            self.clean_availability()

    def remove_from_availability(self, timeblock: TimeBlock) -> None:
        """Removes a timeblock from availability."""
        new_availability = []
        for tb in self.availability:
            if tb.overlaps_with(timeblock):
                if tb.start_time < timeblock.start_time:
                    new_availability.append(TimeBlock(start_time=tb.start_time,
                                                      end_time=timeblock.start_time))
                if timeblock.end_time < tb.end_time:
                    new_availability.append(TimeBlock(start_time=timeblock.end_time,
                                                      end_time=tb.end_time))
            else:
                new_availability.append(tb)
        self.availability = new_availability

    def remove_availability_for_event(self, event_name: str, event_timeblocks: list[TimeBlock]) -> None:
        """
        Removes overlap with event timeblocks from availability and saves them as RemovedTime objects.

        Arguments
        ---------
        event_name: :class:`str`
            The name of the event the timeblocks are for.
        event_timeblocks: :class:`list[TimeBlock]`
            The event's timeblocks.
        """
        self.restore_availability_for_event(event_name=event_name)
        for event_timeblock in event_timeblocks:
            # Saved even without an overlap, so the event shows as busy and
            # update_removed_times() removes availability entered later
            removed_timeblocks = self.get_availability_overlaps(event_timeblock)
            for removed_timeblock in removed_timeblocks:
                self.remove_from_availability(removed_timeblock)
            removed_time = RemovedTime(event_name=event_name,
                                       event_timeblock=event_timeblock,
                                       removed_timeblocks=removed_timeblocks)
            self.removed_times.append(removed_time)

    def restore_availability_for_event(self, event_name: str) -> None:
        """
        Restores availability for a cancelled or rescheduled event.

        Arguments
        ---------
        event_name: :class:`str`
            The name of the event to restore availability for.
        """
        restored_timeblocks = []
        new_removed_times = []
        for removed_time in self.removed_times:
            if removed_time.event_name == event_name:
                restored_timeblocks.extend(removed_time.removed_timeblocks)
            else:
                new_removed_times.append(removed_time)
        # Drop the event's removed times before restoring so that
        # clean_availability() doesn't remove the restored blocks again
        self.removed_times = new_removed_times
        for timeblock in restored_timeblocks:
            self.add_to_availability(timeblock)
        self.update_removed_times()

    def confirm_answered(self, duration: timedelta = timedelta(minutes=30)) -> None:
        """
        Confirms that the participant's availability is valid.

        Arguments
        ----------
        duration: :class:`timedelta`
            Optional. Duration of the event in minutes.
            Default: 30 minutes
        """
        self.update_removed_times()
        cur_time = datetime.now().astimezone().replace(second=0, microsecond=0)
        if self.availability:
            new_availability = []
            for tb in self.availability:
                if cur_time + duration <= tb.end_time:
                    tb.start_time = max(tb.start_time, cur_time)
                    new_availability.append(tb)
            self.availability = new_availability
        if not self.availability:
            self.answered = False
            self.full_availability_flag = False

    def update_removed_times(self) -> None:
        """Removes removed times that are entirely in the past."""
        cur_time = datetime.now().astimezone().replace(second=0, microsecond=0)
        new_removed_times = []
        for removed_time in self.removed_times:
            if cur_time < removed_time.event_timeblock.end_time:
                new_removed_times.append(removed_time)
                # Availability entered or reused since the event was saved may overlap it again
                saved = {(tb.start_time, tb.end_time) for tb in removed_time.removed_timeblocks}
                for removed_timeblock in self.get_availability_overlaps(removed_time.event_timeblock):
                    self.remove_from_availability(removed_timeblock)
                    if (removed_timeblock.start_time, removed_timeblock.end_time) not in saved:
                        removed_time.removed_timeblocks.append(removed_timeblock)
        self.removed_times = new_removed_times

    @property
    def name(self) -> str:
        return self.member.name

    @property
    def nick(self) -> str:
        return self.__repr__()

    @property
    def id(self) -> int:
        return self.member.id

    @property
    def availability_string(self) -> str:
        """
        Gets the availability string of the participant.

        Returns
        --------
        response: :class:`str`
            The participant's availability string
        """
        self.removed_times.sort(key=lambda rt: rt.start_time)
        removed_index = 0
        response = ''
        if self.note != "":
            response += f"[Note] \"{self.note}\"\n"
        if self.full_availability_flag:
            response += "[Full Availability]\n"
        if self.availability:
            # Print availability and removed times within availability
            for timeblock in self.availability:
                while removed_index < len(self.removed_times) and \
                        self.removed_times[removed_index].event_timeblock.start_time < timeblock.start_time:
                    response += f"{self.removed_times[removed_index]}\n"
                    removed_index += 1
                response += f"{timeblock.string}\n"
            # Print removed times after end of availability
            while removed_index < len(self.removed_times):
                response += f"{self.removed_times[removed_index]}\n"
                removed_index += 1
        else:
            # Print removed times
            for removed_time in self.removed_times:
                response += f"{removed_time}\n"
        return response

    @classmethod
    def from_dict(cls, guild: Guild, data: dict, member: Member = None):
        return cls(
            member=member or guild.get_member(data['member_id']),
            answered=data['answered'],
            subscribed=data['subscribed'],
            unavailable=data['unavailable'],
            removed_times=[RemovedTime.from_dict(removed_time) for removed_time in data['removed_time']],
            full_availability_flag=data['full_availability_flag'],
            note=data['note'],
            availability=[TimeBlock.from_dict(timeblock_data) for timeblock_data in data['availability']],
            blocked_until=datetime.fromisoformat(data['blocked_until']) if data.get('blocked_until') else None
        )

    def to_dict(self) -> dict:
        return {
            'member_id': self.member.id,
            'member_name': self.member.name,
            'answered': self.answered,
            'subscribed': self.subscribed,
            'unavailable': self.unavailable,
            'removed_time': [removed_time.to_dict() for removed_time in self.removed_times],
            'full_availability_flag': self.full_availability_flag,
            'note': self.note,
            'availability': [timeblock.to_dict() for timeblock in self.availability],
            'blocked_until': self.blocked_until.isoformat() if self.blocked_until else None
        }

    def __repr__(self) -> str:
        if self.member.nick:
            return f'{self.member.nick}'
        return f'{self.member.name}'
