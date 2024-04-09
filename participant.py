from discord import Member

class Participant(Member):
    def __init__(self, member: Member):
        super().__init__(data=member.__dict__ , guild=member.guild, state=member._state)
        self.availability = Participant.Availability()
        self.answered = False
        self.subscribed = True
        self.msg_lock = Lock()

    class Availability:
        def __init__(self):
            self.dates = {}
            self.times = []