# Event Scheduler

A Discord bot that finds a time that works for everyone and turns it into a Discord server event. Start scheduling in a text channel, and the bot asks everyone involved for their availability. Once everyone has answered, it picks the earliest time when every participant is free, creates the event, reminds people before it starts, and starts and ends the event as people join and leave the voice channel. Events can also take place somewhere other than a voice channel, such as a physical address.

## Contents

- [Setup](#setup)
- [Using the bot](#using-the-bot)
- [Entering availability](#entering-availability)
- [Managing a scheduled event](#managing-a-scheduled-event)
- [Owner-only commands](#owner-only-commands)
- [Scheduling from another program](#scheduling-from-another-program)
- [Files and logs](#files-and-logs)

---

## Setup

### Requirements

- Python 3.10 or newer
- A Discord application with a bot user

### 1. Create the Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a **New Application**.
2. Open the **Bot** tab and click **Reset Token** to get the bot token. Keep it secret.
3. On the same tab, under **Privileged Gateway Intents**, turn on **Server Members Intent**. The bot won't connect without it. It doesn't use the Presence or Message Content intents, so those can stay off.
4. Open **OAuth2 → URL Generator**, select the `bot` and `applications.commands` scopes, then select these bot permissions:
   - View Channels
   - Send Messages
   - Embed Links
   - Read Message History
   - Create Events and Manage Events
   - Connect and Speak, for [voice announcements](#voice-announcements)
5. Open the generated URL and add the bot to your server.

### 2. Install the dependencies

```bash
git clone <this repository>
cd Scheduler
pip install -r requirements.txt
```

[Voice announcements](#voice-announcements) also need `ffmpeg` and `libopus` on the host, plus the text-to-speech engines you want to offer:

```bash
sudo apt install ffmpeg libopus0 espeak-ng   # espeak-ng
pip install piper-tts                        # piper, which also needs a voice model in PIPER_MODEL_EN or PIPER_MODEL_JP
sudo apt install python3-dev cmake build-essential   # needed to build pyopenjtalk
pip install pyopenjtalk                      # Japanese piper voices
```

`pip install pyopenjtalk` compiles it from source, which needs Python's development headers, `cmake`, and a C++ compiler. `python3-dev` matches the system's default Python. If the bot runs on a different Python version, install that version's headers instead, such as `python3.12-dev`.

### 3. Configure `.env`

Create a file called `.env` in the project folder:

```env
DISCORD_TOKEN=your-bot-token
OWNER_ID=your-discord-user-id
HOST=127.0.0.1
PORT=5000
```

| Variable        | Purpose |
|-----------------|---------|
| `DISCORD_TOKEN` | The bot token from the Developer Portal. |
| `OWNER_ID`      | Your Discord user ID. This user can run the [owner-only commands](#owner-only-commands). To copy your ID, enable Developer Mode under *Settings → Advanced*, then right-click your name and choose **Copy User ID**. |
| `HOST`, `PORT`  | Where the bot listens for [scheduling requests from other programs](#scheduling-from-another-program). Use `127.0.0.1` to accept connections from the same machine only. All four variables are required. |
| `PIPER_MODEL_EN`, `PIPER_MODEL_JP` | Optional. The full paths to the `.onnx` files of the piper voice models for the `piper-en` (English) and `piper-jp` (Japanese) [voice announcements](#voice-announcements). Download one with `python3 -m piper.download_voices en_US-lessac-medium` (or `ja_JP-hi_fi_captain-medium`), keeping its `.onnx.json` file next to it. Samples of every voice are at [rhasspy.github.io/piper-samples](https://rhasspy.github.io/piper-samples/). |

### 4. Run the bot

```bash
python3 scheduler.py
```

When the bot's status changes to *Watching for event scheduling commands*, it's ready. On startup, it registers its slash commands with Discord. Newly registered commands can take a few minutes to show up in your server.

To stop the bot, press `Ctrl+C` or send it `SIGTERM`. Before it exits, it saves every event and disables the buttons on its messages. On the next start, it reloads the saved events and continues where it left off.

> **Timezone:** Typed times are read as Eastern Time, and the bot uses the host machine's clock. Run the bot on a machine set to Eastern Time so the times line up.

---

## Using the bot

Run all commands in a server text channel. The bot posts event messages in the channel where you run the command.

### `/schedule`: find a time that works for everyone

This is the main command. The bot posts an availability message in the channel and mentions every participant. Each participant enters when they're free. Within about a minute of the last person answering, the bot creates the event at the earliest time when everyone is available. If no time works, it cancels scheduling.

| Option            | Required | Description |
|-------------------|----------|-------------|
| `event_name`      | Yes | The event's name, up to 100 characters. Two events can't have the same name at the same time. |
| `location`        | Yes | Where the event takes place. The voice and stage channels appear as suggestions, so press Enter to pick the first one. Or type any other location, such as an address, up to 100 characters. See [Locations](#locations). |
| `image_url`       | No  | An image for the Discord event and the bot's messages. |
| `include_exclude` | No  | Whether `usernames` and `roles` add people (`INCLUDE`, the default) or leave people out (`EXCLUDE`). |
| `usernames`       | No  | Comma-separated usernames or user IDs to include or exclude. |
| `roles`           | No  | Comma-separated role names to include or exclude. |
| `duration`        | No  | The length in minutes, up to 1440 (24 hours). The default, `0`, sets the length to however long everyone is available. |
| `multi_event`     | No  | Creates one event on each day everyone is available, instead of a single event. |
| `timeout`         | No  | The number of days to wait for responses before giving up, from 1 to 30. The default is 7. |

**Who gets invited:** You're always a participant. If you don't set `usernames` or `roles`, everyone who can see the text channel is invited. Bots are never invited. If you set `roles`, `usernames` is ignored.

**Examples:**

```
/schedule event_name:Game Night location:General
/schedule event_name:Raid location:Raids roles:Raiders
/schedule event_name:Movie location:Theater include_exclude:EXCLUDE usernames:alice, bob
/schedule event_name:Board Games location:123 Main St
```

### Locations

The `location` option suggests the server's voice channels (🔊) and stage channels (🎙️) as you type. Picking one, or typing a channel's exact name, puts the event in that channel. Anything else becomes an external location, and the Discord event is created as a "somewhere else" event.

Stage channels work like voice channels below. When the event starts, the bot opens the stage with the event's name as its topic, and closes it when the event ends. If the stage is already live, or the bot can't open it, the event starts without opening the stage. Opening and closing stages requires the bot to be a stage moderator in that channel.

The location changes how the event starts and ends:

| | Voice channel | Other location |
|---|---|---|
| **Starts** | When every participant is in the voice channel, from 15 minutes before the start time | At the start time |
| **Ends** | When everyone has left the voice channel, or when at least half of 4 or more attendees (or all but 5 of more than 10) have been gone for over 5 minutes | When its duration has passed |
| **Start Event button** | You must be in the voice channel | Any participant can press it |

Either way, events at the same location can't overlap. For typed locations, "the same location" means the same text, ignoring capitalization.

### `/create`: create an event at a time you choose

Use this command when you already know the time. It takes the same options as `/schedule`, plus:

| Option       | Required | Description |
|--------------|----------|-------------|
| `start_time` | Yes | A time such as `2200`, or a full ISO date and time such as `2026-10-04T22:00`. |
| `duration`   | No  | The length in minutes, up to 1440 (24 hours). The default is 30. |

### Other commands

| Command         | What it does |
|-----------------|--------------|
| `/edit`         | Change an event in the current channel. You can change its name, location, image, duration, multi-event setting, or timeout. If the channel has more than one event, pick one from a dropdown. |
| `/attach`       | Have the bot manage a Discord event that was created outside the bot. Pick the event from a dropdown. |
| `/availability` | Show everyone's availability for an event in the current channel. |
| `/listevents`   | List every event the bot is managing in this server. |
| `/listmyevents` | List every event you're a participant in. |
| `/cutoff`       | Set how many hours past midnight your **Full Availability** extends to, from -6 to 23. Negative numbers end it before midnight to match an earlier bedtime, so `-2` means 10 PM. The default is 0, which means midnight. Leave the number blank to go back to the default. The setting is yours alone and applies in every server. It's deleted if you no longer share a server with the bot. |
| `/tts`          | Choose the text-to-speech engine for this server's [voice announcements](#voice-announcements): `espeak-ng`, `piper-en`, `piper-jp`, or `none` to turn them off. The piper options show their voice's language. Anyone can change it, and the bot posts who changed it in the channel. |
| `/help`         | Show the built-in help. |

### Voice announcements

When `/tts` is set to an engine, the bot joins an event's voice or stage channel and says "The event *name* is starting now." when the event starts, and "The event *name* is ending now." when it ends. Then it leaves. Announcements are off until someone in the server turns them on.

- The bot can only be in one voice channel per server, so announcements in a server are read out one at a time.
- Nothing is read out to an empty channel, so there is no ending announcement when everyone has left.
- Events at a [typed location](#locations) have no voice channel and aren't announced.
- Japanese piper voices say 「イベント「*name*」が今から始まります。」 and 「イベント「*name*」が終わります。」 instead. Voices in other languages use the English sentences.
- With an English and a Japanese piper voice set up, each part of an announcement is read by the voice for its characters, whichever voice is selected. An English event name in the Japanese sentence is read by the English voice, and a Japanese event name in the English sentence (or with `espeak-ng`) is read by the Japanese voice. This takes about a second longer to prepare, before the bot joins the channel.
- With only one voice set up, it reads everything. The Japanese voice spells out English words it doesn't know letter by letter, and English voices read Japanese characters as "Japanese letter".
- If the host is missing something the engine needs, `/tts` says what it is instead of switching to it. Announcements that fail are skipped, and the event starts or ends as usual.

### Adding an image later

Send the bot a direct message that includes the event's name and an image attachment. If the event has been created, the bot updates the Discord event's image. If not, it uses the image once the event is created.

---

## Entering availability

The availability message has these buttons:

| Button | What it does |
|--------|--------------|
| **Respond** | Opens a form for entering when you're free. |
| **Full Availability (Today)** | Marks you as available from now until midnight, or the time you set with `/cutoff`. If that time has already passed today, the bot tells you instead. If someone else enters availability later than that, yours is extended to match. |
| **Use Existing Availability** | Copies your availability from another event you're in. If you're in more than one, choose which event to copy from. |
| **Unsubscribe / Resubscribe** | Stops the bot from mentioning you. You're still a participant. Press it again to resubscribe. Your status shows in the availability message. |
| **Cancel Scheduling** | Cancels the event, with an optional reason. |

### The Respond form

- **Date:** The day you're entering times for, as `YYYY-MM-DD`. It defaults to today. You can leave off the year (`MM-DD`) to mean the next time that date comes around, or enter just the day of the month (`DD`).
- **Timeslot:** The time ranges you're free. Separate multiple ranges with commas.
- **Timezone:** The timezone you typed your times in. Supported values: `AT`, `ET`, `CT`, `MT`, `PT`, and their standard and daylight forms, such as `EST` or `PDT`. Times are converted to Eastern Time.
- **Note:** An optional note that appears next to your availability. If you leave it blank when you submit again, your note is cleared.

Time ranges accept several formats:

| You type | Meaning |
|----------|---------|
| `9-12` | 9:00 AM to 12:00 PM (hours use 24-hour time) |
| `8-11, 13-17` | Two separate ranges |
| `1pm-3pm` | 1:00 PM to 3:00 PM |
| `15:30-17` | 3:30 PM to 5:00 PM |
| `18-22x3` | 6:00 PM to 10:00 PM on the chosen date and the next two days |
| `full` | Available all day |
| `clear` | Remove your availability for the chosen date |
| `clear x3` | Remove your availability for the chosen date and the next two days |
| `none` | Remove all of your availability |

If you're in several events, time taken up by a created event is automatically removed from your availability in the other events. It's restored if that event is cancelled or ends.

---

## Managing a scheduled event

After the event is created, the bot posts a message with these buttons:

| Button | What it does |
|--------|--------------|
| **Start Event / End Event** | Starts the event manually, then changes to an End button. It can be pressed from 30 minutes before the start time. Events also start automatically when every participant joins the voice channel, and end when everyone leaves, or when at least half of 4 or more attendees (or all but 5 of more than 10) have been gone for over 5 minutes. Events at a [typed location](#locations) start at their start time and end when their duration has passed. |
| **End and Forget** | Ends the event, and the bot stops tracking it. |
| **Unsubscribe / Resubscribe** | Stops the bot from mentioning you about this event. Press it again to resubscribe. Joining the voice channel while the event is running also resubscribes you. |
| **Reschedule Event** | Restarts availability collection with the same participants. |
| **Cancel Event** | Cancels the event. |

The bot mentions participants 15 minutes before the event starts.

If an event hasn't started by an hour after its scheduled end time, the bot cancels it. For a multi-event, only that occurrence is cancelled and the rest stay scheduled.

### Schedule Again

When an event ends or is cancelled, the bot shows a **Schedule Again** button for about a week. It reuses the location and participants, and lets you change the name, duration, image URL, and start time:

- Leave **Start Time** blank to start collecting availability again, as with `/schedule`.
- Enter a **Start Time** to create the event at that time, as with `/create`.

Click **Forget** to dismiss the button.

---

## Owner-only commands

Only the user set as `OWNER_ID` can run these. Send them to the bot as direct messages. They're ignored in server channels.

| Message | What it does |
|---------|--------------|
| `scheduler: events` | List every event the bot is tracking, in all servers, with its status. |
| `scheduler: debug` | Turn debug logging on or off. |

---

## Scheduling from another program

The bot listens for TCP connections on `HOST:PORT` from `.env`. Send a JSON object whose `type` says what to do.

### `"type": "schedule"`: find a time that works for everyone

Starts scheduling an event, just as `/schedule` would:

```json
{
  "type": "schedule",
  "name": "Game Night",
  "guild_id": 123456789012345678,
  "text_channel_id": 123456789012345678,
  "voice_channel_id": 123456789012345678,
  "location": null,
  "scheduler_id": 123456789012345678,
  "image_url": null,
  "include_exclude": "INCLUDE",
  "usernames": null,
  "roles": null,
  "duration": 0,
  "multi_event": false
}
```

A packet without a `type` is treated as `schedule`.

### `"type": "create"`: create an event at a time you choose

Creates the event right away, just as `/create` would:

```json
{
  "type": "create",
  "name": "Range Night",
  "guild_id": 123456789012345678,
  "text_channel_id": 123456789012345678,
  "voice_channel_id": null,
  "location": "123 Main St",
  "scheduler_id": 123456789012345678,
  "start_time": "2026-10-11T19:00:00-04:00",
  "image_url": null,
  "include_exclude": "INCLUDE",
  "usernames": "123456789012345678, 234567890123456789",
  "roles": null,
  "duration": 60
}
```

`start_time` takes the same formats as `/create`: a full ISO date and time (an offset such as `-04:00` is recommended), or a time such as `2200`.

### Both types

Every field is required except `location` and `voice_channel_id`. Set `location` to a string, such as an address, to hold the event there instead of in a voice channel. Without either one, the event uses the server's first voice channel. The bot replies `valid` if the request was accepted, `invalid JSON` if the message couldn't be parsed, or `error: <message>` if scheduling failed or the `type` is unknown.

Example in Python:

```python
import json, socket

with socket.create_connection(("127.0.0.1", 5000)) as s:
    s.sendall(json.dumps(payload).encode())
    print(s.recv(1024).decode())
```

The server has no authentication. Keep `HOST` set to `127.0.0.1`, or put it behind a firewall.

---

## Files and logs

| File | Contents |
|------|----------|
| `data.json` | Saved events, rewritten every second. The bot reads it on startup to resume events. To start fresh, delete it while the bot is stopped. |
| `scheduler.log` | The full log. The same output is printed to the console. |

## Running the tests

```bash
pip install pytest
python3 -m pytest
```
