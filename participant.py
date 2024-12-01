import re
from discord import Guild, Member
from asyncio import Lock
from datetime import datetime, timedelta
from calendar import isleap


HOURS_PAST_MIDNIGHT_CUTOFF = 2


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
        self.duration: timedelta = end_time - start_time

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
        # return f'{self.start_time.strftime("%a, %m/%d %H:%M")} - {self.end_time.strftime("%a, %m/%d %H:%M")}'
        return f'{self.start_time.strftime("%a, %m/%d %H:%M")} - {self.end_time.strftime("%H:%M")}'


class RemovedTime:
    """
    Represents a combination of event name and timeblock
    for time removed from a participant's availability for an event.

    Attributes
    -----------
    event_name: :class:`str`
        The name of the event that the timeblock represents.
    timeblock: :class:`TimeBlock`
        The timeblock representing the event.
    """

    def __init__(self, event_name: str, timeblock: TimeBlock):
        self.event_name = event_name
        self.timeblock = timeblock

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            event_name=data['event_name'],
            timeblock=TimeBlock.from_dict(data['timeblock'])
        )

    def to_dict(self) -> dict:
        return {
            'event_name': self.event_name,
            'timeblock': self.timeblock.to_dict()
        }


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
                 full_availability_flag: bool = False
                 ) -> None:
        self.member = member
        self.availability = availability or []
        self.answered = answered
        self.subscribed = subscribed
        self.unavailable = unavailable
        self.removed_times = removed_times or []
        self.full_availability_flag = full_availability_flag
        self.msg_lock = Lock()

    # Participant is available at the specified time for the specified duration
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

    # Get the participant's availability in string format
    def get_availability_string(self) -> str:
        """
        Gets the availability string of the participant.

        Returns
        --------
        response: :class:`str`
            The participant's availability string
        """
        removed_index = 0
        response = ''
        if self.full_availability_flag:
            response += "Full Availability"
        for timeblock in self.availability:
            if removed_index < len(self.removed_times):
                removed_time = self.removed_times[removed_index]
                if removed_time.timeblock.start_time < timeblock.start_time:
                    response += f'\n{removed_time.event_name[:20]}'
                    response += f' {removed_time.timeblock.start_time.strftime("%H:%M")} - {removed_time.timeblock.end_time.strftime("%H:%M")}'
                    removed_index += 1
            response += f'\n{timeblock}'
        return response

    def set_full_availability(self, month=None, day=None, year=None, end_time=None) -> None:
        """
        Sets the aprticipant to have full deliver.

        Arguments
        ----------
        month: :class:`int`
            Optional. Current entered month.
            Default: Current
        day: :class:`int`
            Optional. Current entered day.
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
            start_time = cur_time.replace(month=month, day=day, year=year)
            if not end_time:
                end_time = cur_time.replace(month=month, day=day, year=year, hour=HOURS_PAST_MIDNIGHT_CUTOFF, minute=0) + timedelta(days=1)
            self.availability.append(TimeBlock(start_time, end_time))
            self.answered = True
            self.full_availability_flag = True
            self.clean_availability()
        except Exception as e:
            raise e

    def set_no_availability(self) -> None:
        """
        Sets the participant to have no availability.
        """
        self.availability.clear()
        self.answered = False
        self.full_availability_flag = False

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
        # Blank input to view current availability
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

        # Date validity check
        if year < datetime.now().astimezone().year:
            raise Exception(f'Cannot schedule for the past: {year}')
        if month < 1 or month > 12:
            raise Exception(f'Invalid month provided by user: {month}')
        if day < 0:
            raise Exception(f'Invalid day provided by user: {day}')
        if month == 1 and day > 31:
            raise Exception(f'Invalid day provided by user: {day}')
        if isleap(datetime.now().astimezone().year):
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
        if 'full' in avail_string or 'all' in avail_string:
            self.set_full_availability(month=month, day=day, year=year)
            return
        if 'clear' in avail_string or 'empty' in avail_string:
            self.set_no_availability()
            return

        # Timezone parsing
        timezone_offset = 0
        avail_string = avail_string.replace('s', '')
        avail_string = avail_string.replace('d', '')
        if 'et' in avail_string:
            timezone_offset = 0
            avail_string = avail_string.replace('et', '')
        elif 'ct' in avail_string:
            timezone_offset = 1
            avail_string = avail_string.replace('ct', '')
        elif 'mt' in avail_string:
            timezone_offset = 2
            avail_string = avail_string.replace('mt', '')
        elif 'pt' in avail_string:
            timezone_offset = 3
            avail_string = avail_string.replace('pt', '')

        # 12-hour time parsing pt. 1
        avail_string = avail_string.replace('.', '')
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
            if 'pm' in start_time:
                start_time = re.sub(r"\D", "", start_time)
                start_time = str(int(start_time) + 12)
            else:
                start_time = re.sub(r"\D", "", start_time)
            if 'pm' in end_time:
                end_time = re.sub(r"\D", "", end_time)
                end_time = str(int(end_time) + 12)
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
                    start_time = datetime.now().astimezone().replace(month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
            # Start time is defined
            else:
                start_hr = int(start_time_string[:2])
                start_min = int(start_time_string[2:])
                start_time = datetime.now().astimezone().replace(month=month, day=day, hour=start_hr, minute=start_min, second=0, microsecond=0)
                start_time += timedelta(hours=timezone_offset)
            # End time is midnight
            if end_time_string == '':
                end_time = datetime.now().astimezone().replace(month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
                end_time += timedelta(days=1)
            # End time is defined
            else:
                end_hr = int(end_time_string[:2])
                end_min = int(end_time_string[2:])
                end_time = datetime.now().astimezone().replace(month=month, day=day, hour=end_hr, minute=end_min, second=0, microsecond=0)
                end_time += timedelta(hours=timezone_offset)
                while end_time < start_time:
                    end_time += timedelta(days=1)

            # Currency check
            while end_time < datetime.now().astimezone():
                start_time += timedelta(days=1)
                end_time += timedelta(days=1)

            self.availability.append(TimeBlock(start_time, end_time))
            self.clean_availability()

    def clean_availability(self) -> None:
        """
        Cleans the participant's availability by combining overlapping/touching timeblocks.
        """
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

    def remove_availability_for_event(self, event_name: str, event_start_times: list, event_duration: timedelta) -> None:
        """
        Removes availability for another event and stores it separately.

        Arguments
        ----------
        event_name: :class:`str`
            The event of the name that is occupying the availability.
        event_start_times: :class:`list`
            The list of event start times.
        event_duration: :class:`timedelta`
            The duration of the event.
        """
        if not self.availability:
            return
        new_availability = []
        changed = False
        for event_start_time in event_start_times:
            changed = True
            event_end_time = event_start_time + event_duration
            for timeblock in self.availability:
                # Timeblock does not overlap with event
                if timeblock.end_time <= event_start_time or event_end_time <= timeblock.start_time:
                    new_availability.append(timeblock)
                # Timeblock overlaps with event
                else:
                    self.removed_times.append(RemovedTime(event_name, TimeBlock(event_start_time, event_end_time)))
                    # Timeblock starts before event
                    if timeblock.start_time < event_start_time:
                        new_availability.append(TimeBlock(timeblock.start_time, event_start_time))
                    # Timeblock ends after event
                    if event_end_time < timeblock.end_time:
                        new_availability.append(TimeBlock(event_end_time, timeblock.end_time))
        if changed:
            self.availability = new_availability
            if self.availability:
                self.clean_availability()
            else:
                self.full_availability_flag = False

    def restore_availability_for_event(self, event_name: str) -> None:
        """
        Restores availability for an event for which it was removed.

        Arguments
        ----------
        event_name: :class:`str`
            The name of the event to restore availability from.
        """
        for removed_time in self.removed_times.copy():
            if removed_time.name == event_name:
                self.availability.append(removed_time.timeblock)
                self.removed_times.remove(removed_time)
                self.clean_availability()
                break

    def confirm_answered(self, duration: timedelta = timedelta(minutes=30), latest_date=None) -> None:
        """
        Confirms that the participant's availability is valid.

        Arguments
        ----------
        duration: :class:`timedelta`
            Optional. Duration of the event in minutes.
            Default: 30 minutes
        latest_date: :class:`datetime.date`
            Optional: Latest date from other participants in the event.
        """
        if self.availability:
            new_availability = []
            cur_time = datetime.now().astimezone().replace(second=0, microsecond=0)
            for tb in self.availability:
                if tb.start_time < cur_time:
                    tb.start_time = cur_time
                if cur_time + duration <= tb.end_time:
                    new_availability.append(tb)
            self.availability = new_availability
        if self.availability and latest_date is not None:
            self.answered = self.availability[-1].start_time.date() >= latest_date
        if not self.availability:
            self.answered = False
            self.full_availability_flag = False

    @classmethod
    def from_dict(cls, guild: Guild, data: dict):
        return cls(
            member=guild.get_member(data['member_id']),
            answered=data['answered'],
            subscribed=data['subscribed'],
            unavailable=data['unavailable'],
            removed_times=[RemovedTime.from_dict(removed_time) for removed_time in data['removed_time']],
            full_availability_flag=data['full_availability_flag'],
            availability=[TimeBlock.from_dict(timeblock_data) for timeblock_data in data['availability']]
        )

    def to_dict(self) -> dict:
        return {
            'member_id': self.member.id,
            'answered': self.answered,
            'subscribed': self.subscribed,
            'unavailable': self.unavailable,
            'removed_time': [removed_time.to_dict() for removed_time in self.removed_times],
            'full_availability_flag': self.full_availability_flag,
            'availability': [timeblock.to_dict() for timeblock in self.availability]
        }

    def __repr__(self) -> str:
        if self.member.nick:
            return f'{self.member.nick}'
        return f'{self.member.name}'
