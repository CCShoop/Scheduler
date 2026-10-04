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
3. On the same tab, under **Privileged Gateway Intents**, turn on all three:
   - Presence Intent
   - Server Members Intent
   - Message Content Intent

   The bot requests every intent, and it won't connect unless these are enabled.
4. Open **OAuth2 → URL Generator**, select the `bot` and `applications.commands` scopes, then select these bot permissions:
   - View Channels
   - Send Messages
   - Embed Links
   - Read Message History
   - Create Events and Manage Events
5. Open the generated URL and add the bot to your server.

### 2. Install the dependencies

```bash
git clone <this repository>
cd Scheduler
pip install -r requirements.txt
```

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
| `event_name`      | Yes | The event's name. Two events can't have the same name at the same time. |
| `location`        | Yes | Where the event takes place. The voice channels appear as suggestions, so press Enter to pick the first one. Or type any other location, such as an address, up to 100 characters. See [Locations](#locations). |
| `image_url`       | No  | An image for the Discord event and the bot's messages. |
| `include_exclude` | No  | Whether `usernames` and `roles` add people (`INCLUDE`, the default) or leave people out (`EXCLUDE`). |
| `usernames`       | No  | Comma-separated usernames or user IDs to include or exclude. |
| `roles`           | No  | Comma-separated role names to include or exclude. |
| `duration`        | No  | The length in minutes. The default, `0`, sets the length to however long everyone is available. |
| `multi_event`     | No  | Creates one event on each day everyone is available, instead of a single event. |
| `timeout`         | No  | The number of days to wait for responses before giving up. The default is 7. |

**Who gets invited:** You're always a participant. If you don't set `usernames` or `roles`, everyone who can see the text channel is invited. Bots are never invited. If you set `roles`, `usernames` is ignored.

**Examples:**

```
/schedule event_name:Game Night location:General
/schedule event_name:Raid location:Raids roles:Raiders
/schedule event_name:Movie location:Theater include_exclude:EXCLUDE usernames:alice, bob
/schedule event_name:Board Games location:123 Main St
```

### Locations

The `location` option suggests the server's voice channels as you type. Picking one, or typing a voice channel's exact name, puts the event in that voice channel. Anything else becomes an external location, and the Discord event is created as a "somewhere else" event.

The location changes how the event starts and ends:

| | Voice channel | Other location |
|---|---|---|
| **Starts** | When every participant is in the voice channel, from 15 minutes before the start time | At the start time |
| **Ends** | When everyone has left the voice channel | When its duration has passed |
| **Start Event button** | You must be in the voice channel | Any participant can press it |

Either way, events at the same location can't overlap. For typed locations, "the same location" means the same text, ignoring capitalization.

### `/create`: create an event at a time you choose

Use this command when you already know the time. It takes the same options as `/schedule`, plus:

| Option       | Required | Description |
|--------------|----------|-------------|
| `start_time` | Yes | A time such as `2200`, or a full ISO date and time such as `2026-10-04T22:00`. |
| `duration`   | No  | The length in minutes. The default is 30. |

### Other commands

| Command         | What it does |
|-----------------|--------------|
| `/edit`         | Change an event in the current channel. You can change its name, location, image, duration, multi-event setting, or timeout. If the channel has more than one event, pick one from a dropdown. |
| `/attach`       | Have the bot manage a Discord event that was created outside the bot. Pick the event from a dropdown. |
| `/availability` | Show everyone's availability for an event in the current channel. |
| `/listevents`   | List every event the bot is managing in this server. |
| `/listmyevents` | List every event you're a participant in. |
| `/help`         | Show the built-in help. |

### Adding an image later

Send the bot a direct message that includes the event's name and an image attachment. If the event has been created, the bot updates the Discord event's image. If not, it uses the image once the event is created.

---

## Entering availability

The availability message has these buttons:

| Button | What it does |
|--------|--------------|
| **Respond** | Opens a form for entering when you're free. |
| **Full Availability (Today)** | Marks you as available from now until 2 AM. If someone else enters availability later than that, yours is extended to match. |
| **Use Existing Availability** | Copies your availability from another event you're in. If you're in more than one, choose which event to copy from. |
| **Unsubscribe from Event** | Stops the bot from mentioning you. You're still a participant. |
| **Cancel Scheduling** | Cancels the event, with an optional reason. |

### The Respond form

- **Date:** The day you're entering times for, as `MM/DD/YYYY`.
- **Timeslot 1 / Timeslot 2:** The time ranges you're free. Separate multiple ranges with commas.
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
| `none` | Remove all of your availability |

If you're in several events, time taken up by a created event is automatically removed from your availability in the other events. It's restored if that event is cancelled or ends.

---

## Managing a scheduled event

After the event is created, the bot posts a message with these buttons:

| Button | What it does |
|--------|--------------|
| **Start Event / End Event** | Starts the event manually, then changes to an End button. Events also start automatically when every participant joins the voice channel, and end when everyone leaves. Events at a [typed location](#locations) start at their start time and end when their duration has passed. |
| **End and Forget** | Ends the event, and the bot stops tracking it. |
| **Unsubscribe** | Stops the bot from mentioning you about this event. |
| **Reschedule Event** | Restarts availability collection with the same participants. |
| **Cancel Event** | Cancels the event. |

The bot mentions participants 15 minutes before the event starts.

### Schedule Again

When an event ends or is cancelled, the bot shows a **Schedule Again** button for about a week. It reuses the location and participants, and lets you change the name, duration, image URL, and start time:

- Leave **Start Time** blank to start collecting availability again, as with `/schedule`.
- Enter a **Start Time** to create the event at that time, as with `/create`.

Click **Forget** to dismiss the button.

---

## Owner-only commands

Only the user set as `OWNER_ID` can run these. Type them as normal messages in any channel the bot can read.

| Message | What it does |
|---------|--------------|
| `scheduler: sync` | Register the slash commands with Discord again. |
| `scheduler: events` | List every event the bot is tracking, in all servers, with its status. |
| `scheduler: check` | Check every event right away and create any that are ready. |
| `scheduler: debug` | Turn debug logging on or off. |
| `scheduler: subscribe <user id> to <event name>` | Add a user to an event. |
| `scheduler: unsubscribe <user id> from <event name>` | Unsubscribe a user from an event. |

The slash command `/offset <hours>` is also owner-only. It sets how many hours past midnight **Full Availability (Today)** extends to, from 0 to 23. The default is 2, which means 2 AM. The setting applies to every server.

---

## Scheduling from another program

The bot listens for TCP connections on `HOST:PORT` from `.env`. Send a JSON object to start scheduling an event, just as `/schedule` would:

```json
{
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

Every field is required except `location` and `voice_channel_id`. Set `location` to a string, such as an address, to hold the event there instead of in a voice channel. Without either one, the event uses the server's first voice channel. The bot replies `valid` if the request was accepted, `invalid JSON` if the message couldn't be parsed, or `error: <message>` if scheduling failed.

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
