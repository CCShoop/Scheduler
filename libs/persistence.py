import os
import json


class Persistence():
    def __init__(self, filename):
        self.filename = filename
        self.reading = False
        self.saving = False
        self.paused = False

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def read(self):
        if not self.paused and not self.reading and not self.saving:
            if os.path.exists(self.filename):
                self.reading = True
                with open(self.filename, 'r', encoding='utf-8') as file:
                    self.reading = False
                    return json.load(file)
                self.reading = False
        return None

    def write(self, data={}):
        if not self.paused and not self.reading and not self.saving:
            self.saving = True
            json_data = json.dumps(data, indent=4)
            with open(self.filename, 'w+', encoding='utf-8') as file:
                file.write(json_data)
            self.saving = False
