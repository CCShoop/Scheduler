from discord import Embed, Color


HELP_EMBEDS = []
# Commands
commands = Embed(title="Commands",
                 description="How to use the Event Scheduler",
                 color=Color.blurple())
commands.add_field(name="schedule",
                   value="Begin scheduling an event."
                   "\nevent_name: The name for the event. Only one event can have a specific name at any time."
                   "\nvoice_channel: The voice channel where the event will take place."
                   "\nstart_time: The start time for the event. Accepts a time (e.g. 2200) or ISO format datetime."
                   "\nimage_url: A URL for an image to use for the guild event and in embed thumbnails."
                   "\ninclude_exclude: Whether to include or exclude the provided usernames/ids/roles."
                   "\nusernames: A comma separated list of usernames/ids to include/exclude."
                   "\nroles: A comma separated list of roles to include/exclude."
                   "\nduration: The duration of the event in minutes. Default is 30 minutes."
                   "\nmulti_event: Whether or not to make one event per day, respecting availabilities.",
                   inline=False)
commands.add_field(name="create",
                   value="Create an event at a specified time."
                   "\nevent_name: The name for the event. Only one event can have a specific name at any time."
                   "\nvoice_channel: The voice channel where the event will take place."
                   "\nstart_time: The start time for the event. Accepts a time (e.g. 2200) or ISO format datetime."
                   "\nimage_url: A URL for an image to use for the guild event and in embed thumbnails."
                   "\ninclude_exclude: Whether to include or exclude the provided usernames/ids/roles."
                   "\nusernames: A comma separated list of usernames/ids to include/exclude."
                   "\nroles: A comma separated list of roles to include/exclude."
                   "\nduration: The duration of the event in minutes. Default is 30 minutes.",
                   inline=False)
commands.add_field(name="edit",
                   value="Edit an existing event using this text channel chosen from a dropdown."
                   "\nname: Change the name of the event."
                   "\nvoice_channel: Change the location of the event."
                   "\nimage_url: Change the image of the event."
                   "\nduration: Change the duration of the event."
                   "\nmulti_event: Change whether or not the event is a multi-event.",
                   inline=False)
commands.add_field(name="attach",
                   value="Make the Event Scheduler aware of an existing guild event chosen from a dropdown.",
                   inline=False)
commands.add_field(name="listevents",
                   value="Shows all events that the Event Scheduler is aware of in this guild.",
                   inline=False)
commands.add_field(name="availability",
                   value="Shows the availability for an event tied to this text channel.",
                   inline=False)
commands.add_field(name="help",
                   value="Shows this help message.",
                   inline=False)
HELP_EMBEDS.append(commands)
# Instructions
instructions = Embed(title="Instructions",
                     description="How to respond with your availability:",
                     color=Color.purple())
instructions.add_field(name="Respond Button",
                       value="Set the date and enter the periods of time you are available."
                       "\nAllows for some keyword inputs: full, clear, none"
                       "\n\tfull: full availability"
                       "\n\tclear: removes availability for selected day"
                       "\n\tnone: removes all availability"
                       "\nRequires 24 hour time (e.g. \"21-2\" is 9pm - 2am)."
                       "\nSeparate multiple periods of time with commas (e.g. \"9-12, 13-17\")."
                       "\nSet your timezone if you use your local time and it will be shifted to Eastern Time."
                       "\nCurrently supported timezones: AT, ET, CT, MT, PT"
                       "\nNote: Allows you to leave a note in the availability embed, with or without availability."
                       "\nLeaving the note field blank when resubmitting the form will clear your note.",
                       inline=False)
instructions.add_field(name="Full Availability Button",
                       value="Sets a \"full availability flag\" and adds a time period from now until midnight."
                       "\nIf someone else puts availability extending past midnight, yours will be extended to the same time.",
                       inline=False)
instructions.add_field(name="Use Existing Button",
                       value="Grabs your availability from another event."
                       "\nIf you are in more than one other event, you will have to choose which event's availability to reuse.",
                       inline=False)
instructions.add_field(name="Unsubscribe Button",
                       value="Unsubscribe from the event."
                       "\nYou will still be a participant, but you will not be mentioned.",
                       inline=False)
instructions.add_field(name="Cancel Button",
                       value="Cancel scheduling of the event.",
                       inline=False)
instructions.add_field(name="General Information",
                       value="The event will be either created or cancelled within a minute after the last person responds.",
                       inline=False)
HELP_EMBEDS.append(instructions)
# Other Buttons
other_buttons = Embed(title="Other Buttons",
                      description="How to utilize other buttons:",
                      color=Color.magenta())
other_buttons.add_field(name="Start End Button",
                        value="Starts an event and converts itself to an End button to end the event."
                        "\nEvents will start and end automatically when all participants join or leave the voice channel.",
                        inline=False)
other_buttons.add_field(name="Schedule Again",
                        value="Allows you to reuse data from an ended or cancelled event."
                        "\nThe modal will allow you to modify the following event details:"
                        "\nName"
                        "\nDuration"
                        "\nImage URL"
                        "\nStart Time"
                        "\nLeaving the Start Time field blank will start scheduling the event."
                        "\nEntering a Start Time will create the event at that time."
                        "\nEvent voice channel and participants are recycled.",
                        inline=False)
HELP_EMBEDS.append(other_buttons)
