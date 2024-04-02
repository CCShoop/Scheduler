from event import Event
from discord import Button, ButtonStyle, Interaction
from discord.ui import View

class PromptButtons(View):
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
                print(f'{get_log_time()} {self.event.name}> {self.participant.member.name} selected full availability')
            else:
                button.style = ButtonStyle.blurple
                # TODO: remove availability
                print(f'{get_log_time()} {self.event.name}> {self.participant.member.name} deselected full availability')
            try:
                await interaction.response.edit_message(view=self)
            except Exception as e:
                print(f'{get_log_time()} Error with ALL button press by {self.participant.member.name}: {e}')

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
                print(f'{get_log_time()} {self.event.name}> {self.participant.member.name} selected no availability')
            else:
                button.style = ButtonStyle.blurple
                self.event.reason.replace(f'{self.participant.member.name} has no availability. ', '')
                self.event.unavailable = False
                print(f'{get_log_time()} {self.event.name}> {self.participant.member.name} deselected no availability')
            try:
                await interaction.response.edit_message(view=self)
            except Exception as e:
                print(f'{get_log_time()} Error with NONE button press by {self.participant.member.name}: {e}')

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
                print(f'{get_log_time()} {self.event.name}> {self.participant.member.name} unsubscribed')
            else:
                button.style = ButtonStyle.blurple
                print(f'{get_log_time()} {self.event.name}> {self.participant.member.name} resubscribed')
            try:
                await interaction.response.edit_message(view=self)
            except Exception as e:
                print(f'{get_log_time()} Error with UNSUB button press by {self.participant.member.name}: {e}')

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
            print(f'{get_log_time()} {self.event.name}> {interaction.user} started by button press')
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
                print(f'{get_log_time()} Error responding to START button interaction: {e}')
        self.start_button.callback = start_button_callback
        self.add_item(self.start_button)

    def add_end_button(self):
        self.end_button.disabled = True
        async def end_button_callback(interaction: Interaction):
            self.event.text_channel = interaction.channel
            if self.event.scheduled_event.status != EventStatus.active and self.event.scheduled_event.status != EventStatus.scheduled:
                await interaction.response.edit_message(view=self)
                return
            print(f'{get_log_time()} {self.event.name}> {interaction.user} ended by button press')
            client.scheduled_events.remove(self.event.scheduled_event)
            await self.event.scheduled_event.delete(reason='End button pressed.')
            self.event.created = False
            await self.event.remove()
            self.end_button.style = ButtonStyle.gray
            self.end_button.disabled = True
            self.reschedule_button.disabled = True
            self.cancel_button.disabled = True
            try:
                await interaction.response.edit_message(view=self)
            except Exception as e:
                print(f'{get_log_time()} Error responding to END button interaction: {e}')
        self.end_button.callback = end_button_callback
        self.add_item(self.end_button)

    def add_reschedule_button(self):
        async def reschedule_button_callback(interaction: Interaction):
            self.event.text_channel = interaction.channel
            if not self.event.created:
                await interaction.response.edit_message(view=self)
                return
            print(f'{get_log_time()} {self.event.name}> {interaction.user} rescheduled by button press')
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
            await self.event.remove()
            client.events.append(new_event)
            mentions = ''
            for participant in self.event.participants:
                if participant.member != interaction.user:
                    mentions += participant.member.mention
            try:
                await self.event.text_channel.send(f'{mentions}\n{interaction.user.mention} wants to reschedule {new_event.name}. Check your DMs to share your availability!')
            except Exception as e:
                print(f'{get_log_time()} Error sending RESCHEDULE button text channel message: {e}')
            try:
                await new_event.dm_all_participants(interaction, self.event.duration, reschedule=True)
            except Exception as e:
                print(f'{get_log_time()} Error with RESCHEDULE button DMing all participants: {e}')
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
            mentions = ''
            for participant in self.event.participants:
                if participant.member != interaction.user:
                    async with client.msg_lock:
                        await participant.member.send(f'{interaction.user.name} has cancelled {self.event.name}.')
                    mentions += participant.member.mention
            print(f'{get_log_time()} {self.event.name}> {interaction.user} cancelled by button press')
            self.cancel_button.style = ButtonStyle.gray
            self.start_button.disabled = True
            self.end_button.disabled = True
            self.reschedule_button.disabled = True
            self.cancel_button.disabled = True
            try:
                await interaction.response.edit_message(view=self)
                await self.event.text_channel.send(f'{mentions}\n{interaction.user.mention} cancelled {self.event.name}.')
            except Exception as e:
                print(f'{get_log_time()} Error sending CANCEL button interaction response or cancelled message to text channel: {e}')
        self.cancel_button.callback = cancel_button_callback
        self.add_item(self.cancel_button)
