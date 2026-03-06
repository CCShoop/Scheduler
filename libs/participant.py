import re
from discord import Guild, Member
from asyncio import Lock
from datetime import datetime, timedelta
from calendar import isleap


HOURS_PAST_MIDNIGHT_CUTOFF = 2


def print_time_until(time: datetime) -> str:
    return f"<t:{int(time.timestamp())}:R>"


def print_date_time(time: datetime) -> str:
    return f"<t:{int(time.timestamp())}:F>"


def print_date_time_abbreviated(time: datetime) -> str:
    return f"<t:{int(time.timestamp())}>"


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
        return f'{print_date_time_abbreviated(self.start_time)} - {print_date_time_abbreviated(self.end_time)}'


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
    removed_timeblock: :class:`TimeBlock`
        The timeblock of removed availability.
    """

    def __init__(self, event_name: str,
                 event_timeblock: TimeBlock,
                 removed_timeblock: TimeBlock):
        self.event_name = event_name
        self.event_timeblock = event_timeblock
        self.removed_timeblock = removed_timeblock

    @property
    def start_time(self) -> datetime:
        return self.event_timeblock.start_time

    @property
    def end_time(self) -> datetime:
        return self.event_timeblock.end_time

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            event_name=data['event_name'],
            event_timeblock=TimeBlock.from_dict(data['timeblock']),
            removed_timeblock=TimeBlock.from_dict(data['removed_timeblock'])
        )

    def to_dict(self) -> dict:
        timeblock_dict = self.event_timeblock.to_dict()
        removed_timeblock_dict = None
        if self.removed_timeblock is not None:
            removed_timeblock_dict = self.removed_timeblock.to_dict()
        return {
            'event_name': self.event_name,
            'timeblock': timeblock_dict,
            'removed_timeblock': removed_timeblock_dict
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
    """

    def __init__(self,
                 member: Member,
                 availability: list = None,
                 answered: bool = False,
                 subscribed: bool = True,
                 unavailable: bool = False,
                 removed_times: list = None,
                 full_availability_flag: bool = False,
                 note: str = "") -> None:
        self.member = member
        self.availability = availability or []
        self.answered = answered
        self.subscribed = subscribed
        self.unavailable = unavailable
        self.removed_times = removed_times or []
        self.full_availability_flag = full_availability_flag
        self.note = note
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

    def set_full_availability(self, day=None, month=None, year=None, end_time=None) -> None:
        """
        Sets the aprticipant to have full deliver.

        Arguments
        ----------
        day: :class:`int`
            Optional. Current entered day.
            Default: Current
        month: :class:`int`
            Optional. Current entered month.
            Default: Current
        year: :class:`int`
            Optional. Current entered year.
            Default: Current
        end_time: class:`datetime`
            Optional. Current end time.
            Default: Current
        """
        try:
            cur_time = datetime.now().astimezone().replace(second=0, microsecond=0)
            if not month:
                month = cur_time.month
            if not day:
                day = cur_time.day
            if not year:
                year = cur_time.year
            start_time = cur_time.replace(day=day,
                                          month=month,
                                          year=year)
            if not end_time:
                end_time = cur_time.replace(day=day,
                                            month=month,
                                            year=year,
                                            hour=HOURS_PAST_MIDNIGHT_CUTOFF,
                                            minute=0)
                end_time += timedelta(days=1)
            self.add_to_availability(TimeBlock(start_time, end_time))
            self.answered = True
            self.full_availability_flag = True
            self.clean_availability()
        except Exception as e:
            raise e

    def set_no_availability(self, day=None, month=None, year=None) -> None:
        """
        Sets the participant to have no availability.

        Arguments
        ----------
        day: :class:`int`
            Optional. Current entered day.
            Default: None
        month: :class:`int`
            Optional. Current entered month.
            Default: None
        year: :class:`int`
            Optional. Current entered year.
            Default: None
        """
        self.full_availability_flag = False
        if day is None or month is None or year is None:
            self.availability.clear()
        else:
            new_availability = []
            for timeblock in self.availability:
                if timeblock.start_time.day != day or \
                        timeblock.start_time.month != month or \
                        timeblock.start_time.year != year:
                    new_availability.append(timeblock)
            self.availability = new_availability

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
        try:
            month, day, year = date_string.split('/')
        except Exception:
            try:
                month, day = date_string.split('/')
                year = datetime.now().astimezone().year
            except Exception:
                try:
                    day = int(date_string)
                    month = datetime.now().astimezone().month
                    year = datetime.now().astimezone().year
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

        curYear = datetime.now().astimezone().year
        # Convert YY to YYYY
        if year < 100:
            year += curYear - (curYear % 100)
        # Date validity check
        if year < curYear:
            raise Exception(f'Cannot schedule for the past: {year}')
        if month < 1 or month > 12:
            raise Exception(f'Invalid month provided by user: {month}')
        if day < 0:
            raise Exception(f'Invalid day provided by user: {day}')
        if month == 1 and day > 31:
            raise Exception(f'Invalid day provided by user: {day}')
        if isleap(curYear):
            if month == 2 and day > 29:
                raise Exception(f'Invalid day provided by user: {day}')
        else:
            if month == 2 and day > 28:
                raise Exception(f'Invalid day provided by user: {day}')
        if month == 3 and day > 31:
            raise Exception(f'Invalid day provided by user: {day}')
        if month == 4 and day > 30:
            raise Exception(f'Invalid day provided by user: {day}')
        if month == 5 and day > 31:
            raise Exception(f'Invalid day provided by user: {day}')
        if month == 6 and day > 30:
            raise Exception(f'Invalid day provided by user: {day}')
        if month == 7 and day > 31:
            raise Exception(f'Invalid day provided by user: {day}')
        if month == 8 and day > 31:
            raise Exception(f'Invalid day provided by user: {day}')
        if month == 9 and day > 30:
            raise Exception(f'Invalid day provided by user: {day}')
        if month == 10 and day > 31:
            raise Exception(f'Invalid day provided by user: {day}')
        if month == 11 and day > 30:
            raise Exception(f'Invalid day provided by user: {day}')
        if month == 12 and day > 31:
            raise Exception(f'Invalid day provided by user: {day}')

        # Check if the entered date is today
        date_is_today = False
        curMonth = datetime.now().astimezone().month
        curDay = datetime.now().astimezone().day
        if curMonth == month and curDay == day:
            date_is_today = True

        # Keyword shortcuts
        if 'full' in avail_string:
            self.set_full_availability(day=day, month=month, year=year)
            return
        if 'clear' in avail_string:
            self.set_no_availability(day=day, month=month, year=year)
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

        # 12-hour time parsing pt. 1
        avail_string = avail_string.replace('.', '')
        if '12am' in avail_string:
            avail_string = avail_string.replace('12am', '0000')
        if '1200am' in avail_string:
            avail_string = avail_string.replace('1200am', '0000')
        avail_string = avail_string.replace('am', '')

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
            start_time, part, end_time = timeblock.partition('-')

            # Start/end time keywords
            if 'now' in start_time or 'cur' in start_time or 'curr' in start_time or 'current' in start_time:
                start_time = datetime.now().astimezone().replace(second=0, microsecond=0).strftime("%H%M")
            if 'now' in end_time or 'cur' in end_time or 'curr' in end_time or 'current' in end_time:
                raise Exception("Invalid end time provided by user: cannot use current time as end time")

            # 12-hour time parsing pt. 2
            if 'pm' in start_time and '12' not in start_time:
                start_time = re.sub(r"\D", "", start_time)
                if len(start_time) == 1 or len(start_time) == 2:
                    start_time = str(int(start_time) + 12)
                elif len(start_time) == 3 or len(start_time) == 4:
                    start_time = str(int(start_time) + 1200)
                else:
                    raise Exception(f'Invalid start time provided by user: {start_time}')
            else:
                start_time = re.sub(r"\D", "", start_time)
            if 'pm' in end_time and '12' not in end_time:
                end_time = re.sub(r"\D", "", end_time)
                if len(end_time) == 1 or len(end_time) == 2:
                    end_time = str(int(end_time) + 12)
                elif len(end_time) == 3 or len(end_time) == 4:
                    end_time = str(int(end_time) + 1200)
                else:
                    raise Exception(f'Invalid end time provided by user: {start_time}')
            else:
                end_time = re.sub(r"\D", "", end_time)

            # Affixing and Appending 0s
            if start_time != '':
                if (int(start_time) < 10 and len(start_time) == 1) or len(start_time) == 3:
                    start_time = '0' + start_time
                if int(start_time) < 24:
                    start_time = start_time + '00'
            if end_time != '':
                if (int(end_time) < 10 and len(end_time) == 1) or len(end_time) == 3:
                    end_time = '0' + end_time
                if int(end_time) < 24:
                    end_time = end_time + '00'

            # Validity check
            if start_time != '' and (len(start_time) < 4 or int(start_time) > 2359):
                raise Exception(f'Invalid start time provided by user: {start_time}')
            if end_time != '' and (len(end_time) < 4 or int(end_time) > 2359):
                raise Exception(f'Invalid end time provided by user: {start_time}')

            # Convert to datetime objects
            start_time_string = start_time
            end_time_string = end_time
            # Start time is now if today, midnight if not today
            if start_time_string == '':
                if date_is_today:
                    start_time = datetime.now().astimezone().replace(second=0, microsecond=0)
                else:
                    start_time = datetime.now().astimezone().replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
            # Start time is defined
            else:
                start_hr = int(start_time_string[:2])
                start_min = int(start_time_string[2:])
                start_time = datetime.now().astimezone().replace(year=year, month=month, day=day, hour=start_hr, minute=start_min, second=0, microsecond=0)
                start_time += timedelta(hours=timezone_offset)
            # End time is midnight
            if end_time_string == '':
                end_time = datetime.now().astimezone().replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
                end_time += timedelta(days=1)
            # End time is defined
            else:
                end_hr = int(end_time_string[:2])
                end_min = int(end_time_string[2:])
                end_time = datetime.now().astimezone().replace(year=year, month=month, day=day, hour=end_hr, minute=end_min, second=0, microsecond=0)
                end_time += timedelta(hours=timezone_offset)
                while end_time < start_time:
                    end_time += timedelta(days=1)

            # Currency check
            while end_time < datetime.now().astimezone():
                start_time += timedelta(days=1)
                end_time += timedelta(days=1)

            self.add_to_availability(TimeBlock(start_time, end_time))

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

    def get_availability_overlap(self, event_timeblock: TimeBlock) -> TimeBlock:
        """
        Gets the overlap between an event and the participant's availability.

        Arguments
        ---------
        event_timeblock: :class:`TimeBlock`
            The timeblock with which to get its overlap with availability.
        """
        for timeblock in self.availability:
            # Timeblock ends before or when event starts
            if timeblock.end_time <= event_timeblock.start_time:
                continue
            # Event ends before or when timeblock starts
            if event_timeblock.end_time <= timeblock.start_time:
                break
            # Overlap
            return TimeBlock(start_time=max(event_timeblock.start_time, timeblock.start_time),
                             end_time=min(event_timeblock.end_time, timeblock.end_time))
        return None

    def add_to_availability(self, timeblock: TimeBlock) -> None:
        if timeblock is not None:
            self.availability.append(timeblock)
            self.clean_availability()

    def remove_from_availability(self, timeblock: TimeBlock) -> None:
        """Removes a timeblock from availability."""
        new_availability = []
        for tb in self.availability:
            if timeblock.end_time <= tb.start_time:
                # We've passed the timeblock
                return
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
        Removes and saves availability for an event.

        Arguments
        ---------
        event_name: :class:`str`
            The name of the event the timeblocks are for.
        event_timeblocks: :class:`list[TimeBlock]`
            The event's timeblocks.
        """
        for event_timeblock in event_timeblocks:
            removed_timeblock = self.get_availability_overlap(event_timeblock)
            existing_removed_time = next(
                (rt for rt in self.removed_times
                 if rt.event_name == event_name and rt.event_timeblock.start_time.date() == event_timeblock.start_time.date()),
                None)
            if existing_removed_time:
                if existing_removed_time.removed_timeblock is not None:
                    self.add_to_availability(existing_removed_time.removed_timeblock)
                existing_removed_time.event_timeblock = event_timeblock
                existing_removed_time.removed_timeblock = removed_timeblock
            else:
                # Add a new RemovedTime
                removed_time = RemovedTime(event_name=event_name,
                                           event_timeblock=event_timeblock,
                                           removed_timeblock=removed_timeblock)
                self.removed_times.append(removed_time)
            if removed_timeblock:
                self.remove_from_availability(removed_timeblock)

    def restore_availability_for_event(self, event_name: str) -> None:
        """
        Restores availability for a cancelled or rescheduled event.

        Arguments
        ---------
        event_name: :class:`str`
            The name of the event to restore availability for.
        """
        new_removed_times = []
        for removed_time in self.removed_times:
            if removed_time.event_name == event_name:
                if removed_time.removed_timeblock:
                    self.add_to_availability(removed_time.removed_timeblock)
            else:
                new_removed_times.append(removed_time)
        self.removed_times = new_removed_times
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
                if not removed_time.removed_timeblock:
                    removed_time.removed_timeblock = self.get_availability_overlap(removed_time.event_timeblock)
                if removed_time.removed_timeblock:
                    self.remove_from_availability(removed_time.removed_timeblock)
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
                if removed_index < len(self.removed_times):
                    removed_time = self.removed_times[removed_index]
                    if removed_time.event_timeblock.start_time < timeblock.start_time:
                        response += f"{removed_time}\n"
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
    def from_dict(cls, guild: Guild, data: dict):
        return cls(
            member=guild.get_member(data['member_id']),
            answered=data['answered'],
            subscribed=data['subscribed'],
            unavailable=data['unavailable'],
            removed_times=[RemovedTime.from_dict(removed_time) for removed_time in data['removed_time']],
            full_availability_flag=data['full_availability_flag'],
            note=data['note'],
            availability=[TimeBlock.from_dict(timeblock_data) for timeblock_data in data['availability']]
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
            'availability': [timeblock.to_dict() for timeblock in self.availability]
        }

    def __repr__(self) -> str:
        if self.member.nick:
            return f'{self.member.nick}'
        return f'{self.member.name}'
