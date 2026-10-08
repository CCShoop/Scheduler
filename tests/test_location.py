import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from discord import EntityType, EventStatus

from libs.participant import TimeBlock
from fakes import FakeScheduledEvent, at, run


def make_external_event(env, location="123 Main St", participants=None, **kwargs):
    if participants is None:
        participants = [env.make_participant("a", [TimeBlock(at(1, 18), at(2, 23))])]
    return env.make_event(participants, location=location, **kwargs)


def make_created_external_event(env, start_time, **kwargs):
    scheduled_event = FakeScheduledEvent(start_time)
    event = make_external_event(env, created=True, start_times=[start_time],
                                scheduled_events=[scheduled_event], **kwargs)
    event.event_buttons = SimpleNamespace(start_end_button=SimpleNamespace(disabled=False), convert=lambda: None)
    return event, scheduled_event


class TestResolveLocation:
    def test_voice_channel_id_from_suggestion(self, env):
        assert env.sched.resolve_location(env.guild, str(env.voice_channel.id)) == (env.voice_channel, None)

    @pytest.mark.parametrize("typed", ["General", "general", "#General", "  General "])
    def test_voice_channel_name(self, env, typed):
        assert env.sched.resolve_location(env.guild, typed) == (env.voice_channel, None)

    def test_other_text_is_external(self, env):
        assert env.sched.resolve_location(env.guild, " 123 Main St ") == (None, "123 Main St")

    def test_number_that_is_not_a_voice_channel_is_external(self, env):
        assert env.sched.resolve_location(env.guild, "42") == (None, "42")

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_empty(self, env, empty):
        assert env.sched.resolve_location(env.guild, empty) == (None, None)

    def test_too_long(self, env):
        with pytest.raises(Exception, match="100 characters"):
            env.sched.resolve_location(env.guild, "x" * 101)


class TestLocationAutocomplete:
    def choices(self, env, current):
        interaction = SimpleNamespace(guild=env.guild)
        return [(choice.name, choice.value) for choice in run(env.sched.location_autocomplete(interaction, current))]

    def test_suggests_voice_channels_when_empty(self, env):
        assert self.choices(env, "") == [("🔊 General", str(env.voice_channel.id))]

    def test_filters_voice_channels_and_offers_typed_text(self, env):
        assert self.choices(env, "gen") == [("🔊 General", str(env.voice_channel.id)), ("📍 gen", "gen")]

    def test_offers_typed_location(self, env):
        assert self.choices(env, "123 Main St") == [("📍 123 Main St", "123 Main St")]

    def test_exact_voice_channel_name_is_not_offered_as_location(self, env):
        assert self.choices(env, "general") == [("🔊 General", str(env.voice_channel.id))]


class TestExternalEvent:
    def test_attributes(self, env):
        event = make_external_event(env)
        assert event.is_external
        assert event.voice_channel is None
        assert event.entity_type == EntityType.external
        assert event.location_string == "123 Main St"

    def test_voice_event_attributes(self, env):
        event = env.make_event([env.make_participant("a")])
        assert not event.is_external
        assert event.entity_type == EntityType.voice
        assert event.location_string == env.voice_channel.mention

    def test_guild_event_has_location_and_end_time(self, env):
        event = make_external_event(env, start_times=[at(1, 20)], duration=timedelta(hours=2))
        run(event.make_scheduled_events())
        (scheduled_event,) = env.guild.created
        assert scheduled_event.kwargs["entity_type"] == EntityType.external
        assert scheduled_event.kwargs["location"] == "123 Main St"
        assert scheduled_event.kwargs["end_time"] == at(1, 22)
        assert "channel" not in scheduled_event.kwargs

    def test_automatic_duration_uses_default_end_time(self, env):
        event = make_external_event(env, start_times=[at(1, 20)], duration=timedelta(0))
        run(event.make_scheduled_events())
        assert env.guild.created[0].kwargs["end_time"] == at(1, 20) + timedelta(minutes=env.sched.DEFAULT_EVENT_DURATION)

    def test_voice_guild_event_has_channel(self, env):
        event = env.make_event([env.make_participant("a")], start_times=[at(1, 20)])
        run(event.make_scheduled_events())
        assert env.guild.created[0].kwargs["channel"] is env.voice_channel
        assert "location" not in env.guild.created[0].kwargs

    def test_same_location_ignores_case(self, env):
        event = make_external_event(env)
        other = make_external_event(env, location="123 main st")
        voice = env.make_event([env.make_participant("b")])
        assert event.same_location(other)
        assert not event.same_location(voice)

    def test_avoids_created_events_at_the_same_location(self, env):
        occupying = make_external_event(env, location="123 MAIN ST", participants=[env.make_participant("b")],
                                        start_times=[at(1, 18)], created=True)
        event = make_external_event(env)
        event.compare_availabilities()
        assert event.start_times == [occupying.start_times[0] + occupying.duration]

    def test_voice_channel_events_do_not_block_external_events(self, env):
        env.make_event([env.make_participant("b")], start_times=[at(1, 18)], created=True)
        event = make_external_event(env)
        event.compare_availabilities()
        assert event.start_times == [at(1, 18)]

    def test_names_string_ignores_voice_channel(self, env):
        event = make_external_event(env)
        assert event.get_names_string(not_in_voice_channel_only=True) == "a"


