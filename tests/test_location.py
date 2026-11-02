import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from discord import EntityType, EventStatus

from libs.participant import Participant, TimeBlock
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

    @pytest.mark.parametrize("submitted", ["🔊 General", "🔊General", " 🔊  general "])
    def test_voice_channel_suggestion_name(self, env, submitted):
        assert env.sched.resolve_location(env.guild, submitted) == (env.voice_channel, None)

    def test_external_suggestion_name(self, env):
        assert env.sched.resolve_location(env.guild, "📍 123 Main St") == (None, "123 Main St")

    def test_suggestion_prefix_alone_is_empty(self, env):
        assert env.sched.resolve_location(env.guild, "🔊") == (None, None)

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

    def test_voice_guild_event_has_channel_and_end_time(self, env):
        event = env.make_event([env.make_participant("a")], start_times=[at(1, 20)], duration=timedelta(minutes=24))
        run(event.make_scheduled_events())
        assert env.guild.created[0].kwargs["channel"] is env.voice_channel
        assert env.guild.created[0].kwargs["end_time"] == at(1, 20) + timedelta(minutes=24)
        assert "location" not in env.guild.created[0].kwargs

    def test_external_events_never_share_a_voice_channel(self, env):
        event = make_external_event(env)
        other = make_external_event(env, location="123 main st")
        voice = env.make_event([env.make_participant("b")])
        assert not event.shares_voice_channel(other)
        assert not event.shares_voice_channel(voice)

    def test_overlaps_created_events_at_the_same_location(self, env):
        make_external_event(env, location="123 MAIN ST", participants=[env.make_participant("b")],
                            start_times=[at(1, 18)], created=True)
        event = make_external_event(env)
        event.compare_availabilities()
        assert event.start_times == [at(1, 18)]

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

    def test_starts_while_another_event_is_running_there(self, env):
        event, _ = make_created_external_event(env, env.sched.now(), reminder_flag=True)
        running = make_external_event(env, participants=[env.make_participant("b")], created=True, started=True,
                                      start_times=[env.sched.now()])
        run(event.update())
        assert event.started
        assert running.started and not running.ended

    def test_starting_does_not_push_back_events_at_the_same_location(self, env):
        later_start = env.sched.now() + timedelta(minutes=30)
        later = make_external_event(env, participants=[env.make_participant("b")], created=True,
                                    start_times=[later_start], scheduled_events=[FakeScheduledEvent(later_start)])
        event, _ = make_created_external_event(env, env.sched.now(), reminder_flag=True)
        run(event.update())
        assert event.started
        assert later.start_times == [later_start]

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
        assert event.scheduled_events[0].edits == [{"entity_type": EntityType.voice, "channel": env.voice_channel,
                                                   "end_time": at(1, 22)}]

    def test_unchanged(self, env):
        event = make_external_event(env)
        embed = run(env.sched.edit_event(event, location="123 MAIN ST"))
        assert embed.fields[0].name == "Location (Unchanged)"


