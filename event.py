from asyncio import Lock
from datetime import datetime, timedelta
from discord import Interaction, EntityType, Guild, VoiceChannel, TextChannel, PrivacyLevel, ButtonStyle
from discord.ui import View, Button

from participant import Participant
from logger import log_info, log_warn, log_error

def get_time():
    ct = str(datetime.now())
    hour = int(ct[11:13])
    minute = int(ct[14:16])
    return hour, minute

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
        if not reschedule:
            for participant in self.participants:
                log_info(f'{self.name}> Sending prompt to {participant.member.name}')
                view = AvailabilityButtons(participant=participant, event=self)
                async with participant.msg_lock:
                    await participant.member.send(f'⬇️⬇️⬇️⬇️⬇️ __**{self.name}**__ ⬇️⬇️⬇️⬇️⬇️'
                                                  f'\n\n{interaction.user.name} wants to create an event called {self.name}.'
                                                  f'\nIt will be scheduled to last {duration} minutes.'
                                                  f'\n\nPlease send all of the times you will be available to attend {self.name}!'
                                                  f'\nExamples: "{self.name} 8-11" | "{self.name} 15:30-17:00" | "{self.name} 17-2030, 22-2"'
                                                  f'\n\n**All** will set your availability to full.'
                                                  f'\n**None** will stop the event from being created.'
                                                  f'\n**Unsubscribe** will allow the event to occur with or without you.'
                                                  f'\n\nThe event will be either created or cancelled 1-2 minutes after the last person responds.️', view=view)
            log_info(f'{self.name}> Done DMing all participants')
        else:
            for participant in self.participants:
                if participant.member.name == interaction.user.name:
                    view = AvailabilityButtons(participant=participant, event=self)
                    async with participant.msg_lock:
                        await participant.member.send(f'⬇️⬇️⬇️⬇️⬇️ __**{self.name}**__ ⬇️⬇️⬇️⬇️⬇️'
                                                      f'\n\nPlease send your new availability for {self.name}.'
                                                      f'\nIt will be scheduled to last {duration} minutes.'
                                                      f'\n\nExamples: "{self.name} 8-11" | "{self.name} 15:30-17:00" | "{self.name} 17-2030, 22-2"'
                                                      f'\n\n**All** will set your availability to full.'
                                                      f'\n**None** will stop the event from being created.'
                                                      f'\n**Unsubscribe** will allow the event to occur with or without you.', view=view)
                        log_info(f'{self.name}> Done DMing {participant.name} for reschedule')
                    break

    async def update_message(self):
        if self.has_everyone_answered():
            try:
                await self.responded_message.edit(content=f'Everyone has responded.')
            except Exception as e:
                log_error(f'{self.name}> Error editing responded message with "everyone has responded": {e}')
            return
        try:
            mentions = ''
            for participant in self.participants:
                if participant.subscribed and not participant.answered:
                    mentions += f'{participant.member.mention} '
            mentions = '\nWaiting for a response from these participants:\n' + mentions
        except Exception as e:
            log_error(f'{self.name}> Error generating mentions list for responded message: {e}')
        try:
            await self.responded_message.edit(content=f'{mentions}')
        except Exception as e:
            log_error(f'{self.name}> Error editing responded message: {e}')

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
                log_info(f'{self.name}> Nudged {participant.member.name}')

