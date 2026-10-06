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
                                                                 TimeBlock(at(1, 20), at(1, 22)))]
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
        delete_channel(env, env.voice_channel)
        prompt = event.voice_channel_prompt
        new_voice_channel = add_voice_channel(env)
        prompt.channel_select._values = [SimpleNamespace(id=new_voice_channel.id)]
        interaction = FakeInteraction(SimpleNamespace(name="a", nick=None))
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
        delete_channel(env, env.voice_channel)
        prompt = event.voice_channel_prompt
        prompt.channel_select._values = [SimpleNamespace(id=12345)]
        interaction = FakeInteraction(SimpleNamespace(name="a", nick=None))
        run(prompt.channel_select_callback(interaction))
        assert event.voice_channel_missing
        assert interaction.responses[0][0] == "send_message"

    def test_cancel_button_opens_cancel_modal(self, env):
        event = env.make_event([env.make_participant("a")])
        delete_channel(env, env.voice_channel)
        interaction = FakeInteraction(SimpleNamespace(name="a", nick=None))
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
        assert "voice channel was deleted" in interaction.followups[0]["content"]

    def test_timeout_still_applies_while_missing(self, env):
        event = env.make_event([env.make_participant("a")])
        delete_channel(env, env.voice_channel)
        event.timeout_at = env.sched.now() - timedelta(minutes=1)
        run(event.update())
        assert event not in env.sched.client.events


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
        assert loaded.location_string == "(Deleted voice channel)"

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
