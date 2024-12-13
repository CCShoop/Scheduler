import os
import json


class Persistence():
    def __init__(self, filename):
        self.filename = filename

    def read(self):
        if os.path.exists(self.filename):
            with open(self.filename, 'r', encoding='utf-8') as file:
                return json.load(file)
        return None

    def write(self, data = {}):
        json_data = json.dumps(data, indent=4)
        with open(self.filename, 'w+', encoding='utf-8') as file:
            file.write(json_data)