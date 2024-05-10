from discord import Member
from asyncio import Lock
from datetime import datetime, timedelta

class Times():
    def __init__(self):
        self.start_time: datetime
        self.duration: timedelta

class Participant():
    def __init__(self, member: Member):
        self.member = member
        self.availability = Participant.Availability()
        self.answered = False
        self.subscribed = True
        self.msg_lock = Lock()

    class Availability:
        def __init__(self):
            self.dates = {}
            self.times = []