class TestRestoreGuildEvent:
    @pytest.fixture(autouse=True)
    def no_delay(self, env, monkeypatch):
        monkeypatch.setattr(env.sched, "GUILD_EVENT_RESTORE_DELAY_SECONDS", 0)

    def make_created_voice_event(self, env, **kwargs):
        scheduled_event = FakeScheduledEvent(at(1, 20), channel=env.voice_channel, end_time=at(1, 22), **kwargs)
        env.guild.created.append(scheduled_event)
        event = env.make_event([env.make_participant("a")], created=True, start_times=[at(1, 20)],
                               scheduled_events=[scheduled_event])
        scheduled_event.kwargs["name"] = event.name
        event.remember_guild_event(scheduled_event)
        return event, scheduled_event

    def restore(self, env, event, scheduled_event):
        async def go():
            await env.sched.on_scheduled_event_update(scheduled_event, scheduled_event)
            await event.guild_event_restores[scheduled_event.id]
        run(go())

    def test_unchanged_guild_event_is_left_alone(self, env):
        event, scheduled_event = self.make_created_voice_event(env)
        assert run(event.restore_guild_event(scheduled_event.id)) == []
        assert scheduled_event.edits == []

    def test_location_moved_in_discord_is_moved_back(self, env):
        event, scheduled_event = self.make_created_voice_event(env)
        scheduled_event.kwargs.update(entity_type=EntityType.external, location="Park", channel=None)
        self.restore(env, event, scheduled_event)
        assert event.voice_channel is env.voice_channel and event.location is None
        assert scheduled_event.edits == [{"entity_type": EntityType.voice, "channel": env.voice_channel, "end_time": at(1, 22)}]

    def test_name_times_and_description_are_put_back(self, env):
        event, scheduled_event = self.make_created_voice_event(env, description="Bot-generated event")
        scheduled_event.kwargs.update(name="Free nitro", description="join my server", end_time=at(1, 23))
        scheduled_event.start_time = at(1, 21)
        restored = run(event.restore_guild_event(scheduled_event.id))
        assert restored == ["name", "description", "start_time", "end_time"]
        assert scheduled_event.edits == [{"name": event.name, "description": "Bot-generated event",
                                          "start_time": at(1, 20), "end_time": at(1, 22)}]

    def test_small_time_differences_count_as_unchanged(self, env):
        event, scheduled_event = self.make_created_voice_event(env)
        scheduled_event.start_time = at(1, 20) + timedelta(seconds=30)
        assert run(event.restore_guild_event(scheduled_event.id)) == []

    def test_started_guild_event_keeps_its_start_time(self, env):
        event, scheduled_event = self.make_created_voice_event(env, status=EventStatus.active)
        scheduled_event.start_time = at(1, 21)
        assert run(event.restore_guild_event(scheduled_event.id)) == []

    def test_end_time_in_the_past_is_left_alone(self, env):
        start = env.sched.now() - timedelta(hours=3)
        event, scheduled_event = self.make_created_voice_event(env, status=EventStatus.active)
        event.start_times = [start]
        scheduled_event.start_time = start
        scheduled_event.kwargs["end_time"] = start + timedelta(hours=4)
        assert run(event.restore_guild_event(scheduled_event.id)) == []

    def test_image_change_is_put_back(self, env, monkeypatch):
        event, scheduled_event = self.make_created_voice_event(env, cover_image=SimpleNamespace(key="bot"))
        monkeypatch.setattr(env.sched.Event, "has_image_saved", property(lambda self: True))
        monkeypatch.setattr(env.sched.Event, "get_image", lambda self: b"bot image")
        scheduled_event.kwargs["cover_image"] = SimpleNamespace(key="spam")
        assert run(event.restore_guild_event(scheduled_event.id)) == ["image"]
        assert scheduled_event.edits == [{"image": b"bot image"}]

    def test_added_image_is_removed(self, env):
        event, scheduled_event = self.make_created_voice_event(env)
        scheduled_event.kwargs["cover_image"] = SimpleNamespace(key="spam")
        assert run(event.restore_guild_event(scheduled_event.id)) == ["image"]
        assert scheduled_event.edits == [{"image": None}]

    def test_restore_remembers_what_it_set(self, env):
        event, scheduled_event = self.make_created_voice_event(env)
        scheduled_event.kwargs["cover_image"] = SimpleNamespace(key="spam")

        async def edit(**kwargs):
            scheduled_event.edits.append(kwargs)
            edited = FakeScheduledEvent(at(1, 20), cover_image=SimpleNamespace(key="new"))
            edited.id = scheduled_event.id
            return edited

        scheduled_event.edit = edit
        run(event.restore_guild_event(scheduled_event.id))
        assert event.guild_event_image_keys[scheduled_event.id] == "new"

    @pytest.mark.parametrize("status", [EventStatus.ended, EventStatus.cancelled])
    def test_ended_or_cancelled_guild_event_is_left_to_update(self, env, status):
        event, scheduled_event = self.make_created_voice_event(env, status=status)
        scheduled_event.kwargs["name"] = "renamed"
        assert run(event.restore_guild_event(scheduled_event.id)) == []

    def test_missing_voice_channel_or_unknown_guild_event_is_left_alone(self, env):
        event, scheduled_event = self.make_created_voice_event(env)
        scheduled_event.kwargs["name"] = "renamed"
        assert run(event.restore_guild_event(12345)) == []
        event.location = env.sched.DELETED_VOICE_CHANNEL_LOCATION
        assert run(event.restore_guild_event(scheduled_event.id)) == []

    def test_unmanaged_guild_event_is_ignored(self, env):
        event, scheduled_event = self.make_created_voice_event(env)
        other = FakeScheduledEvent(at(1, 20))
        run(env.sched.on_scheduled_event_update(other, other))
        assert event.guild_event_restores == {}

    def test_several_updates_restore_once(self, env):
        event, scheduled_event = self.make_created_voice_event(env)
        scheduled_event.kwargs["name"] = "renamed"

        async def go():
            await env.sched.on_scheduled_event_update(scheduled_event, scheduled_event)
            task = event.guild_event_restores[scheduled_event.id]
            await env.sched.on_scheduled_event_update(scheduled_event, scheduled_event)
            assert event.guild_event_restores[scheduled_event.id] is task
            await task
        run(go())
        assert scheduled_event.edits == [{"name": event.name}]

    def test_restore_errors_are_logged(self, env, caplog):
        event, scheduled_event = self.make_created_voice_event(env)
        scheduled_event.kwargs["name"] = "renamed"

        async def edit(**kwargs):
            raise RuntimeError("403 Missing Permissions")

        scheduled_event.edit = edit
        self.restore(env, event, scheduled_event)
        assert "403 Missing Permissions" in caplog.text


