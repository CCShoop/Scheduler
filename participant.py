from discord import Member
from asyncio import Lock
from datetime import datetime, timedelta
from calendar import isleap

class TimeBlock():
    def __init__(self, start_time: datetime, end_time: datetime):
        self.start_time: datetime = start_time
        self.end_time: datetime = end_time
        self.duration: timedelta = end_time - start_time

class Participant():
    def __init__(self, member: Member):
        self.member = member
        self.answered = False
        self.subscribed = True
        self.unavailable = False
        self.full_availability_flag = False
        self.msg_lock = Lock()
        self.availability = []

    def is_available_at(self, time: datetime, duration: timedelta):
        for timeblock in self.availability:
            if (time >= timeblock.start_time) and ((time + duration) <= timeblock.end_time):
                return True
        return False

    def set_full_availability(self):
        try:
            start_time = datetime.now().astimezone().replace(second=0, microsecond=0)
            end_time = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            end_time += timedelta(days=1)
            avail_block = TimeBlock(start_time, end_time)
            self.availability.append(avail_block)
            self.answered = True
        except Exception as e:
            raise(e)

    def set_no_availability(self):
        self.availability.clear()
        self.answered = True

    def set_specific_availability(self, avail_string: str, date_string: str):
        avail_string = avail_string.lower()

        # Date parsing
        try:
            month, day, year = date_string.split('/')
        except:
            try:
                month, day = date_string.split('/')
                year = datetime.now().astimezone().year
            except:
                raise(Exception(f'Invalid date format provided by user: {date_string}'))
        try:
            month = int(month)
        except:
            raise(Exception(f'Invalid month: {month}'))
        try:
            day = int(day)
        except:
            raise(Exception(f'Invalid day: {day}'))
        try:
            year = int(year)
        except:
            raise(Exception(f'Invalid year: {year}'))

        # Date validity check
        if year < datetime.now().astimezone().year:
            raise(Exception(f'Cannot schedule for the past: {year}'))
        if month < 1 or month > 12:
            raise(Exception(f'Invalid month provided by user: {month}'))
        if day < 0:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if month == 1 and day > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if isleap(datetime.now().astimezone().year):
            if month == 2 and day > 29:
                raise(Exception(f'Invalid day provided by user: {day}'))
        else:
            if month == 2 and day > 28:
                raise(Exception(f'Invalid day provided by user: {day}'))
        if month == 3 and day > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if month == 4 and day > 30:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if month == 5 and day > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if month == 6 and day > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if month == 7 and day > 30:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if month == 8 and day > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if month == 9 and day > 30:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if month == 10 and day > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if month == 11 and day > 30:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if month == 12 and day > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))

        # Check if the entered date is today
        date_is_today = False
        curMonth = datetime.now().astimezone().month
        curDay = datetime.now().astimezone().day
        if curMonth == month and curDay == day:
            date_is_today = True

        # Timezone parsing
        timezone_offset = 0
        avail_string = avail_string.replace('s', '')
        avail_string = avail_string.replace('d', '')
        avail_string = avail_string.replace('t', '')
        if 'e' in avail_string:
            timezone_offset = 0
            avail_string = avail_string.replace('e', '')
        elif 'c' in avail_string:
            timezone_offset = 1
            avail_string = avail_string.replace('c', '')
        elif 'm' in avail_string:
            timezone_offset = 2
            avail_string = avail_string.replace('m', '')
        elif 'p' in avail_string:
            timezone_offset = 3
            avail_string = avail_string.replace('p', '')

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
                raise(Exception(f'Invalid time provided by user: cannot double hyphen (--)'))
            start_time, part, end_time = timeblock.partition('-')

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
                raise(Exception(f'Invalid start time provided by user: {start_time}'))
            if end_time != '' and (len(end_time) < 4 or int(end_time) > 2359):
                raise(Exception(f'Invalid end time provided by user: {start_time}'))

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
            # End time is midnight
            if end_time_string == '':
                end_time = datetime.now().astimezone().replace(month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
                end_time += timedelta(days=1)
            # End time is defined
            else:
                end_hr = int(end_time_string[:2])
                end_min = int(end_time_string[2:])
                end_time = datetime.now().astimezone().replace(month=month, day=day, hour=end_hr, minute=end_min, second=0, microsecond=0)

            avail_block = TimeBlock(start_time, end_time)
            self.availability.append(avail_block)
        self.answered = True