class AvailabilityButtons(View):
    def __init__(self, participant: Participant, event: Event):
        super().__init__(timeout=None)
        self.all_label = "All"
        self.none_label = "None"
        self.unsub_label = "Unsubscribe"
        self.participant = participant
        self.event = event
        self.add_all_button()
        self.add_none_button()
        self.add_unsub_button()

    def add_all_button(self):
        button = Button(label=self.all_label, style=ButtonStyle.blurple)
        async def all_button_callback(interaction: Interaction):
            self.event.changed = True
            self.event.ready_to_create = False
            self.participant.answered = True
            if button.style == ButtonStyle.blurple:
                button.style = ButtonStyle.green
                # TODO: make fully available
                log_info(f'{self.event.name}> {self.participant.member.name} selected full availability')
            else:
                button.style = ButtonStyle.blurple
                # TODO: remove availability
                log_info(f'{self.event.name}> {self.participant.member.name} deselected full availability')
            try:
                await interaction.response.edit_message(view=self)
                await self.event.update_message()
            except Exception as e:
                log_error(f'Error with ALL button press by {self.participant.member.name}: {e}')

        button.callback = all_button_callback
        self.add_item(button)

    def add_none_button(self):
        button = Button(label=self.none_label, style=ButtonStyle.blurple)
        async def none_button_callback(interaction: Interaction):
            self.event.changed = True
            self.event.ready_to_create = False
            self.participant.answered = True
            if button.style == ButtonStyle.blurple:
                button.style = ButtonStyle.gray
                for time_slot in timestamps.all_timestamps:
                    if self.participant.is_available(time_slot):
                        self.participant.toggle_availability(time_slot)
                self.event.reason += f'{self.participant.member.name} has no availability. '
                self.event.unavailable = True
                log_info(f'{self.event.name}> {self.participant.member.name} selected no availability')
            else:
                button.style = ButtonStyle.blurple
                self.event.reason.replace(f'{self.participant.member.name} has no availability. ', '')
                self.event.unavailable = False
                log_info(f'{self.event.name}> {self.participant.member.name} deselected no availability')
            try:
                await interaction.response.edit_message(view=self)
                await self.event.update_message()
            except Exception as e:
                log_error(f'Error with NONE button press by {self.participant.member.name}: {e}')

        button.callback = none_button_callback
        self.add_item(button)

    def add_unsub_button(self):
        button = Button(label=self.unsub_label, style=ButtonStyle.blurple)
        async def unsub_button_callback(interaction: Interaction):
            self.event.changed = True
            self.event.ready_to_create = False
            self.participant.answered = True
            self.participant.subscribed = not self.participant.subscribed
            if button.style == ButtonStyle.blurple:
                button.style = ButtonStyle.gray
                log_info(f'{self.event.name}> {self.participant.member.name} unsubscribed')
            else:
                button.style = ButtonStyle.blurple
                log_info(f'{self.event.name}> {self.participant.member.name} resubscribed')
            try:
                await interaction.response.edit_message(view=self)
                await self.event.update_message()
            except Exception as e:
                log_error(f'Error with UNSUB button press by {self.participant.member.name}: {e}')

        button.callback = unsub_button_callback
        self.add_item(button)

