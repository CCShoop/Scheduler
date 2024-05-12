from discord import Member
from asyncio import Lock
from datetime import datetime, timedelta
from calendar import isleap

class TimeBlock():
    def __init__(self, start_time: datetime, end_time: datetime):
        self.start_time: datetime = start_time
        self.end_time: datetime = end_time
        self.duration: timedelta = end_time - start_time

    def overlaps_with(self, availability):
        for timeblock in availability:
            if (timeblock.start_time < timeblock.end_time and timeblock.start_time < timeblock.end_time):
                return True
        return False

class Participant():
    def __init__(self, member: Member):
        self.member = member
        self.answered = False
        self.subscribed = True
        self.unavailable = False
        self.msg_lock = Lock()
        self.availability = []

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
        month, part, day = date_string.partition('/')
        log_debug(f'month: {month}, day: {day}')
        if int(month) < 1 or int(month) > 12:
            raise(Exception(f'Invalid month provided by user: {month}'))
        if int(day) < 0:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if int(month) == 1 and int(day) > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if isleap(datetime.now().astimezone().year):
            if int(month) == 2 and int(day) > 29:
                raise(Exception(f'Invalid day provided by user: {day}'))
        else:
            if int(month) == 2 and int(day) > 28:
                raise(Exception(f'Invalid day provided by user: {day}'))
        if int(month) == 3 and int(day) > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if int(month) == 4 and int(day) > 30:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if int(month) == 5 and int(day) > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if int(month) == 6 and int(day) > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if int(month) == 7 and int(day) > 30:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if int(month) == 8 and int(day) > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if int(month) == 9 and int(day) > 30:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if int(month) == 10 and int(day) > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if int(month) == 11 and int(day) > 30:
            raise(Exception(f'Invalid day provided by user: {day}'))
        if int(month) == 12 and int(day) > 31:
            raise(Exception(f'Invalid day provided by user: {day}'))

        # Timezone
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

        # Make time list
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
            if start_time_string == '':
                start_time = datetime.now().astimezone().replace(second=0, microsecond=0)
            else:
                start_hr = int(start_time_string[:2])
                start_min = int(start_time_string[2:])
                start_time = datetime.now().astimezone().replace(hour=start_hr, minute=start_min, second=0, microsecond=0)
            if end_time_string == '':
                end_time = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
                end_time += timedelta(days=1)
            else:
                end_hr = int(end_time_string[:2])
                end_min = int(end_time_string[2:])
                end_time = datetime.now().astimezone().replace(hour=end_hr, minute=end_min, second=0, microsecond=0)

            avail_block = TimeBlock(start_time, end_time)
            self.availability.append(avail_block)
        self.answered = True