class TestRecreateDeletedGuildEvent:
    @pytest.fixture(autouse=True)
    def no_delay(self, env, monkeypatch):
        monkeypatch.setattr(env.sched, "GUILD_EVENT_RESTORE_DELAY_SECONDS", 0)

    def make_created(self, env, days=(1,), **kwargs):
        scheduled_events = [FakeScheduledEvent(at(day, 20), channel=env.voice_channel) for day in days]
        env.guild.created.extend(scheduled_events)
        event = env.make_event([env.make_participant("a")], created=True, multi_event=len(days) > 1,
                               start_times=[at(day, 20) for day in days], scheduled_events=list(scheduled_events), **kwargs)
        return event, scheduled_events

    def delete_in_discord(self, env, scheduled_event):
        async def go():
            scheduled_event.deleted = True
            await env.sched.on_scheduled_event_delete(scheduled_event)
            # Lets the queued recreation run
            for _ in range(5):
                await asyncio.sleep(0.02)
        run(go())

    def test_deleted_guild_event_is_recreated(self, env):
        event, (scheduled_event,) = self.make_created(env)
        self.delete_in_discord(env, scheduled_event)
        (recreated,) = event.scheduled_events
        assert recreated is not scheduled_event and not recreated.deleted
        assert recreated.start_time == at(1, 20) and recreated.kwargs["channel"] is env.voice_channel

    def test_middle_occurrence_is_recreated_with_later_ones(self, env):
        event, (first, second, third) = self.make_created(env, days=(1, 2, 3))
        self.delete_in_discord(env, second)
        assert event.scheduled_events[0] is first
        assert third.deleted
        assert [se.start_time for se in event.scheduled_events] == [at(1, 20), at(2, 20), at(3, 20)]
        assert not any(se.deleted for se in event.scheduled_events)

    def test_guild_events_deleted_by_the_bot_are_not_recreated(self, env):
        event, (scheduled_event,) = self.make_created(env)
        run(event.delete_guild_event(scheduled_event, reason="test"))
        self.delete_in_discord(env, scheduled_event)
        assert event.scheduled_events == [scheduled_event]
        assert env.guild.created == [scheduled_event]
        assert event.bot_deleted_guild_event_ids == set()

    def test_bot_deletion_noticed_after_it_stopped_tracking(self, env):
        event, (scheduled_event,) = self.make_created(env)
        run(event.delete_guild_event(scheduled_event, reason="test"))
        event.scheduled_events.clear()
        self.delete_in_discord(env, scheduled_event)
        assert event.bot_deleted_guild_event_ids == set()

    def test_started_guild_event_deleted_ends_the_event(self, env, monkeypatch):
        event, (scheduled_event,) = self.make_created(env, started=True)
        ended = []

        async def fake_end(self, reason=None, forget=False):
            ended.append(reason)

        monkeypatch.setattr(env.sched.Event, "end", fake_end)
        assert run(event.recreate_guild_event(scheduled_event.id))
        assert ended == ["Event ended manually."]

    def test_waiting_for_a_voice_channel_is_left_alone(self, env):
        event, (scheduled_event,) = self.make_created(env)
        event.location = env.sched.DELETED_VOICE_CHANNEL_LOCATION
        assert not run(event.recreate_guild_event(scheduled_event.id))
        assert not run(event.recreate_guild_event(12345))

    def test_recreate_errors_are_logged(self, env, monkeypatch, caplog):
        event, (scheduled_event,) = self.make_created(env)

        async def failing(self):
            raise RuntimeError("403 Missing Permissions")

        monkeypatch.setattr(env.sched.Event, "make_scheduled_events", failing)
        self.delete_in_discord(env, scheduled_event)
        assert "403 Missing Permissions" in caplog.text

    def test_unmanaged_guild_event_is_ignored(self, env):
        event, _ = self.make_created(env)
        other = FakeScheduledEvent(at(1, 20))
        self.delete_in_discord(env, other)
        assert len(env.guild.created) == 1


