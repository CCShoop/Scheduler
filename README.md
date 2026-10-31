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
python3 -m pip install piper-tts             # piper, which also needs a voice model in PIPER_MODEL_EN or PIPER_MODEL_JP
sudo apt install python3-dev cmake build-essential   # needed to build pyopenjtalk
python3 -m pip install pyopenjtalk           # Japanese piper voices
```

Each piper voice needs all of these:

1. **piper-tts**, installed for the Python that runs the bot.
2. **Both of the voice's files in the same folder:** the model (`en_US-lessac-medium.onnx`) and its config (`en_US-lessac-medium.onnx.json`). `python3 -m piper.download_voices` downloads both. If you download from Hugging Face, get the `.onnx.json` file too.
3. **`PIPER_MODEL_EN` or `PIPER_MODEL_JP` in `.env`**, set to the `.onnx` file. A full path is safest. A relative path such as `piper/en_US-lessac-medium.onnx` is read from the folder the bot is started in. A `piper/` folder in the project is ignored by git.
4. **For Japanese voices, pyopenjtalk**, installed for the same Python.
5. **ffmpeg, libopus, and voice support** (`discord.py[voice]`, from `requirements.txt`), which every engine needs.
6. **Connect and Speak permissions** for the bot in the event channels.

`/tts` checks 1 to 5 before switching, and says what's missing. Missing permissions only show up as errors in the log when an announcement fails. So does a missing pyopenjtalk, if piper runs from a separate install on `PATH` instead of the bot's Python.

Install piper-tts and pyopenjtalk with the same Python that runs the bot, which is why these use `python3 -m pip`. The bot runs piper through that Python, so the `piper` command doesn't need to be on `PATH`. If `/tts` says piper is missing, it names the Python the bot is running, and you can install piper-tts with that one.

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

> **Timezone:** Typed times are read as Eastern Time, and the bot uses the host machine's clock. Run the bot on a machine set to Eastern Time so the times line up. Discord doesn't tell bots what timezone a user is in, so Full Availability ends at midnight Eastern Time for everyone unless they change it with [`/cutoff`](#other-commands).

---

## Using the bot

Run all commands in a server text channel. The bot posts event messages in the channel where you run the command.

### `/schedule`: find a time that works for everyone

This is the main command. The bot posts an availability message in the channel and mentions every participant. Each participant enters when they're free. Within about a minute of the last person answering, the bot creates the event at the earliest time when everyone is available, starting no sooner than 30 minutes from then so people have time to show up. If no time works, it cancels scheduling.

| Option            | Required | Description |
|-------------------|----------|-------------|
| `event_name`      | Yes | The event's name, up to 100 characters. Two events can't have the same name at the same time. |
| `location`        | Yes | Where the event takes place. The voice and stage channels appear as suggestions, so press Enter to pick the first one. Or type any other location, such as an address, up to 100 characters. See [Locations](#locations). |
| `image_url`       | No  | An image for the Discord event and the bot's messages. |
| `include_exclude` | No  | Sends you dropdowns of the text channel's members and their roles to add (`INCLUDE`) or leave out (`EXCLUDE`). Choose from either one. |
| `duration`        | No  | The length in minutes, up to 1440 (24 hours). The default, `0`, sets the length to however long everyone is available. |
| `multi_event`     | No  | Creates one event on each day everyone is available, instead of a single event. |
| `timeout`         | No  | The number of days to wait for responses before giving up, from 1 to 30. The default is 7. |

**Who gets invited:** You're always a participant. If you don't set `include_exclude`, everyone who can see the text channel is invited. Bots are only invited if you pick one from the users dropdown. In smaller channels the dropdown doesn't list bots or you. If you don't choose any users or roles within 5 minutes, the command is cancelled.

**Examples:**

```
/schedule event_name:Game Night location:General
/schedule event_name:Movie location:Theater include_exclude:EXCLUDE
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

Events in the same voice or stage channel can't overlap. Events at the same typed location can, such as separate groups at a range.

### `/create`: create an event at a time you choose

Use this command when you already know the time. It takes the same options as `/schedule`, plus:

