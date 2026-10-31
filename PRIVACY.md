# Privacy Policy

**Effective date:** October 31, 2026

This policy explains what data the Event Scheduler Discord bot ("the bot") collects, why, and how you can have it removed. The bot is a personal project run by its developer ("I", "me").

## What the bot collects

The bot only collects what it needs to schedule events. It cannot see messages in servers. Discord passes along direct messages sent to the bot. It ignores them if they are not sent by the developer, and does not save them.

**Your Discord account**
- Your Discord user ID and username, when you are added to an event, schedule or reschedule one, or change a setting.

**Your settings**
- The timezone you entered with your most recent availability, and your Full Availability cutoff (set with `/cutoff`).

**Events you are part of**
- The event's name, location, image, duration, start times, and the server and channels it uses.
- Who scheduled it and who is participating. Participants can be added by name or by role.
- For each participant: the availability they entered, whether they've answered, whether they're subscribed or unavailable, any note they left, and the times removed from their availability because their other events take place then.
- Which participants are in the event's voice channel, to start and end the event automatically.
- Temporary blocks on people who are not participants but pressed an event's cancel button.

**Server settings**
- Each server's choice of voice for event announcements (set with `/tts`).

**Logs**
- The bot keeps a log of what it does for debugging purposes, such as events being created, started, and ended, and who took those actions. Logs include Discord usernames and IDs, server and channel names, event names, locations, and image links.

Other programs, such as my other bots, can also create and schedule events through the bot. Events made this way store the same kinds of information. The bot only replies to those programs with whether the request was successful.

## How the data is used

The data is used only to run the bot: finding a time that works for everyone, creating and updating Discord events, sending reminders and direct messages about events, announcing events in voice channels, and fixing problems with the bot.

Your data is not sold, shared with third parties, or used for advertising or tracking.

## Where the data is stored

All data is stored on a personal server that I run from home. Event announcements are generated on that server, so no voice or text is sent to an outside service.

When an event has an image, the bot downloads it from the link it was given and saves a copy on the server. That link is also shown in the bot's Discord messages, so Discord loads the image from the same place.

The bot also sends data to Discord, such as messages and server events, as part of how it works. Discord's own [Privacy Policy](https://discord.com/privacy) covers that.

## How long data is kept

- **Events** are deleted when they end, are cancelled, or time out. An event with several occurrences is deleted after its last one. Events in a server are also deleted when the bot is removed from that server, and an event is deleted when its text channel is.
- **Schedule Again buttons**, shown after an event ends or is cancelled, keep that event's details for up to 8 days, or until the button is used.
- **Event images** are deleted with their event, or, while the bot is running, within about 20 minutes of nothing using them anymore.
- **Your settings** are kept until you leave every server the bot shares with you. They are also deleted if the bot is removed from those servers.
- **Server settings** are kept until the bot is removed from the server.
- **Logs** are kept until I clear them.
- If the bot's data file is ever damaged, a copy of it is kept until I repair or delete it.

## Your choices

- You can unsubscribe from an event, or decline to take part.
- You can ask me to delete your data, or ask what data the bot has about you, by contacting me (see below). I will delete it from the bot's data and logs, and can manually remove you from any events if necessary.

## Children

The bot is intended for people who meet Discord's minimum age requirement. It does not knowingly collect data from anyone younger.

## Changes to this policy

I may update this policy. The effective date above shows when it last changed. Continuing to use the bot after a change means you accept the new policy.

## Contact

For questions or requests, open an issue at [github.com/CCShoop/Scheduler/issues](https://github.com/CCShoop/Scheduler/issues).
