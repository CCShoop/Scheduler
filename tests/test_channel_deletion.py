from datetime import timedelta
from types import SimpleNamespace

import pytest
from discord import EntityType

from libs.participant import TimeBlock
from fakes import at, run
from test_lifecycle import FakeInteraction, make_created_event


def delete_channel(env, channel):
    env.guild.channels.pop(channel.id, None)
    if channel in env.guild.voice_channels:
        env.guild.voice_channels.remove(channel)
    run(env.sched.on_guild_channel_delete(SimpleNamespace(id=channel.id, guild=env.guild)))


def add_voice_channel(env, channel_id=3):
    voice_channel = SimpleNamespace(id=channel_id, name="Other", members=[], mention=f"<#{channel_id}>")
    env.guild.channels[channel_id] = voice_channel
    env.guild.voice_channels.append(voice_channel)
    return voice_channel


class TestTextChannelOrGuildDeleted:
    def test_text_channel_deleted_cancels_without_messaging(self, env):
        event, (scheduled_event,) = make_created_event(env)
        delete_channel(env, env.text_channel)
        assert event not in env.sched.client.events
        assert event.cancelled
        assert scheduled_event.deleted
        assert env.text_channel.sent == []

    def test_text_channel_deleted_restores_shared_availability(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        event, _ = make_created_event(env, participants=[a])
        other_text_channel = SimpleNamespace(id=9, members=[a.member], mention="<#9>")
        env.guild.channels[other_text_channel.id] = other_text_channel
        other = env.sched.Event(name="other", voice_channel=env.voice_channel, guild=env.guild,
                                text_channel=other_text_channel, participants=[a])
        env.sched.client.events.append(other)
        a.removed_times = [env.sched.participant_lib.RemovedTime(event.name, TimeBlock(at(1, 20), at(1, 22)),
                                                                 [TimeBlock(at(1, 20), at(1, 22))])]
        delete_channel(env, env.text_channel)
        assert other in env.sched.client.events
        assert a.removed_times == []

    def test_other_text_channel_deleted_is_ignored(self, env):
        event, (scheduled_event,) = make_created_event(env)
        run(env.sched.on_guild_channel_delete(SimpleNamespace(id=99, guild=env.guild)))
        assert event in env.sched.client.events
        assert not scheduled_event.deleted

    def test_guild_removed_cancels_its_events(self, env, monkeypatch):
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: None)
        event, (scheduled_event,) = make_created_event(env)
        run(env.sched.on_guild_remove(env.guild))
        assert event not in env.sched.client.events
        assert scheduled_event.deleted
        assert env.text_channel.sent == []

    def test_guild_removed_forgets_schedule_again_buttons(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")])
        run(event.cancel(schedule_again=True))
        assert env.sched.client.schedule_again_events
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: None)
        run(env.sched.on_guild_remove(env.guild))
        assert env.sched.client.schedule_again_events == []

    def test_update_discards_when_text_channel_is_gone(self, env):
        event, (scheduled_event,) = make_created_event(env)
        env.guild.channels.pop(env.text_channel.id)
        run(event.update())
        assert event not in env.sched.client.events
        assert scheduled_event.deleted


