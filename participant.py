from discord import Member
from asyncio import Lock
from datetime import datetime, timedelta

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
        self.msg_lock = Lock()
        self.availability = []

    def set_full_availability(self):
        start_time = datetime().astimezone().now().replace(second=0, microsecond=0)
        end_time = datetime().astimezone().now().replace(hour=0, minute=0, second=0, microsecond=0)
        end_time += timedelta(days=1)
        avail_block = TimeBlock(start_time, end_time)
        self.availability.append(avail_block)

    def set_no_availability(self):
        self.availability.clear()

    def set_specific_availability(self, avail_string: str):
        avail_string = avail_string.lower()

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
            start_time, part, end_time = timeblock.partition('-')

            # Affixing and Appending 0s
            if start_time != '':
                if int(start_time) < 10 and len(start_time) == 1:
                    start_time = '0' + start_time
                if int(start_time) < 24:
                    start_time = start_time + '00'
            if end_time != '':
                if int(end_time) < 10 and len(end_time) == 1:
                    end_time = '0' + end_time
                if int(end_time) < 24:
                    end_time = end_time + '00'

            # Validity check
            if len(start_time) < 4 or int(end_time) > 2359:
                raise(Exception('Invalid start time provided by user'))
            if len(end_time) < 4 or int(end_time) > 2359:
                raise(Exception('Invalid end time provided by user'))

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
