from discord import Embed, Color


# Each /help topic is one embed, keyed by the value of its choice in the command
HELP_TOPICS: dict[str, Embed] = {}


def _topic(key: str, title: str, description: str, color: Color, fields: list[tuple[str, str]]) -> Embed:
    embed = Embed(title=title, description=description, color=color)
    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)
    HELP_TOPICS[key] = embed
    return embed


_topic("overview", "Event Scheduler Help",
       "The Event Scheduler finds a time that works for everyone and turns it into a Discord server event."
       " Start scheduling in a text channel and the bot asks everyone involved for their availability."
       " Once everyone has answered, it creates the event at the earliest time everyone is free, reminds people before it starts,"
       " and starts and ends it as people join and leave the voice channel."
       "\n\nRun commands in a server text channel. The bot posts its messages in the channel you run them in."
       "\nTyped times are in Eastern Time unless you set a timezone.",
       Color.blurple(),
       [("Topics",
         "Pick a topic with `/help topic:` to read more."
         "\n**Scheduling:** /schedule, /create, and who gets invited."
         "\n**Locations:** voice channels, stage channels, and other places."
         "\n**Other Commands:** /edit, /attach, /availability, /listevents, /listmyevents, /cutoff, and /tts."
         "\n**Availability Buttons:** the buttons on the availability message."
         "\n**Entering Availability:** the Enter Availability form and time formats."
         "\n**Managing Events:** the buttons on a created event, reminders, and Schedule Again."
         "\n**Voice Announcements:** how events are announced in voice channels.")])

_topic("scheduling", "Scheduling",
       "There are two ways to make an event: let the bot find a time with /schedule, or pick one yourself with /create.",
       Color.blurple(),
       [("/schedule",
         "Posts an availability message in the channel and mentions every participant so they can enter when they're free."
         "\nWithin about a minute of the last person answering, the bot creates the event at the earliest time everyone is available,"
         " starting no sooner than 30 minutes from then so people have time to show up. If no time works, scheduling is cancelled."),
        ("/schedule options",
         "**event_name:** The event's name, up to 100 characters. Two events can't have the same name at the same time."
         "\n**location:** A voice or stage channel, or a typed location such as an address. See the Locations topic."
         "\n**image_url:** An image for the Discord event and the bot's messages."
         "\n**include_exclude:** Choose users or roles from dropdowns to add (INCLUDE) or leave out (EXCLUDE)."
         "\n**duration:** The length in minutes, up to 1440 (24 hours). The default, 0, sets the length to however long everyone is available."
         "\n**multi_event:** Creates one event on each day everyone is available, instead of a single event."
         "\n**timeout:** How many days to wait for responses before giving up, from 1 to 30. The default is 7."),
        ("/create",
         "Creates the event right away at a time you choose. It takes the same options as /schedule except multi_event and timeout, plus:"
         "\n**start_time:** A time such as 2200, or a full ISO date and time such as 2026-10-04T22:00. A time already past moves to the next day."
         "\n**duration:** The length in minutes, from 1 to 1440. The default is 30."),
        ("Who gets invited",
         "You're always a participant. Without include_exclude, everyone who can see the text channel is invited."
         "\nBots are only invited if you pick one from the users dropdown. In smaller channels the dropdown doesn't list bots or you."
         "\nIf you don't choose any users or roles within 5 minutes, the command is cancelled."),
        ("Examples",
         "`/schedule event_name:Game Night location:General`"
         "\n`/schedule event_name:Movie location:Theater include_exclude:EXCLUDE`"
         "\n`/create event_name:Board Games location:123 Main St start_time:1900`")])

_topic("locations", "Locations",
       "The location option suggests the server's voice channels (🔊) and stage channels (🎙️) as you type."
       " Picking one, or typing a channel's exact name, puts the event in that channel."
       " Anything else becomes a typed location, and the Discord event is created as a \"somewhere else\" event.",
       Color.dark_teal(),
       [("Voice channels",
         "**Starts:** When every participant is in the voice channel, from 15 minutes before the start time."
         "\n**Ends:** When everyone has left, or when at least half of 4 or more attendees (or all but 5 of more than 10) have been gone for over 5 minutes."
         "\n**Start Event button:** You must be in the voice channel to press it."
         "\nEvents in the same voice channel can't overlap."),
        ("Stage channels",
         "Work like voice channels. When the event starts, the bot opens the stage with the event's name as its topic, and closes it when the event ends."
         "\nIf the stage is already live, or the bot can't open it, the event starts without opening the stage."
         " Opening and closing stages requires the bot to be a stage moderator in that channel."),
        ("Typed locations",
         "**Starts:** At the start time."
         "\n**Ends:** When its duration has passed."
         "\n**Start Event button:** Any participant can press it."
         "\nEvents at the same typed location can overlap, such as separate groups at a range."
         " They have no voice channel, so they aren't announced.")])

