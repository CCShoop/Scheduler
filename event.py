from asyncio import Lock
from datetime import datetime, timedelta
from discord import EntityType, Guild, VoiceChannel, TextChannel

from buttons import PromptButtons
from participant import Participant

class Event:
    def __init__(self, name: str, entity_type: EntityType, voice_channel: VoiceChannel, participants: list, guild: Guild, text_channel: TextChannel, image_url: str, duration: int = 30, start_time: datetime = None):
        self.name = name
        self.guild = guild
        self.entity_type = entity_type
        self.og_message_text = f' wants to create an event called {self.name}. Check your DMs to share your availability!'
        self.responded_message = None
        self.text_channel = text_channel
        self.voice_channel = voice_channel
        self.privacy_level = PrivacyLevel.guild_only
        self.participants = participants
        self.image_url = image_url
        self.buttons = []
        self.ready_to_create = False
        self.created = False
        self.started = False
        self.scheduled_event: ScheduledEvent = None
        self.changed = False
        self.start_time = start_time
        self.unavailable = False

    def compare_availabilities(self):
        pass

    def shares_participants(self, event):
        for self_participant in self.participants:
            for other_participant in event.participants:
                if self_participant.member == other_participant.member:
                    return True
        return False

    def has_everyone_answered(self):
        for participant in self.participants:
            if participant.subscribed and not participant.answered:
                return False
        return True

    async def dm_all_participants(self, interaction: Interaction, duration: int = 30, reschedule: bool = False):
        curHour, curMinute = get_time()
        curTimeObj = datetime(2000, 1, 1, curHour, curMinute).replace(second=0, microsecond=0)
        for participant in self.participants:
            print(f'{get_log_time()} {self.name}> Sending prompt to {participant.member.name}')
            view = PromptButtons(participant=participant, event=self)
            async with participant.msg_lock:
                if reschedule:
                    await participant.member.send(f'⬇️⬇️⬇️⬇️⬇️ __**{self.name}**__ ⬇️⬇️⬇️⬇️⬇️\n{interaction.user.name} wants to **reschedule** {self.name}.\nThe event will last {duration} minutes.', view=view)
                else:
                    await participant.member.send(f'⬇️⬇️⬇️⬇️⬇️ __**{self.name}**__ ⬇️⬇️⬇️⬇️⬇️\n{interaction.user.name} wants to create an event called {self.name}.\nThe event will last {duration} minutes.', view=view)
                await participant.member.send(f'Select **all** of the 30 minute blocks you could be available to attend {self.name}!\n"None" will stop the event from being created, so click "Unsubscribe" if you want the event to occur with or without you.\n'
                                                f'The event will be either created or cancelled 1-2 minutes after the last person responds.\n'f'⬆️⬆️⬆️⬆️⬆️ __**{self.name}**__ ⬆️⬆️⬆️⬆️⬆️')
        print(f'{get_log_time()} {self.name}> Done DMing all participants')

    async def update_message(self):
        if self.has_everyone_answered():
            try:
                await self.responded_message.edit(content=f'Everyone has responded.')
            except Exception as e:
                print(f'{get_log_time()} {self.name}> Error editing responded message with "everyone has responded": {e}')
            return
        try:
            mentions = ''
            for participant in self.participants:
                if participant.subscribed and not participant.answered:
                    mentions += f'{participant.member.mention} '
            mentions = '\nWaiting for a response from these participants:\n' + mentions
        except Exception as e:
            print(f'{get_log_time()} {self.name}> Error generating mentions list for responded message: {e}')
        try:
            await self.responded_message.edit(content=f'{mentions}')
        except Exception as e:
            print(f'{get_log_time()} {self.name}> Error editing responded message: {e}')

    def nudge_timer(self):
        self.nudge_unresponded_timer -= 1
        if self.nudge_unresponded_timer == 0:
            self.nudge_unresponded_timer = 30
            return True
        return False

    async def nudge_unresponded_participants(self):
        if not self.nudge_timer() or self.created or self.has_everyone_answered():
            return
        for participant in self.participants:
            if not participant.answered:
                await participant.member.send(random.choice(self.nudges))
                print(f'{get_log_time()} {self.name}> Nudged {participant.member.name}')

    async def remove(self):
        client.events.remove(self)