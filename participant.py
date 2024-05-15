import re
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

    def get_availability_string(self):
        response = '**__Availability Received!__**\n'
        for timeblock in self.availability:
            response += f'{timeblock.start_time.strftime("%A, %m/%d: %H%M")} - {timeblock.end_time.strftime("%A, %m/%d: %H%M")}\n'
        return response

    def set_full_availability(self, month=datetime.now().astimezone().month, day=datetime.now().astimezone().day, year=datetime.now().astimezone().year):
        try:
            start_time = datetime.now().astimezone().replace(month=month, day=day, year=year, second=0, microsecond=0)
            end_time = datetime.now().astimezone().replace(month=month, day=day, year=year, hour=0, minute=0, second=0, microsecond=0)
            end_time += timedelta(days=1)
            avail_block = TimeBlock(start_time, end_time)
            self.availability.append(avail_block)
            self.answered = True
        except Exception as e:
            raise(e)

    def set_no_availability(self, month=datetime.now().astimezone().month, day=datetime.now().astimezone().day, year=datetime.now().astimezone().year):
        for idx, timeblock in enumerate(self.availability.copy()):
            if timeblock.start_time.day == day:
                self.availability.remove(timeblock)

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
                try:
                    day = int(date_string)
                    month = datetime.now().astimezone().month
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

        # Keyword shortcuts
        if 'full' in avail_string or 'all' in avail_string:
            set_full_availability(month=month, day=day, year=year)
            return
        if 'clear' in avail_string or 'empty' in avail_string:
            set_no_availability(month=month, day=day, year=year)
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
                raise(Exception(f'Invalid time provided by user: cannot double hyphen (--)'))
            start_time, part, end_time = timeblock.partition('-')

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
                if end_time < start_time:
                    end_time += timedelta(days=1)

            avail_block = TimeBlock(start_time, end_time)
            self.availability.append(avail_block)
        self.answered = True
