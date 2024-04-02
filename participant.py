from discord import User

class Participant(User):
    def __init__(self, member):
        super().__init__()
        self.availability = Participant.Availability()
        self.answered = False
        self.subscribed = True
        self.msg_lock = Lock()

    class Availability:
        def __init__(self):
            self.dates = {}
            self.times = []