class EventButtons(View):
    def __init__(self, event: Event):
        super().__init__(timeout=None)
        self.start_label = "Start Event"
        self.end_label = "End Event"
        self.reschedule_label = "Reschedule Event"
        self.cancel_label = "Cancel Event"
        self.event = event
        self.start_button = Button(label=self.start_label, style=ButtonStyle.blurple)
        self.end_button = Button(label=self.end_label, style=ButtonStyle.blurple)
        self.reschedule_button = Button(label=self.reschedule_label, style=ButtonStyle.red)
        self.cancel_button = Button(label=self.cancel_label, style=ButtonStyle.red)
        self.add_start_button()
        self.add_end_button()
        self.add_reschedule_button()
        self.add_cancel_button()

    def add_start_button(self):
        async def start_button_callback(interaction: Interaction):
            self.event.text_channel = interaction.channel
            if not self.event.created or self.event.scheduled_event.status != EventStatus.scheduled:
                await interaction.response.edit_message(view=self)
                return
            log_info(f'{self.event.name}> {interaction.user} started by button press')
            participant_names = [participant.member.name for participant in self.event.participants]
            if interaction.user.name not in participant_names:
                self.event.participants.append(interaction.user)
            await self.event.scheduled_event.start(reason='Start button pressed.')
            self.event.started = True
            self.start_button.style = ButtonStyle.green
            self.start_button.disabled = True
            self.end_button.disabled = False
            try:
                await interaction.response.edit_message(view=self)
            except Exception as e:
                log_error(f'Error responding to START button interaction: {e}')
        self.start_button.callback = start_button_callback
        self.add_item(self.start_button)

    def add_end_button(self):
        self.end_button.disabled = True
        async def end_button_callback(interaction: Interaction):
            self.event.text_channel = interaction.channel
            if self.event.scheduled_event.status != EventStatus.active and self.event.scheduled_event.status != EventStatus.scheduled:
                await interaction.response.edit_message(view=self)
                return
            log_info(f'{self.event.name}> {interaction.user} ended by button press')
            client.scheduled_events.remove(self.event.scheduled_event)
            await self.event.scheduled_event.delete(reason='End button pressed.')
            self.event.created = False
            client.events.remove(event)
            del event
            self.end_button.style = ButtonStyle.gray
            self.end_button.disabled = True
            self.reschedule_button.disabled = True
            self.cancel_button.disabled = True
            try:
                await interaction.response.edit_message(view=self)
            except Exception as e:
                log_error(f'Error responding to END button interaction: {e}')
        self.end_button.callback = end_button_callback
        self.add_item(self.end_button)

    def add_reschedule_button(self):
        async def reschedule_button_callback(interaction: Interaction):
            self.event.text_channel = interaction.channel
            if not self.event.created:
                await interaction.response.edit_message(view=self)
                return
            log_info(f'{self.event.name}> {interaction.user} rescheduled by button press')
            participant_names = [participant.member.name for participant in self.event.participants]
            if interaction.user.name not in participant_names:
                self.event.participants.append(interaction.user)
            new_event = Event(self.event.name, self.event.entity_type, self.event.voice_channel, self.event.participants, self.event.guild, interaction.channel, self.event.image_url, self.event.duration) #, weekly
            client.scheduled_events.remove(self.event.scheduled_event)
            await self.event.scheduled_event.delete(reason='Reschedule button pressed.')
            self.event.created = False
            self.start_button.disabled = True
            self.start_button.style = ButtonStyle.blurple
            self.end_button.disabled = True
            self.end_button.style = ButtonStyle.blurple
            self.reschedule_button.disabled = True
            self.cancel_button.disabled = True
            await interaction.response.edit_message(view=self)
            client.events.remove(event)
            del event
            client.events.append(new_event)
            mentions = ''
            for participant in self.event.participants:
                if participant.member != interaction.user:
                    mentions += participant.member.mention
            try:
                await self.event.text_channel.send(f'{mentions}\n{interaction.user.mention} wants to reschedule {new_event.name}. Check your DMs to share your availability!')
            except Exception as e:
                log_error(f'Error sending RESCHEDULE button text channel message: {e}')
            try:
                await new_event.dm_all_participants(interaction, self.event.duration, reschedule=True)
            except Exception as e:
                log_error(f'Error with RESCHEDULE button DMing all participants: {e}')
        self.reschedule_button.callback = reschedule_button_callback
        self.add_item(self.reschedule_button)

    def add_cancel_button(self):
        async def cancel_button_callback(interaction: Interaction):
            self.event.text_channel = interaction.channel
            if self.event.created:
                client.scheduled_events.remove(self.event.scheduled_event)
                await self.event.scheduled_event.delete(reason='Cancel button pressed.')
                self.event.created = False
            await self.event.remove()
            client.events.remove(event)
            del event
            mentions = ''
            for participant in self.event.participants:
                if participant.member != interaction.user:
                    async with client.msg_lock:
                        await participant.member.send(f'{interaction.user.name} has cancelled {self.event.name}.')
                    mentions += participant.member.mention
            log_info(f'{self.event.name}> {interaction.user} cancelled by button press')
            self.cancel_button.style = ButtonStyle.gray
            self.start_button.disabled = True
            self.end_button.disabled = True
            self.reschedule_button.disabled = True
            self.cancel_button.disabled = True
            try:
                await interaction.response.edit_message(view=self)
                await self.event.text_channel.send(f'{mentions}\n{interaction.user.mention} cancelled {self.event.name}.')
            except Exception as e:
                log_error(f'Error sending CANCEL button interaction response or cancelled message to text channel: {e}')
        self.cancel_button.callback = cancel_button_callback
        self.add_item(self.cancel_button)