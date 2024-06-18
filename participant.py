import re
from discord import Guild, Member, NotFound, HTTPException
from asyncio import Lock
from datetime import datetime, timedelta
from calendar import isleap

class TimeBlock():
    def __init__(self, start_time: datetime, end_time: datetime) -> None:
        self.start_time: datetime = start_time
        self.end_time: datetime = end_time
        self.duration: timedelta = end_time - start_time

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            start_time = datetime.fromisoformat(data["start_time"]),
            end_time = datetime.fromisoformat(data["end_time"])
        )

    def to_dict(self) -> dict:
        return {
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat()
        }

    def __repr__(self):
        return f'{self.start_time.strftime("%A, %m/%d %H:%M")} - {self.end_time.strftime("%A, %m/%d %H:%M")}'

class Participant:
    def __init__(self,
        member: Member,
        availability: list = None,
        answered: bool = False,
        subscribed: bool = True,
        unavailable: bool = False,
        full_availability_flag: bool = False
    ) -> None:
        self.member = member
        self.availability = availability if availability else []
        self.answered = answered
        self.subscribed = subscribed
        self.unavailable = unavailable
        self.full_availability_flag = full_availability_flag
        self.msg_lock = Lock()

    # Participant is available at the specified time for the specified duration
    def is_available_at(self, time: datetime, duration: timedelta) -> bool:
        for timeblock in self.availability:
            if (timeblock.start_time <= time) and ((time + duration) <= timeblock.end_time):
                return True
        return False

    # Get the participant's availability in string format
    def get_availability_string(self) -> str:
        response = ''
        for timeblock in self.availability:
            response += f'{timeblock}\n'
        return response

    # Set the participant as available until midnight today
    def set_full_availability(self, month = None, day = None, year = None, end_time = None) -> None:
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
                end_time = cur_time.replace(month=month, day=day, year=year, hour=0, minute=0)
            end_time += timedelta(days=1)
            self.availability.append(TimeBlock(start_time, end_time))
            self.answered = True
            self.full_availability_flag = True
            self.clean_availability()
        except Exception as e:
            raise e

    # Remove the participant's availability for the specified day, otherwise today
    def set_no_availability(self) -> None:
        self.availability.clear()
        self.answered = False

    # Complex availability input from the Availability Modal
    def set_specific_availability(self, avail_string: str, date_string: str) -> None:
        # Blank input to view current availability
        if avail_string == '':
            return

        avail_string = avail_string.lower()

        # Date parsing
        try:
            month, day, year = date_string.split('/')
        except:
            try:
                month, day = date_string.split('/')
                year = datetime.now().astimezone().year
            except:
                try:
                    day = int(date_string)
                    month = datetime.now().astimezone().month
                    year = datetime.now().astimezone().year
                except:
                    raise Exception(f'Invalid date format provided by user: {date_string}')
        try:
            month = int(month)
        except:
            raise Exception(f'Invalid month: {month}')
        try:
            day = int(day)
        except:
            raise Exception(f'Invalid day: {day}')
        try:
            year = int(year)
        except:
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
        if month == 6 and day > 31:
            raise Exception(f'Invalid day provided by user: {day}')
        if month == 7 and day > 30:
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
        timeblocks = []
        for timeblock in timeblock_strings.copy():
            # Stripping
            timeblock = timeblock.replace(' ', '')
            if timeblock == '':
                continue
            timeblock = timeblock.replace(':', '')
            timeblock = timeblock.replace(';', '')
            if '--' in timeblock:
                raise Exception(f'Invalid time provided by user: cannot double hyphen (--)')
            start_time, part, end_time = timeblock.partition('-')

            # Start/end time keywords
            if 'now' in start_time or 'cur' in start_time or 'curr' in start_time or 'current' in start_time:
                start_time = datetime.now().astimezone().replace(second=0, microsecond=0).strftime("%H%M")
            if 'now' in end_time or 'cur' in end_time or 'curr' in end_time or 'current' in end_time:
                raise Exception(f'Invalid end time provided by user: cannot use current time as end time')

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
                if end_time < start_time:
                    end_time += timedelta(days=1)

            self.availability.append(TimeBlock(start_time, end_time))
            self.clean_availability()

    # Combine intersecting/touching availability
    def clean_availability(self) -> None:
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

    # Remove the participant's availability for an event with its start time and duration
    def remove_availability_for_event(self, event_start_time: datetime, event_duration: timedelta) -> None:
        if not self.availability:
            return
        event_end_time = event_start_time + event_duration
        new_availability = []
        for timeblock in self.availability:
            # Timeblock does not overlap with event
            if timeblock.end_time <= event_start_time or event_end_time <= timeblock.start_time:
                new_availability.append(timeblock)
            # Timeblock overlaps with event
            else:
                # Timeblock starts before event
                if timeblock.start_time < event_start_time:
                    new_availability.append(TimeBlock(timeblock.start_time, event_start_time))
                # Timeblock ends after event
                if event_end_time < timeblock.end_time:
                    new_availability.append(TimeBlock(event_end_time, timeblock.end_time))
        self.availability = new_availability
        if self.availability:
            self.clean_availability()
        else:
            self.full_availability_flag = False

    # Confirm the participant's availability is still valid
    def confirm_answered(self, duration: timedelta = timedelta(minutes=30)) -> None:
        if self.availability:
            new_availability = []
            cur_time = datetime.now().astimezone().replace(second=0, microsecond=0)
            for tb in self.availability:
                if cur_time + duration <= tb.end_time:
                    new_availability.append(tb)
            self.availability = new_availability
        if not self.availability:
            self.answered = False
            self.full_availability_flag = False

    @classmethod
    def from_dict(cls, guild: Guild, data: dict):
        return cls(
            member = guild.get_member(data['member_id']),
            answered = data['answered'],
            subscribed = data['subscribed'],
            unavailable = data['unavailable'],
            full_availability_flag = data['full_availability_flag'],
            availability = [TimeBlock.from_dict(timeblock_data) for timeblock_data in data['availability']]
        )

    def to_dict(self) -> dict:
        return {
            'member_id': self.member.id,
            'answered': self.answered,
            'subscribed': self.subscribed,
            'unavailable': self.unavailable,
            'full_availability_flag': self.full_availability_flag,
            'availability': [timeblock.to_dict() for timeblock in self.availability]
        }

    def __repr__(self) -> str:
        return f'{self.member.name}'