class TestExternalLifecycle:
    @pytest.fixture(autouse=True)
    def bot_user(self, env, monkeypatch):
        # Reminder embeds use the bot's avatar in the footer
        bot_user = SimpleNamespace(avatar=SimpleNamespace(url="https://example.invalid/avatar.png"))
        monkeypatch.setattr(type(env.sched.client), "user", property(lambda self: bot_user))

    def test_starts_at_start_time(self, env):
        event, scheduled_event = make_created_external_event(env, env.sched.now(), reminder_flag=True)
        run(event.update())
        assert event.started
        assert scheduled_event.status == EventStatus.active

    def test_does_not_start_before_start_time(self, env):
        event, _ = make_created_external_event(env, env.sched.now() + timedelta(minutes=5), reminder_flag=True)
        run(event.update())
        assert not event.started

    def test_does_not_start_while_another_event_is_running_there(self, env):
        event, _ = make_created_external_event(env, env.sched.now(), reminder_flag=True)
        make_external_event(env, participants=[env.make_participant("b")], created=True, started=True,
                            start_times=[env.sched.now()])
        run(event.update())
        assert not event.started

    def test_sends_reminder_even_if_everyone_is_in_a_voice_channel(self, env):
        event, _ = make_created_external_event(env, env.sched.now() + timedelta(minutes=5))
        env.voice_channel.members.append(event.participants[0].member)
        run(event.update())
        assert event.reminder_message is not None

    def test_keeps_running_until_duration_passes(self, env):
        event, _ = make_created_external_event(env, env.sched.now() - timedelta(minutes=30), started=True)
        run(event.update())
        assert not event.ended

    def test_ends_when_duration_passes(self, env):
        event, scheduled_event = make_created_external_event(env, env.sched.now() - timedelta(hours=2), started=True)
        run(event.update())
        assert event.ended
        assert scheduled_event.deleted


class TestEditLocation:
    def test_voice_to_external(self, env):
        event = env.make_event([env.make_participant("a")], created=True, start_times=[at(1, 20)],
                               scheduled_events=[FakeScheduledEvent(at(1, 20))])
        embed = run(env.sched.edit_event(event, location="Park"))
        assert event.location == "Park" and event.voice_channel is None
        assert event.scheduled_events[0].edits == [{"entity_type": EntityType.external,
                                                    "location": "Park",
                                                    "end_time": at(1, 22)}]
        assert embed.fields[0].name == "Location"
        assert embed.fields[0].value == f"{env.voice_channel.mention} -> Park"

    def test_external_to_voice(self, env):
        event = make_external_event(env, created=True, start_times=[at(1, 20)],
                                    scheduled_events=[FakeScheduledEvent(at(1, 20))])
        run(env.sched.edit_event(event, voice_channel=env.voice_channel))
        assert event.location is None and event.voice_channel is env.voice_channel
        assert event.scheduled_events[0].edits == [{"entity_type": EntityType.voice, "channel": env.voice_channel}]

    def test_unchanged(self, env):
        event = make_external_event(env)
        embed = run(env.sched.edit_event(event, location="123 MAIN ST"))
        assert embed.fields[0].name == "Location (Unchanged)"