class TestVoiceChannelDeleted:
    def prompt_messages(self, env):
        return [sent for sent in env.text_channel.sent
                if sent.get("embed") is not None and sent["embed"].title == "Voice Channel Deleted"]

    def test_scheduling_event_prompts_and_pauses(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        event = env.make_event([a])
        delete_channel(env, env.voice_channel)
        assert event.voice_channel_missing
        assert event in env.sched.client.events
        (prompt_message,) = self.prompt_messages(env)
        assert prompt_message["view"] is event.voice_channel_prompt
        run(event.update())
        assert not event.created
        assert env.guild.created == []
        # Only one prompt is sent
        assert len(self.prompt_messages(env)) == 1

    def test_created_event_keeps_start_times_and_deletes_guild_events(self, env):
        event, scheduled_events = make_created_event(env, days=(1, 2))
        start_times = list(event.start_times)
        delete_channel(env, env.voice_channel)
        assert all(se.deleted for se in scheduled_events)
        assert event.scheduled_events == []
        assert event.start_times == start_times
        assert event.created
        run(event.update())
        assert env.guild.created == []

    def test_started_event_is_ended(self, env):
        event, (scheduled_event,) = make_created_event(env, started=True)
        delete_channel(env, env.voice_channel)
        assert event not in env.sched.client.events
        assert self.prompt_messages(env) == []

    def test_started_multi_event_ends_occurrence_then_prompts(self, env):
        event, (first, second) = make_created_event(env, days=(1, 2), started=True)
        delete_channel(env, env.voice_channel)
        assert event in env.sched.client.events
        assert not event.started
        assert event.start_times == [second.start_time]
        assert len(self.prompt_messages(env)) == 1

    def test_external_location_event_is_unaffected(self, env):
        event = env.make_event([env.make_participant("a")], location="Park")
        delete_channel(env, env.voice_channel)
        assert not event.voice_channel_missing
        assert self.prompt_messages(env) == []

    def test_selecting_a_voice_channel_recreates_guild_events(self, env):
        event, _ = make_created_event(env, days=(1, 2))
        new_voice_channel = add_voice_channel(env)
        delete_channel(env, env.voice_channel)
        prompt = event.voice_channel_prompt
        prompt.channel_select._values = [SimpleNamespace(id=new_voice_channel.id)]
        interaction = FakeInteraction(event.participants[0].member)
        run(prompt.channel_select_callback(interaction))
        assert event.voice_channel is new_voice_channel
        assert event.voice_channel_prompt is None
        assert prompt.message.deleted
        assert env.text_channel.sent[-1]["embed"].title == "Voice Channel Changed"
        run(event.update())
        assert [se.channel for se in event.scheduled_events] == [new_voice_channel, new_voice_channel]
        assert [se.start_time for se in event.scheduled_events] == event.start_times

    def test_selecting_a_missing_voice_channel_is_rejected(self, env):
        event = env.make_event([env.make_participant("a")])
        add_voice_channel(env)
        delete_channel(env, env.voice_channel)
        prompt = event.voice_channel_prompt
        prompt.channel_select._values = [SimpleNamespace(id=12345)]
        interaction = FakeInteraction(event.participants[0].member)
        run(prompt.channel_select_callback(interaction))
        assert event.voice_channel_missing
        assert interaction.responses[0][0] == "send_message"

    def test_non_participant_selecting_a_voice_channel_is_offered_to_join(self, env):
        event = env.make_event([env.make_participant("a")])
        new_voice_channel = add_voice_channel(env)
        delete_channel(env, env.voice_channel)
        prompt = event.voice_channel_prompt
        prompt.channel_select._values = [SimpleNamespace(id=new_voice_channel.id)]
        outsider = SimpleNamespace(id=70, name="outsider", nick=None)
        env.text_channel.members.append(outsider)
        interaction = FakeInteraction(outsider)
        run(prompt.channel_select_callback(interaction))
        assert event.voice_channel_missing
        assert interaction.responses[0][1]["content"] == \
            f"You aren't a participant of {event}. Join it, then use the voice channel dropdown again."

    def test_cancel_button_opens_cancel_modal(self, env):
        participant = env.make_participant("a")
        event = env.make_event([participant])
        delete_channel(env, env.voice_channel)
        interaction = FakeInteraction(participant.member)
        run(event.voice_channel_prompt.cancel_button_callback(interaction))
        (kind, modal), = interaction.responses
        assert kind == "send_modal" and isinstance(modal, env.sched.CancelModal)
        assert modal.event is event

    def test_cancelling_multi_event_cancels_every_start_time_and_deletes_prompt(self, env):
        event, _ = make_created_event(env, days=(1, 2, 3))
        delete_channel(env, env.voice_channel)
        prompt_message = event.voice_channel_prompt.message
        run(event.cancel(reason="no channel"))
        assert event.cancelled
        assert event not in env.sched.client.events
        assert prompt_message.deleted

    def test_location_set_elsewhere_removes_prompt(self, env):
        event = env.make_event([env.make_participant("a")])
        delete_channel(env, env.voice_channel)
        prompt_message = event.voice_channel_prompt.message
        event.location = "Park"
        run(event.update())
        assert event.voice_channel_prompt is None
        assert prompt_message.deleted

    def test_start_button_refuses_while_missing(self, env, monkeypatch):
        event, _ = make_created_event(env)
        delete_channel(env, env.voice_channel)
        starts = []

        async def fake_start(self, reason=None):
            starts.append(reason)

        monkeypatch.setattr(env.sched.Event, "start", fake_start)

        async def press():
            buttons = env.sched.EventButtons(event)
            interaction = FakeInteraction(event.participants[0].member)
            await buttons.start_callback(interaction)
            return interaction

        interaction = run(press())
        assert starts == []
        assert "voice channel was deleted" in interaction.responses[0][1]["content"]

    def test_timeout_still_applies_while_missing(self, env):
        event = env.make_event([env.make_participant("a")])
        delete_channel(env, env.voice_channel)
        event.timeout_at = env.sched.now() - timedelta(minutes=1)
        run(event.update())
        assert event not in env.sched.client.events


class TestDeletedChannelLocation:
    def test_becomes_a_typed_location(self, env):
        event, _ = make_created_event(env)
        delete_channel(env, env.voice_channel)
        assert event.location == env.sched.DELETED_VOICE_CHANNEL_LOCATION
        assert event.voice_channel is None
        assert event.location_string == "Deleted Voice Channel"

    def test_stage_channel_becomes_deleted_stage_channel(self, env):
        from test_stage_channels import add_stage_channel, make_stage_event
        stage_channel = add_stage_channel(env)
        event, _ = make_stage_event(env, stage_channel)
        delete_channel(env, stage_channel)
        assert event.location == env.sched.DELETED_STAGE_CHANNEL_LOCATION
        assert event.voice_channel_missing

    def test_events_of_deleted_channels_do_not_conflict(self, env):
        first, _ = make_created_event(env)
        other_channel = add_voice_channel(env)
        second = env.make_event([env.make_participant("b")], voice_channel=other_channel, created=True,
                                start_times=[at(1, 21)], scheduled_events=[])
        delete_channel(env, env.voice_channel)
        delete_channel(env, other_channel)
        assert not first.shares_voice_channel(second)
        assert first.get_occupied_timeblocks() == []
        run(env.sched.edit_event(first, duration=180))
        assert first.duration == timedelta(hours=3)

    def test_choosing_a_new_channel_clears_the_location(self, env):
        event, _ = make_created_event(env)
        delete_channel(env, env.voice_channel)
        new_voice_channel = add_voice_channel(env)
        run(event.move_to_voice_channel(new_voice_channel, "a"))
        assert event.location is None
        assert not event.voice_channel_missing
        assert event.location_string == new_voice_channel.mention

    @pytest.mark.parametrize("typed", ["Deleted Voice Channel", " Deleted Stage Channel ", "Deleted Voice Channel\t"])
    def test_typed_location_never_counts_as_deleted(self, env, typed):
        voice_channel, location = env.sched.resolve_location(env.guild, typed)
        event = env.make_event([env.make_participant("a")], location=location)
        assert not event.voice_channel_missing

    def test_discord_event_location_is_stripped(self, env):
        guild_event = SimpleNamespace(entity_type=EntityType.external, location="Deleted Voice Channel ")
        assert env.sched.get_guild_event_location(guild_event) == (None, "Deleted Voice Channel")
        guild_event.location = "   "
        assert env.sched.get_guild_event_location(guild_event) == (None, None)

    def test_old_data_without_a_location_loads_as_deleted(self, env):
        event = env.sched.Event(name="old", voice_channel=None, guild=env.guild, text_channel=env.text_channel,
                                voice_channel_deleted=True)
        assert event.location == env.sched.DELETED_VOICE_CHANNEL_LOCATION
        assert event.voice_channel_missing


class TestVoiceChannelPromptDropdown:
    def prompt_embeds(self, env):
        return [sent["embed"] for sent in env.text_channel.sent
                if sent.get("embed") is not None and sent["embed"].title == "Voice Channel Deleted"]

    def test_dropdown_when_a_voice_channel_remains(self, env):
        event = env.make_event([env.make_participant("a")])
        add_voice_channel(env)
        delete_channel(env, env.voice_channel)
        assert event.voice_channel_prompt.channel_select is not None
        (embed,) = self.prompt_embeds(env)
        assert embed.description == (f"The voice channel for **{event}** has been deleted.\n"
                                     "Please select a voice channel from the dropdown, or cancel the event.")

    def test_no_dropdown_without_voice_channels(self, env):
        event = env.make_event([env.make_participant("a")])
        delete_channel(env, env.voice_channel)
        prompt = event.voice_channel_prompt
        assert prompt.channel_select is None
        assert prompt.children == [prompt.cancel_button]
        (embed,) = self.prompt_embeds(env)
        assert embed.description.endswith("There are no voice channels in the server. "
                                          "Once one is made, you can select it here, or cancel the event.")
        run(prompt.disable())
        assert prompt.cancel_button.disabled

    def test_stage_channel_is_named(self, env):
        from test_stage_channels import add_stage_channel, make_stage_event
        stage_channel = add_stage_channel(env)
        event, _ = make_stage_event(env, stage_channel)
        delete_channel(env, stage_channel)
        (embed,) = self.prompt_embeds(env)
        assert embed.description.startswith(f"The stage channel for **{event}** has been deleted.")

    def test_resent_with_dropdown_once_a_voice_channel_is_made(self, env):
        event = env.make_event([env.make_participant("a")])
        delete_channel(env, env.voice_channel)
        old_message = event.voice_channel_prompt.message
        run(event.update())
        assert event.voice_channel_prompt.message is old_message
        add_voice_channel(env)
        run(event.update())
        assert old_message.deleted
        assert event.voice_channel_prompt.channel_select is not None
        assert len(self.prompt_embeds(env)) == 2

    def test_resent_without_dropdown_once_every_voice_channel_is_gone(self, env):
        event = env.make_event([env.make_participant("a")])
        other = add_voice_channel(env)
        delete_channel(env, env.voice_channel)
        delete_channel(env, other)
        run(event.update())
        assert event.voice_channel_prompt.channel_select is None


class TestDeletedVoiceChannelPromptMessage:
    def prompts(self, env):
        return [sent for sent in env.text_channel.sent
                if sent.get("embed") is not None and sent["embed"].title == "Voice Channel Deleted"]

    def make_waiting(self, env):
        event = env.make_event([env.make_participant("a")])
        add_voice_channel(env)
        delete_channel(env, env.voice_channel)
        return event

    def test_first_prompt_mentions_participants(self, env):
        event = self.make_waiting(env)
        (prompt,) = self.prompts(env)
        assert prompt["content"] == event.get_names_string(subscribed_only=True, mention=True)

    def test_deleted_message_is_resent_quietly_at_the_next_check(self, env, monkeypatch):
        event = self.make_waiting(env)
        old_message = event.voice_channel_prompt.message
        old_message.deleted = True
        # Not checked again until the interval passes
        run(event.update())
        assert len(self.prompts(env)) == 1
        event.voice_channel_prompt_checked_at -= env.sched.VOICE_CHANNEL_PROMPT_CHECK_INTERVAL
        run(event.update())
        prompts = self.prompts(env)
        assert len(prompts) == 2 and prompts[-1]["content"] is None
        assert event.voice_channel_prompt.message is not old_message

    def test_existing_message_is_kept(self, env):
        event = self.make_waiting(env)
        message = event.voice_channel_prompt.message
        event.voice_channel_prompt_checked_at -= env.sched.VOICE_CHANNEL_PROMPT_CHECK_INTERVAL
        run(event.update())
        assert event.voice_channel_prompt.message is message
        assert len(self.prompts(env)) == 1

    def test_failed_check_keeps_the_prompt(self, env, monkeypatch):
        event = self.make_waiting(env)
        prompt = event.voice_channel_prompt
        event.voice_channel_prompt_checked_at -= env.sched.VOICE_CHANNEL_PROMPT_CHECK_INTERVAL

        async def failing_fetch(message_id):
            raise RuntimeError("503 Service Unavailable")

        monkeypatch.setattr(env.text_channel, "fetch_message", failing_fetch)
        assert not run(event.voice_channel_prompt_deleted())
        assert event.voice_channel_prompt is prompt

    def test_channel_change_resends_quietly(self, env):
        event = env.make_event([env.make_participant("a")])
        delete_channel(env, env.voice_channel)
        add_voice_channel(env)
        run(event.update())
        assert self.prompts(env)[-1]["content"] is None


class TestMissedWhileWaitingForAVoiceChannel:
    def test_missed_occurrences_are_dropped(self, env):
        event, _ = make_created_event(env, days=(1, 2, 3))
        other = add_voice_channel(env)
        delete_channel(env, env.voice_channel)
        now = env.sched.now()
        upcoming = now + timedelta(days=1)
        event.start_times = [now - timedelta(days=3), now - timedelta(days=2), upcoming]
        env.update_messages_calls.clear()
        run(event.update())
        assert event.start_times == [upcoming]
        assert env.update_messages_calls == [event]
        run(event.move_to_voice_channel(other, "a"))
        run(event.update())
        assert [se.start_time for se in event.scheduled_events] == [upcoming]

    def test_event_is_cancelled_once_every_occurrence_is_missed(self, env):
        event, _ = make_created_event(env, days=(1, 2))
        add_voice_channel(env)
        delete_channel(env, env.voice_channel)
        now = env.sched.now()
        event.start_times = [now - timedelta(days=2), now - timedelta(days=1)]
        run(event.update())
        assert event.cancelled
        assert env.sched.client.schedule_again_events[-1].event is event

    def test_occurrence_within_the_grace_period_is_kept(self, env):
        event, _ = make_created_event(env)
        add_voice_channel(env)
        delete_channel(env, env.voice_channel)
        recent = env.sched.now() - timedelta(minutes=10)
        event.start_times = [recent]
        run(event.update())
        assert event.start_times == [recent] and not event.cancelled


class TestScheduleAgainAfterVoiceChannelDeleted:
    def submit(self, env, monkeypatch, event, start_time=""):
        calls = []

        async def fake_schedule(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(env.sched, "schedule", fake_schedule)
        monkeypatch.setattr(env.sched, "create", fake_schedule)

        async def go():
            modal = env.sched.ScheduleAgainModal(event=event, title="Schedule Again")
            modal.event_duration._value = "60"
            modal.event_start_time._value = start_time
            interaction = FakeInteraction(event.participants[0].member)
            await modal.on_submit(interaction)
            return interaction

        return run(go()), calls

    @pytest.mark.parametrize("start_time", ["", "tomorrow 8pm"])
    def test_uses_the_first_voice_channel_once_deleted(self, env, monkeypatch, start_time):
        event = env.make_event([env.make_participant("a")])
        other = add_voice_channel(env)
        delete_channel(env, env.voice_channel)
        assert event.voice_channel_missing
        monkeypatch.setattr(env.sched, "parse_start_time", lambda text: at(2, 20))
        _, (call,) = self.submit(env, monkeypatch, event, start_time)
        assert call["voice_channel"] is other and call["location"] is None

    def test_voice_channel_deleted_after_the_event_ended(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")])
        other = add_voice_channel(env)
        # Finished events aren't told about deleted channels, so they keep the old one
        env.guild.voice_channels.remove(env.voice_channel)
        _, (call,) = self.submit(env, monkeypatch, event)
        assert call["voice_channel"] is other

    def test_without_voice_channels_it_says_so(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")])
        delete_channel(env, env.voice_channel)
        interaction, calls = self.submit(env, monkeypatch, event)
        assert calls == []
        assert "there are no voice channels in the server" in interaction.followups[0]["content"]

    def test_typed_location_is_kept(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")], location="Park")
        _, (call,) = self.submit(env, monkeypatch, event)
        assert call["location"] == "Park" and call["voice_channel"] is None

    def test_buttons_load_after_voice_channel_deleted_while_offline(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")])
        run(event.cancel(schedule_again=True))
        (saved,) = env.sched.client.event_data["schedule_again_events"]
        env.sched.client.schedule_again_events.clear()
        env.guild.channels.pop(env.voice_channel.id)
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        restored = run(env.sched.AfterButtons.from_dict(saved))
        assert restored.event.voice_channel_missing


class TestVoiceChannelDeletedWhileOffline:
    def load(self, env, monkeypatch, data):
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        return run(env.sched.Event.from_dict(data))

    def test_loads_waiting_for_a_new_voice_channel(self, env, monkeypatch):
        event, scheduled_events = make_created_event(env, days=(1, 2))
        env.guild.created = list(scheduled_events)
        data = event.to_dict()
        env.sched.client.events.remove(event)
        env.guild.channels.pop(env.voice_channel.id)
        env.guild.voice_channels.remove(env.voice_channel)
        loaded = self.load(env, monkeypatch, data)
        assert loaded.voice_channel_missing
        assert loaded.start_times == event.start_times
        assert loaded.scheduled_events == []
        assert all(se.deleted for se in scheduled_events)
        assert loaded.location == env.sched.DELETED_VOICE_CHANNEL_LOCATION

    def test_reattaches_existing_prompt(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")])
        delete_channel(env, env.voice_channel)
        data = event.to_dict()
        prompt_message = event.voice_channel_prompt.message
        env.sched.client.events.remove(event)
        loaded = self.load(env, monkeypatch, data)
        assert loaded.voice_channel_prompt.message is prompt_message
        assert prompt_message.edits[-1]["view"] is loaded.voice_channel_prompt

    def test_sends_new_prompt_when_old_one_is_gone(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")])
        delete_channel(env, env.voice_channel)
        data = event.to_dict()
        event.voice_channel_prompt.message.deleted = True
        env.sched.client.events.remove(event)
        loaded = self.load(env, monkeypatch, data)
        assert loaded.voice_channel_prompt is None
        sent_before = len(env.text_channel.sent)
        run(loaded.update())
        assert loaded.voice_channel_prompt is not None
        assert len(env.text_channel.sent) == sent_before + 1

    def test_text_channel_deleted_while_offline_deletes_guild_events(self, env, monkeypatch):
        event, (scheduled_event,) = make_created_event(env)
        env.guild.created = [scheduled_event]
        data = event.to_dict()
        env.sched.client.events.remove(event)
        env.guild.channels.pop(env.text_channel.id)
        with pytest.raises(Exception, match="Could not find text channel"):
            self.load(env, monkeypatch, data)
        assert scheduled_event.deleted


class TestScheduledEventLocationKwargs:
    def test_recreated_guild_events_use_voice_entity_type(self, env):
        event, _ = make_created_event(env)
        delete_channel(env, env.voice_channel)
        new_voice_channel = add_voice_channel(env)
        run(event.move_to_voice_channel(new_voice_channel, "a"))
        run(event.update())
        (scheduled_event,) = event.scheduled_events
        assert scheduled_event.kwargs["entity_type"] == EntityType.voice
        assert scheduled_event.kwargs["channel"] is new_voice_channel