| Option       | Required | Description |
|--------------|----------|-------------|
| `start_time` | Yes | A time such as `2200`, or a full ISO date and time such as `2026-10-04T22:00`. |
| `duration`   | No  | The length in minutes, from 1 to 1440 (24 hours). The default is 30. |

### Other commands

| Command         | What it does |
|-----------------|--------------|
| `/edit`         | Change an event in the current channel. You can change its name, location, image, duration, multi-event setting, or timeout. If the channel has more than one event, pick one from a dropdown. |
| `/attach`       | Have the bot manage a Discord event that was created outside the bot. Pick the event from a dropdown. |
| `/availability` | Show everyone's availability for an event in the current channel. |
| `/listevents`   | List every event the bot is managing in this server. |
| `/listmyevents` | List every event you're a participant in. |
| `/cutoff`       | Set how many hours past midnight your **Full Availability** extends to, from -6 to 23. Negative numbers end it before midnight to match an earlier bedtime, so `-2` means 10 PM. The default is 0, which means midnight. Hours are counted from midnight Eastern Time, the bot's timezone, because Discord doesn't tell bots what timezone you're in. If you live somewhere else, add your offset from Eastern Time so it ends at your midnight: `1` for Central, `2` for Mountain, `3` for Pacific, or `-1` for Atlantic. Leave the number blank to go back to the default. The setting is yours alone and applies in every server. It's deleted if you no longer share a server with the bot. |
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
| **Enter Availability** | Opens a form for entering when you're free. |
| **Full Availability (Today)** | Marks you as available from now until midnight Eastern Time, or the time you set with `/cutoff`. Discord shows times in your own timezone, so outside Eastern Time this can look like it ends early, e.g. at 11 PM in Central Time. Use `/cutoff` to move it to your midnight. If that time has already passed today, or is too soon to fit the event starting at least 30 minutes from now, the bot tells you instead. If someone else enters availability later than that, yours is extended to match. |
| **Copy From Other Event** | Copies your availability from another event you're in. If you're in more than one, choose which event to copy from. |
| **Unsubscribe / Resubscribe** | Stops the bot from mentioning you. You're still a participant. Press it again to resubscribe. Your status shows in the availability message. |
| **Cancel Scheduling** | Cancels the event, with an optional reason. |

### The Enter Availability form

- **Date:** The day you're entering times for, as `YYYY-MM-DD`. It defaults to today. You can leave off the year (`MM-DD`) to mean the next time that date comes around, or enter just the day of the month (`DD`).
- **Timeslot:** The time ranges you're free. Separate multiple ranges with commas.
- **Timezone:** The timezone you typed your times in. Supported values: `AT`, `ET`, `CT`, `MT`, `PT`, and their standard and daylight forms, such as `EST` or `PDT`. Times are converted to Eastern Time. The form fills in the timezone you last used, starting with `ET`.
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

Times that end too soon to fit the event, which starts at least 30 minutes from now, aren't saved. The bot tells you which ones it skipped, and whether you still need to respond.

If you're in several events, time taken up by a created event is automatically removed from your availability in the other events. It's restored if that event is cancelled or ends.

---

## Managing a scheduled event

After the event is created, the bot posts a message with these buttons:

| Button | What it does |
|--------|--------------|
| **Start Event / End Event** | Starts the event manually, then changes to an End button. It can be pressed from 30 minutes before the start time. Events also start automatically when every participant joins the voice channel, and end when everyone leaves, or when at least half of 4 or more attendees (or all but 5 of more than 10) have been gone for over 5 minutes. Events at a [typed location](#locations) start at their start time and end when their duration has passed. |
| **End and Forget** | Ends the event, and the bot stops tracking it. |
| **Unsubscribe / Resubscribe** | Stops the bot from mentioning you about this event. Press it again to resubscribe. Joining the voice channel while the event is running also resubscribes you, so you can't unsubscribe while you're in it. |
| **Reschedule Event** | Restarts availability collection with the same participants. |
| **Cancel Event** | Cancels the event. |

The bot mentions participants 15 minutes before the event starts.

If an event hasn't started by an hour after its scheduled end time, the bot cancels it. For a multi-event, only that occurrence is cancelled and the rest stay scheduled.

### Schedule Again

When an event ends or is cancelled, the bot shows a **Schedule Again** button for about a week. It reuses the location and participants, and lets you change the name, duration, image URL, and start time:

- Leave **Start Time** blank to start collecting availability again, as with `/schedule`.
- Enter a **Start Time** to create the event at that time, as with `/create`. A **Duration** of `0` or blank uses 30 minutes, since an automatic length only works when collecting availability.

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

Another program, such as your own bot, can start events by sending JSON to the bot over TCP. The bot listens on `HOST:PORT` from `.env`. Send one JSON object per request, and the bot replies with a short text message.

The server has no authentication. Keep `HOST` set to `127.0.0.1`, or put it behind a firewall.

### Packet types

The `type` field says what to do:

| `type` | Does the same as | Extra fields |
|--------|------------------|--------------|
| `"schedule"` | [`/schedule`](#schedule-find-a-time-that-works-for-everyone): asks everyone for their availability, then creates the event when a time works | `multi_event` |
| `"create"` | [`/create`](#create-create-an-event-at-a-time-you-choose): creates the event right away at `start_time` | `start_time` |

A packet without a `type` is treated as `"schedule"`.

### Fields

Every field must be present, though some can be `null`. The exceptions are `type`, `location`, `voice_channel_id` and `scheduler_id`, which can be left out.

Discord ids can be numbers or strings of digits. Use strings if your program is in JavaScript, since it can't hold ids as numbers without losing digits.

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | The event's name, up to 100 characters. It can't match another event the bot is tracking. |
| `guild_id` | id | The server to hold the event in. The bot must be in it. |
| `text_channel_id` | id | The text channel for the bot's messages. Who can see this channel decides who can be invited. |
| `voice_channel_id` | id or `null` | A voice or stage channel to hold the event in. Can't be combined with `location`. |
| `location` | string or `null` | Works like the `/schedule` `location` option: a voice or stage channel's name puts the event in that channel, and any other text, up to 100 characters, is an external location such as an address. Without `location` or `voice_channel_id`, the event uses the server's first voice channel. |
| `scheduler_id` | id or `null` | The person the event is for, such as whoever made a reservation. Leave it out or set it to `null` when nobody in particular asked for the event. See [The scheduler](#the-scheduler). |
| `image_url` | string or `null` | An image for the Discord event and the bot's messages. |
| `include_exclude` | string or `null` | `"INCLUDE"` or `"EXCLUDE"`, in any case. `null` means `"INCLUDE"`. Decides whether `usernames` and `roles` are the people invited or the people left out. |
| `usernames` | see below | Usernames or user ids to include or exclude. |
| `roles` | see below | Role names or role ids to include or exclude. Role names ignore capitalization. |
| `duration` | number | The length in minutes, up to 1440 (24 hours). For `"schedule"`, `0` sets the length to however long everyone is available. For `"create"`, it must be at least 1. |
| `multi_event` | `true` or `false` | `"schedule"` only. Creates one event on each day everyone is available, instead of a single event. |
| `start_time` | string | `"create"` only. A full ISO date and time, such as `"2026-10-11T19:00:00-04:00"` (an offset is recommended, since times without one are read as the bot's local time), or a time such as `"2200"`. A time already past moves to the next day. |

`usernames` and `roles` each take `null`, a comma separated string (`"alice, 123456789012345678"`), a single id, or a list of names and ids (`["alice", 123456789012345678]`).

Packets can't set `/schedule`'s `timeout` option, so `"schedule"` packets always wait up to 7 days for responses.

### Who gets invited

Only members who can see the text channel can be invited. Anyone in `usernames` who isn't in the text channel is skipped without an error, but a role that doesn't exist is an error.

Bots are only invited when they're listed in `usernames`. Being the scheduler, "everyone" and `roles` don't invite them, and this bot never invites itself. Bots can't enter availability, so they count as available at any time, and an event starts in its voice channel without waiting for them to join.

| `usernames` | `roles` | Who's invited |
|-------------|---------|---------------|
| `null` | `null` or empty | Everyone in the text channel. |
| names or ids | `null` or empty | With `INCLUDE`, only those people. With `EXCLUDE`, everyone except them. |
| `null` or empty | role names or ids | With `INCLUDE`, only people with one of the roles. With `EXCLUDE`, everyone without any of them. |
| names or ids | role names or ids | The people in `usernames` are always invited, whatever `include_exclude` says. `roles` then decides everyone else, as in the row above. |
| empty (`""` or `[]`) | `null` or empty | With `INCLUDE`, only the scheduler, or everyone if there's no scheduler or it's a bot. With `EXCLUDE`, everyone. |

A person as the scheduler is also invited on top of this. A bot scheduler isn't. If no one ends up invited, or only bots are, the packet is rejected.

### The scheduler

`scheduler_id` is who the bot's messages say the event was "Scheduled by". It's optional, so a packet can come from any of these:

- **Someone owns the event**, such as a person who reserved a range lane. Send their id as `scheduler_id`. They're shown as the scheduler and always invited, without being in `usernames` or `roles` and even if those would exclude them. The packet is rejected if they can't see the text channel or the event's voice channel, including the server's first voice channel when there's no `location`, so nobody can put an event somewhere they can't go. An external location isn't checked.
- **A bot scheduled it**, such as one announcing an episode release. Send the bot's id as `scheduler_id` to show it as the scheduler. It isn't invited unless it's also listed in `usernames`. It must be able to see the text channel, and also the voice channel if it's invited.
- **No scheduler.** Leave out `scheduler_id` or set it to `null`, and the bot's messages don't show one.

Either way, only `usernames` and `roles` decide who else is invited. An id that isn't in the server is rejected.

### Example: an event someone owns

Someone reserved a lane, and everyone with the Range role is invited along with them:

```json
{
  "type": "create",
  "name": "Range Night",
  "guild_id": "123456789012345678",
  "text_channel_id": "123456789012345678",
  "location": "123 Main St",
  "scheduler_id": "234567890123456789",
  "start_time": "2026-10-11T19:00:00-04:00",
  "image_url": null,
  "include_exclude": "INCLUDE",
  "usernames": null,
  "roles": "Range",
  "duration": 60
}
```

To invite only them, set `usernames` to `""`. To invite them and everyone *without* the role, set `include_exclude` to `"EXCLUDE"`.

### Example: an event a bot scheduled

A new episode is out, so the episode bot asks everyone with the Anime role when they can watch it together. The bot is shown as the scheduler but isn't invited:

```json
{
  "type": "schedule",
  "name": "Episode 12",
  "guild_id": "123456789012345678",
  "text_channel_id": "123456789012345678",
  "location": "Watch Party",
  "scheduler_id": "876543210987654321",
  "image_url": "https://example.com/episode-12.png",
  "include_exclude": "INCLUDE",
  "usernames": null,
  "roles": ["Anime"],
  "duration": 0,
  "multi_event": false
}
```

Here `"location": "Watch Party"` names a voice channel. Leave `roles` as `null` too to invite everyone in the text channel, or leave out `scheduler_id` to show no scheduler.

### Replies

| Reply | Meaning |
|-------|---------|
| `valid` | The event was created, or scheduling started. |
| `invalid JSON` | The message couldn't be read as JSON. |
| `error: <message>` | The packet was rejected and nothing was created. The message says why, for example `error: missing field(s): roles, duration`, `error: "guild_id" must be an id, as a number or a string of digits`, `error: text channel 123 not found in My Server`, `error: the scheduler can't see voice channel General`, `error: An event needs at least one person, not just bots.`, or `error: No one was found to invite to the event.` |

Example in Python:

```python
import json, socket

with socket.create_connection(("127.0.0.1", 5000)) as s:
    s.sendall(json.dumps(payload).encode())
    print(s.recv(1024).decode())
```

---

## Files and logs

| File | Contents |
|------|----------|
| `data.json` | Saved events, users' settings, and server voice settings, rewritten every second. The bot reads it on startup to resume events. To start fresh, delete it while the bot is stopped. |
| `scheduler.log` | The full log. The same output is printed to the console. |
| `<event id>.png` | Each event's downloaded image, used for its Discord event. The id is the event's `id` in `data.json`. Every 10 minutes, the bot deletes `.png` files in its folder that no event or [Schedule Again](#schedule-again) button uses, if they're more than 10 minutes old. Don't keep other `.png` files in the bot's folder. |

## Running the tests

```bash
pip install pytest
python3 -m pytest
```