class TestCreateAndSchedule:
    def test_create_at_external_location(self, env):
        a = env.make_participant("a")
        event = run(env.sched.create(event_name="Board Games", guild=env.guild, text_channel=env.text_channel,
                                     voice_channel=None, location="123 Main St", start_time=at(1, 20),
                                     scheduler_id=a.member.id, usernames=[a]))
        assert event.location == "123 Main St"
        assert env.guild.created[0].kwargs["location"] == "123 Main St"

    def test_create_rejects_overlap_at_the_same_location(self, env):
        make_external_event(env, participants=[env.make_participant("b")], start_times=[at(1, 20)], created=True)
        a = env.make_participant("a")
        with pytest.raises(Exception, match="same location"):
            run(env.sched.create(event_name="Board Games", guild=env.guild, text_channel=env.text_channel,
                                 voice_channel=None, location="123 main st", start_time=at(1, 21),
                                 scheduler_id=a.member.id, usernames=[a]))

    def test_create_allows_overlap_with_voice_channel_event(self, env):
        env.make_event([env.make_participant("b")], start_times=[at(1, 20)], created=True)
        a = env.make_participant("a")
        event = run(env.sched.create(event_name="Board Games", guild=env.guild, text_channel=env.text_channel,
                                     voice_channel=None, location="123 Main St", start_time=at(1, 21),
                                     scheduler_id=a.member.id, usernames=[a]))
        assert event in env.sched.client.events

    def test_create_without_location_uses_first_voice_channel(self, env):
        a = env.make_participant("a")
        event = run(env.sched.create(event_name="Game Night", guild=env.guild, text_channel=env.text_channel,
                                     voice_channel=None, start_time=at(1, 20),
                                     scheduler_id=a.member.id, usernames=[a]))
        assert event.voice_channel is env.voice_channel and not event.is_external

    def test_external_location_does_not_need_a_voice_channel(self, env):
        env.guild.voice_channels = []
        a = env.make_participant("a")
        event = run(env.sched.schedule(event_name="Board Games", guild=env.guild, text_channel=env.text_channel,
                                       voice_channel=None, location="123 Main St",
                                       scheduler_id=a.member.id, usernames=[a]))
        assert event.location == "123 Main St"

    def test_schedule_from_dict_with_location(self, env, monkeypatch):
        a = env.make_participant("a")
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        run(env.sched.client.schedule_from_dict({
            "name": "Board Games", "guild_id": env.guild.id, "text_channel_id": env.text_channel.id,
            "location": "123 Main St", "scheduler_id": a.member.id, "image_url": None,
            "include_exclude": "INCLUDE", "usernames": [a], "roles": None, "duration": 0, "multi_event": False,
        }))
        (event,) = env.sched.client.events
        assert event.location == "123 Main St"

    def test_create_packet_makes_event_at_start_time(self, env, monkeypatch):
        a = env.make_participant("a")
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        start = at(1, 19)
        run(env.sched.client.handle_packet({
            "type": "create", "name": "Range", "guild_id": env.guild.id, "text_channel_id": env.text_channel.id,
            "location": "123 Main St", "scheduler_id": a.member.id, "start_time": start.isoformat(),
            "image_url": None, "include_exclude": "INCLUDE", "usernames": [a], "roles": None, "duration": 60,
        }))
        (event,) = env.sched.client.events
        assert event.start_times == [start] and event.location == "123 Main St"

    @pytest.mark.parametrize("packet_type", ["schedule", None])
    def test_schedule_packet_and_untyped_packet_start_scheduling(self, env, monkeypatch, packet_type):
        called = []
        monkeypatch.setattr(env.sched.client, "schedule_from_dict", lambda data: called.append(data) or asyncio.sleep(0))
        data = {"name": "Game Night"} if packet_type is None else {"type": packet_type, "name": "Game Night"}
        run(env.sched.client.handle_packet(data))
        assert called == [data]

    def test_unknown_packet_type_is_rejected(self, env):
        with pytest.raises(Exception, match="unknown packet type"):
            run(env.sched.client.handle_packet({"type": "delete", "name": "x"}))


class TestPersistence:
    def test_round_trip(self, env, monkeypatch):
        event = make_external_event(env)
        data = event.to_dict()
        env.sched.client.events.remove(event)
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        loaded = run(env.sched.Event.from_dict(data))
        assert loaded.location == "123 Main St"
        assert loaded.voice_channel is None

    def test_old_data_without_location_loads_voice_channel(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")])
        data = event.to_dict()
        del data["location"]
        env.sched.client.events.remove(event)
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        loaded = run(env.sched.Event.from_dict(data))
        assert loaded.voice_channel is env.voice_channel and not loaded.is_external