_topic("commands", "Other Commands",
       "Commands for managing events and your settings. For /schedule and /create, see the Scheduling topic.",
       Color.blue(),
       [("/edit",
         "Change an event in the current channel: its name, location, image, duration, multi-event setting, or timeout."
         "\nIf the channel has more than one event, pick one from a dropdown."
         "\nChanging the image of a created event updates the Discord event. Before it's created, the image is used once it is."
         "\nA created event's duration can be lengthened up to the end of its participants' common availability, the start of another event they're in, and the start of the next event in the same voice channel."),
        ("/attach",
         "Have the bot manage a Discord event that was created outside the bot. The event option suggests the server's events the bot isn't managing yet."
         "\nOther events with the same name and location are added as more occurrences of it."
         " If the bot already has an event with that name and location, the Discord event is added to it. Otherwise, everyone who can see the channel is a participant."),
        ("/availability",
         "Show everyone's availability for an event in the current channel."),
        ("/listevents",
         "List every event the bot is managing in this server."),
        ("/listmyevents",
         "List every event you're a participant in."),
        ("/cutoff",
         "Set how many hours past midnight your Full Availability extends to, from -6 to 23. The default is 0, which means midnight."
         "\nNegative numbers end it before midnight to match an earlier bedtime, so -2 means 10 PM."
         "\nHours count from midnight Eastern Time, since Discord doesn't tell bots your timezone."
         " Outside Eastern Time, add your offset so it ends at your midnight: 1 for Central, 2 for Mountain, 3 for Pacific, or -1 for Atlantic."
         "\nLeave the number blank to go back to the default. The setting is yours alone and applies in every server."),
        ("/tts",
         "Choose the voice that announces events starting and ending in their voice channels: espeak-ng, piper-en, piper-jp, or none to turn announcements off."
         "\nThe setting is for the whole server unless you set scope to This channel, which gives events made in this channel their own voice without changing the server's."
         " Pick server's voice with This channel to go back to the server's voice. Anyone can change it, and the bot posts who changed it in the channel."
         "\nSee the Voice Announcements topic."),
        ("/help",
         "Show this help. Pick a topic to read about it.")])

_topic("buttons", "Availability Buttons",
       "While an event is being scheduled, its availability message shows everyone's availability with these buttons below it.",
       Color.purple(),
       [("Enter Availability",
         "Opens a form for entering when you're free. See the Entering Availability topic."),
        ("Full Availability (Today)",
         "Marks you as available from now until midnight Eastern Time, or the time you set with /cutoff."
         "\nDiscord shows times in your own timezone, so outside Eastern Time this can look like it ends early, e.g. at 11 PM in Central Time."
         " Use /cutoff to move it to your midnight."
         "\nIf that time has already passed today, or is too soon to fit the event starting at least 30 minutes from now, the bot tells you instead."
         "\nIf someone else enters availability later than that, yours is extended to match."),
        ("Copy From Other Event",
         "Copies your availability from another event you're in. If you're in more than one, choose which event to copy from."),
        ("Unsubscribe / Resubscribe",
         "Stops the bot from mentioning you, and you no longer need to answer for the event to be created."
         " You're still a participant. Press it again to resubscribe. Your status shows in the availability message."),
        ("Cancel Scheduling",
         "Cancels the event, with an optional reason."),
        ("When the event is created",
         "The event is created or cancelled within about a minute of the last person answering."
         " If not everyone answers before the timeout (7 days by default), scheduling is cancelled.")])