class TestUpdateChangedAvailabilityMessages:
    def test_updates_only_events_whose_availability_changed(self, env):
        changed = env.make_event([env.make_participant("b", [TimeBlock(at(1, 18), at(1, 20))])])
        unchanged = env.make_event([env.make_participant("c", [TimeBlock(at(1, 18), at(1, 20))])])
        availability_states = env.sched.get_availability_states()
        changed.participants[0].availability[0].end_time = at(1, 21)
        run(env.sched.update_changed_availability_messages(availability_states))
        assert env.update_availability_message_calls == [changed]

    def test_skips_excluded_and_removed_events(self, env):
        excluded = env.make_event([env.make_participant("b", [TimeBlock(at(1, 18), at(1, 20))])])
        removed = env.make_event([env.make_participant("c", [TimeBlock(at(1, 18), at(1, 20))])])
        availability_states = env.sched.get_availability_states()
        excluded.participants[0].availability[0].end_time = at(1, 21)
        removed.participants[0].availability[0].end_time = at(1, 21)
        env.sched.client.events.remove(removed)
        run(env.sched.update_changed_availability_messages(availability_states, exclude=excluded))
        assert env.update_availability_message_calls == []

    def test_creating_an_event_updates_only_events_it_takes_availability_from(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        a_elsewhere = Participant(member=a.member, availability=[TimeBlock(at(1, 18), at(1, 23))])
        affected = env.make_event([a_elsewhere])
        unaffected = env.make_event([env.make_participant("b", [TimeBlock(at(1, 18), at(1, 23))])])
        run(env.sched.create(event_name="New", guild=env.guild, text_channel=env.text_channel,
                             voice_channel=env.voice_channel, start_time=at(1, 20), scheduler_id=a.member.id,
                             usernames=[a]))
        assert affected in env.update_availability_message_calls
        assert unaffected not in env.update_availability_message_calls


class TestCreateAndSchedule:
    def test_create_at_external_location(self, env):
        a = env.make_participant("a")
        event = run(env.sched.create(event_name="Board Games", guild=env.guild, text_channel=env.text_channel,
                                     voice_channel=None, location="123 Main St", start_time=at(1, 20),
                                     scheduler_id=a.member.id, usernames=[a]))
        assert event.location == "123 Main St"
        assert env.guild.created[0].kwargs["location"] == "123 Main St"

    def test_create_allows_overlap_at_the_same_location(self, env):
        make_external_event(env, participants=[env.make_participant("b")], start_times=[at(1, 20)], created=True)
        a = env.make_participant("a")
        event = run(env.sched.create(event_name="Board Games", guild=env.guild, text_channel=env.text_channel,
                                     voice_channel=None, location="123 main st", start_time=at(1, 21),
                                     scheduler_id=a.member.id, usernames=[a]))
        assert event in env.sched.client.events

    def test_create_rejects_overlap_in_the_same_voice_channel(self, env):
        env.make_event([env.make_participant("b")], start_times=[at(1, 20)], created=True)
        a = env.make_participant("a")
        with pytest.raises(Exception, match="same voice channel"):
            run(env.sched.create(event_name="Game Night", guild=env.guild, text_channel=env.text_channel,
                                 voice_channel=env.voice_channel, start_time=at(1, 21),
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
        a.member.bot = False
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        run(env.sched.client.schedule_from_dict({
            "name": "Board Games", "guild_id": env.guild.id, "text_channel_id": env.text_channel.id,
            "location": "123 Main St", "scheduler_id": a.member.id, "image_url": None,
            "include_exclude": "INCLUDE", "usernames": [a.member.id], "roles": None, "duration": 0, "multi_event": False,
        }))
        (event,) = env.sched.client.events
        assert event.location == "123 Main St"

    def test_create_packet_makes_event_at_start_time(self, env, monkeypatch):
        a = env.make_participant("a")
        a.member.bot = False
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        start = at(1, 19)
        run(env.sched.client.handle_packet({
            "type": "create", "name": "Range", "guild_id": env.guild.id, "text_channel_id": env.text_channel.id,
            "location": "123 Main St", "scheduler_id": a.member.id, "start_time": start.isoformat(),
            "image_url": None, "include_exclude": "INCLUDE", "usernames": [a.member.id], "roles": None, "duration": 60,
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

    def test_scheduler_who_is_not_a_participant_is_restored(self, env, monkeypatch):
        a = env.make_participant("a")
        bot = env.make_participant("bot")
        env.text_channel.members.remove(bot.member)
        event = make_external_event(env, participants=[a], scheduler=bot)
        data = event.to_dict()
        env.sched.client.events.remove(event)
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        loaded = run(env.sched.Event.from_dict(data))
        assert loaded.scheduler.member is bot.member
        assert [participant.member for participant in loaded.participants] == [a.member]

    def test_scheduler_who_is_a_participant_is_restored_as_one(self, env, monkeypatch):
        a = env.make_participant("a")
        event = make_external_event(env, participants=[a], scheduler=a)
        data = event.to_dict()
        env.sched.client.events.remove(event)
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        loaded = run(env.sched.Event.from_dict(data))
        assert loaded.scheduler is loaded.participants[0]

    def test_old_data_without_location_loads_voice_channel(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")])
        data = event.to_dict()
        del data["location"]
        env.sched.client.events.remove(event)
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        loaded = run(env.sched.Event.from_dict(data))
        assert loaded.voice_channel is env.voice_channel and not loaded.is_external
