'''Written by Cael Shoop.'''

import discord


class Buttons:
    '''Buttons class'''

    def __init__(self):
        self.buttons = []
        self.sixteen_hundred_hours             = '16:00'
        self.sixteen_hundred_thirty_hours      = '16:30'
        self.seventeen_hundred_hours           = '17:00'
        self.seventeen_hundred_thirty_hours    = '17:30'
        self.eighteen_hundred_hours            = '18:00'
        self.eighteen_hundred_thirty_hours     = '18:30'
        self.nineteen_hundred_hours            = '19:00'
        self.nineteen_hundred_thirty_hours     = '19:30'
        self.twenty_hundred_hours              = '20:00'
        self.twenty_hundred_thirty_hours       = '20:30'
        self.twenty_one_hundred_hours          = '21:00'
        self.twenty_one_hundred_thirty_hours   = '21:30'
        self.twenty_two_hundred_hours          = '22:00'
        self.twenty_two_hundred_thirty_hours   = '22:30'
        self.twenty_three_hundred_hours        = '23:00'
        self.twenty_three_hundred_thirty_hours = '23:30'
        self.zero_hundred_hours                = '00:00'
        self.zero_hundred_thirty_hours         = '00:30'
        self.one_hundred_hours                 = '01:00'
        self.one_hundred_thirty_hours          = '01:30'
        self.done                              = 'Done'

    def make_button(self, time):
        button = discord.ui.Button(
            style=discord.ButtonStyle.red,
            label=str(time),
            custom_id=str(time)
        )
        self.buttons.append(button)
        return button