_topic("availability", "Entering Availability",
       "The Enter Availability button opens a form with these fields.",
       Color.purple(),
       [("Date",
         "The day you're entering times for, as YYYY-MM-DD (e.g. 2026-10-31). It defaults to today."
         "\nLeave off the year (MM-DD) for the next time that date comes around, or enter just the day of the month (DD)."),
        ("Timeslot",
         "The time ranges you're free. Hours use 24-hour time unless you add am or pm."
         "\n`9-12`: 9:00 AM to 12:00 PM"
         "\n`8-11, 13-17`: Two separate ranges"
         "\n`1pm-3pm`: 1:00 PM to 3:00 PM"
         "\n`15:30-17`: 3:30 PM to 5:00 PM"
         "\n`18-22x3`: 6:00 PM to 10:00 PM on the chosen date and the next two days"
         "\n`full`: Available all day"
         "\n`clear`: Remove your availability for the chosen date"
         "\n`clear x3`: Remove it for the chosen date and the next two days"
         "\n`none`: Remove all of your availability"),
        ("Timezone",
         "The timezone you typed your times in. Times are converted to Eastern Time."
         "\nSupported: AT, ET, CT, MT, PT, and their standard and daylight forms, such as EST or PDT."
         "\nThe form fills in the timezone you last used, starting with ET."
         "\nWhen clocks go back, a time in the repeated hour is the first, daylight one. Add the standard form, such as EST, for the second."
         " A time skipped when clocks go forward moves forward with them, so 2:30 becomes 3:30."),
        ("Note",
         "An optional note shown next to your availability. Leaving it blank when you submit again clears your note."),
        ("Skipped times",
         "Times that end too soon to fit the event, which starts at least 30 minutes from now, aren't saved."
         " The bot tells you which ones it skipped, and whether you still need to respond."),
        ("Other events",
         "If you're in several events, time taken up by a created event is removed from your availability in the others."
         " It's restored if that event is cancelled or ends.")])

_topic("managing", "Managing Events",
       "Once an event is created, the bot posts a message with these buttons, and mentions participants 15 minutes before it starts.",
       Color.magenta(),
       [("Start Event / End Event",
         "Starts the event, then changes to an End button. It can be pressed from 30 minutes before the start time."
         "\nVoice channel events also start automatically when every participant joins, and end when everyone leaves,"
         " or when at least half of 4 or more attendees (or all but 5 of more than 10) have been gone for over 5 minutes."
         "\nTyped location events start at their start time and end when their duration has passed."
         "\nIf the previous event in the same channel is still going and this one is scheduled right after it,"
         " a participant of both can press Start to end the previous event and start this one, after confirming."),
        ("End and Forget",
         "Ends the event, and the bot stops tracking it."),
        ("Unsubscribe / Resubscribe",
         "Stops the bot from mentioning you about this event. Press it again to resubscribe."
         "\nJoining the voice channel while the event is running resubscribes you, so you can't unsubscribe while you're in it."),
        ("Reschedule Event",
         "Starts collecting availability again with the same participants."),
        ("Cancel Event",
         "Cancels the event."),
        ("Events that never start",
         "If an event hasn't started by an hour after its scheduled end time, the bot cancels it."
         " For a multi-event, only that occurrence is cancelled and the rest stay scheduled."),
        ("Schedule Again",
         "When an event ends or is cancelled, a Schedule Again button shows for about a week."
         " It reuses the location and participants, and lets you change the name, duration, image URL, and start time."
         "\nLeave Start Time blank to collect availability again, like /schedule."
         "\nEnter a Start Time to create the event at that time, like /create. A Duration of 0 or blank then uses 30 minutes."
         "\nPress Forget to dismiss the button.")])

_topic("announcements", "Voice Announcements",
       "When /tts is set to a voice, the bot joins an event's voice or stage channel and says \"The event *name* is starting now.\""
       " when it starts, and \"The event *name* is ending now.\" when it ends. Then it leaves."
       " Announcements are off until someone in the server turns them on.",
       Color.gold(),
       [("Voices",
         "**espeak-ng:** A basic synthesized voice."
         "\n**piper-en / piper-jp:** More natural voices, if the bot's host has set them up. The /tts options show each one's language."
         "\n**none:** Turns announcements off."
         "\nJapanese voices say 「イベント「*name*」が今から始まります。」 and 「イベント「*name*」が終わります。」 instead."),
        ("Mixed languages",
         "With both an English and a Japanese voice set up, each part of an announcement is read by the voice for its characters, whichever voice is selected."
         " So a Japanese event name in an English announcement is read by the Japanese voice."
         "\nWith only one voice, it reads everything. The Japanese voice spells out English words it doesn't know, and English voices read Japanese characters as \"Japanese letter\"."),
        ("When nothing is announced",
         "The bot can only be in one voice channel per server, so announcements in a server are read out one at a time."
         "\nNothing is read out to an empty channel, so there's no ending announcement when everyone has left."
         "\nEvents at a typed location have no voice channel and aren't announced."
         "\nIf the host is missing something a voice needs, /tts says what instead of switching to it."
         " Announcements that fail are skipped, and the event starts or ends as usual.")])
