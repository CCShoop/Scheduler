import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from discord import EntityType, EventStatus

from libs.participant import Participant, TimeBlock
from fakes import FakeScheduledEvent, at, noop, run

class TestIntersectTimeBlocks:
    def test_returns_only_overlaps(self, env):
        event = env.make_event([env.make_participant("a")])
        result = event.intersect_time_blocks([TimeBlock(at(1, 8), at(1, 12)), TimeBlock(at(2, 8), at(2, 9))],
                                             [TimeBlock(at(1, 10), at(1, 14)), TimeBlock(at(3, 8), at(3, 9))])
        assert [(tb.start_time, tb.end_time) for tb in result] == [(at(1, 10), at(1, 12))]


class TestCompareAvailabilities:
    def test_picks_earliest_common_block_long_enough(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 8), at(1, 9)), TimeBlock(at(1, 18), at(1, 23))])
        b = env.make_participant("b", [TimeBlock(at(1, 8), at(1, 12)), TimeBlock(at(1, 20), at(1, 23))])
        event = env.make_event([a, b])
        event.compare_availabilities()
        # 8-9 is common but shorter than the 2 hour duration
        assert event.start_times == [at(1, 20)]
        assert event.ready_to_create

    def test_no_common_block(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 8), at(1, 10))])
        b = env.make_participant("b", [TimeBlock(at(1, 12), at(1, 14))])
        event = env.make_event([a, b])
        event.compare_availabilities()
        assert event.start_times == []
        assert not event.ready_to_create

    def test_starts_soon_when_everyone_is_available_now(self, env):
        now = env.sched.now()
        a = env.make_participant("a", [TimeBlock(now - timedelta(hours=1), now + timedelta(hours=5))])
        event = env.make_event([a])
        event.compare_availabilities()
        assert event.start_times == [now + timedelta(minutes=env.sched.START_TIME_DELAY)]

    def test_window_too_short_after_the_start_delay_is_skipped(self, env):
        now = env.sched.now()
        # 50 minutes fits a 30 minute event now, but not one starting START_TIME_DELAY from now
        a = env.make_participant("a", [TimeBlock(now, now + timedelta(minutes=50)),
                                       TimeBlock(at(1, 20), at(1, 22))])
        event = env.make_event([a], duration=timedelta(minutes=30))
        event.compare_availabilities()
        assert event.start_times == [at(1, 20)]

    def test_window_starting_soon_starts_after_the_start_delay(self, env):
        now = env.sched.now()
        a = env.make_participant("a", [TimeBlock(now + timedelta(minutes=10), now + timedelta(hours=3))])
        b = env.make_participant("b", [TimeBlock(now - timedelta(hours=1), now + timedelta(hours=3))])
        event = env.make_event([a, b], duration=timedelta(minutes=30))
        event.compare_availabilities()
        assert event.start_times == [now + timedelta(minutes=env.sched.START_TIME_DELAY)]

    def test_created_event_fits_availability_without_being_moved(self, env, caplog):
        now = env.sched.now()
        a = env.make_participant("a", [TimeBlock(now, now + timedelta(minutes=50)),
                                       TimeBlock(at(1, 20), at(1, 22))])
        event = env.make_event([a], duration=timedelta(minutes=30))
        run(event.create_if_possible())
        assert [se.start_time for se in env.guild.created] == [at(1, 20)]
        assert not any("in the past" in record.message for record in caplog.records)

    def test_multi_event_picks_one_start_per_day(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 8), at(1, 10)), TimeBlock(at(1, 20), at(1, 22)),
                                       TimeBlock(at(2, 20), at(2, 22)), TimeBlock(at(3, 20), at(3, 22))])
        event = env.make_event([a], multi_event=True)
        event.compare_availabilities()
        assert event.start_times == [at(1, 8), at(2, 20), at(3, 20)]

    def test_single_event_keeps_only_first_start(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 20), at(1, 22)), TimeBlock(at(2, 20), at(2, 22))])
        event = env.make_event([a])
        event.compare_availabilities()
        assert event.start_times == [at(1, 20)]

    def test_avoids_created_events_in_the_same_voice_channel(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        occupying = env.make_event([env.make_participant("b")], start_times=[at(1, 18)], created=True)
        event = env.make_event([a])
        event.compare_availabilities()
        assert event.start_times == [occupying.start_times[0] + occupying.duration]

    def test_avoids_later_occurrences_of_a_multi_event_in_the_same_voice_channel(self, env):
        a = env.make_participant("a", [TimeBlock(at(2, 18), at(2, 23))])
        occupying = env.make_event([env.make_participant("b")], multi_event=True, created=True,
                                   start_times=[at(1, 18), at(2, 18)])
        event = env.make_event([a])
        event.compare_availabilities()
        assert event.start_times == [at(2, 18) + occupying.duration]

    def test_does_nothing_once_created(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        event = env.make_event([a], created=True, start_times=[at(1, 20)])
        event.compare_availabilities()
        assert event.start_times == [at(1, 20)]


class TestCreation:
    def test_concurrent_create_if_possible_creates_once(self, env):
        """Regression: the update loop and an input callback both creating guild events."""
        a = env.make_participant("a", [TimeBlock(at(1, 20), at(1, 22))])
        event = env.make_event([a])

        async def both():
            await asyncio.gather(event.create_if_possible(), event.create_if_possible())

        run(both())
        assert len(env.guild.created) == 1
        assert event.scheduled_events == env.guild.created
        assert event.created

    def test_concurrent_creation_of_multi_event(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 20), at(1, 22)), TimeBlock(at(2, 20), at(2, 22))])
        event = env.make_event([a], multi_event=True)

        async def three():
            await asyncio.gather(*(event.create_if_possible() for _ in range(3)))

        run(three())
        assert [se.start_time for se in env.guild.created] == [at(1, 20), at(2, 20)]

    def test_waits_for_everyone_to_answer(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 20), at(1, 22))])
        b = env.make_participant("b")
        event = env.make_event([a, b])
        run(event.create_if_possible())
        assert env.guild.created == []
        assert not event.created

    def test_make_scheduled_events_only_creates_missing(self, env):
        existing = FakeScheduledEvent(at(1, 20))
        event = env.make_event([env.make_participant("a")], multi_event=True,
                               start_times=[at(1, 20), at(2, 20), at(3, 20)], scheduled_events=[existing])
        run(event.make_scheduled_events())
        assert [se.start_time for se in env.guild.created] == [at(2, 20), at(3, 20)]
        assert event.scheduled_events == [existing] + env.guild.created

    def test_make_scheduled_events_stops_at_failure_keeping_lists_aligned(self, env):
        env.guild.fail_on_call = 2
        event = env.make_event([env.make_participant("a")], multi_event=True,
                               start_times=[at(1, 20), at(2, 20), at(3, 20)])
        run(event.make_scheduled_events())
        assert [se.start_time for se in event.scheduled_events] == [at(1, 20)]
        # Retrying fills in the rest in order
        run(event.make_scheduled_events())
        assert [se.start_time for se in event.scheduled_events] == event.start_times

    def test_make_scheduled_events_moves_past_start_times_forward(self, env):
        past = env.sched.now() - timedelta(minutes=5)
        event = env.make_event([env.make_participant("a")], start_times=[past])
        run(event.make_scheduled_events())
        expected = env.sched.now() + timedelta(minutes=env.sched.START_TIME_DELAY)
        assert abs(event.start_times[0] - expected) <= timedelta(minutes=1)
        assert env.guild.created[0].start_time == event.start_times[0]


class TestUpdate:
    def test_creates_guild_events_missing_after_shutdown(self, env):
        """Regression: bot stopped partway through creating a multi event."""
        a = env.make_participant("a", [TimeBlock(at(1, 20), at(3, 22))])
        existing = FakeScheduledEvent(at(1, 20))
        event = env.make_event([a], multi_event=True, created=True,
                               start_times=[at(1, 20), at(2, 20)], scheduled_events=[existing])
        run(event.update())
        assert [se.start_time for se in event.scheduled_events] == [at(1, 20), at(2, 20)]
        run(event.update())
        assert len(env.guild.created) == 1

    def test_removes_event_with_nothing_left(self, env):
        event = env.make_event([env.make_participant("a")], created=True)
        run(event.update())
        assert event not in env.sched.client.events

    def test_recreates_manually_cancelled_event_once(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(3, 23))])
        cancelled = FakeScheduledEvent(at(1, 20), status=EventStatus.cancelled)
        later = FakeScheduledEvent(at(2, 20))
        event = env.make_event([a], multi_event=True, created=True,
                               start_times=[at(1, 20), at(2, 20)], scheduled_events=[cancelled, later])
        run(event.update())
        run(event.update())
        assert later.deleted
        assert [se.start_time for se in env.guild.created] == [at(1, 20), at(2, 20)]
        assert event.scheduled_events == env.guild.created

    def test_input_timer_running_blocks_creation(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 20), at(1, 22))])
        event = env.make_event([a], multi_event=True)
        event.start_input_timer()
        run(event.update())
        assert env.guild.created == []
        assert event.input_timer_running
        assert env.update_messages_calls == []

    def test_input_timer_expiry_updates_messages_when_not_created(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 8), at(1, 9))])  # too short to create
        event = env.make_event([a], multi_event=True)
        event.availability_input_timer = datetime.now().astimezone() - timedelta(seconds=env.sched.AVAILABILITY_COOLDOWN_SECONDS + 1)
        run(event.update())
        assert not event.input_timer_running
        assert env.update_messages_calls == [event]

    def test_input_timer_expiry_creates_event(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 20), at(1, 22))])
        event = env.make_event([a], multi_event=True)
        event.availability_input_timer = datetime.now().astimezone() - timedelta(seconds=env.sched.AVAILABILITY_COOLDOWN_SECONDS + 1)
        run(event.update())
        assert event.created
        assert not event.input_timer_running

    def test_input_timer_elapsed_does_not_stop_timer(self, env):
        event = env.make_event([env.make_participant("a")], multi_event=True)
        event.availability_input_timer = datetime.now().astimezone() - timedelta(seconds=env.sched.AVAILABILITY_COOLDOWN_SECONDS + 1)
        assert event.input_timer_elapsed
        assert event.input_timer_running

    def test_removes_participants_who_left_the_text_channel(self, env):
        a = env.make_participant("a")
        b = env.make_participant("b")
        env.text_channel.members.remove(b.member)
        event = env.make_event([a, b])
        run(event.update())
        assert event.participants == [a]

    def test_removes_event_when_text_channel_is_gone(self, env):
        event = env.make_event([env.make_participant("a")])
        del env.guild.channels[env.text_channel.id]
        run(event.update())
        assert event not in env.sched.client.events


class TestCancelOccurrences:
    def make_multi_event(self, env, count=3, **kwargs):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(count + 1, 23))])
        start_times = [at(day, 20) for day in range(1, count + 1)]
        scheduled_events = [FakeScheduledEvent(start_time) for start_time in start_times]
        event = env.make_event([a], multi_event=True, created=True,
                               start_times=list(start_times), scheduled_events=list(scheduled_events), **kwargs)
        return event, scheduled_events

    def test_cancels_selected_later_occurrence(self, env):
        event, (first, second, third) = self.make_multi_event(env)
        run(event.cancel_occurrences([second], reason="busy", canceller="a"))
        assert second.deleted and not first.deleted and not third.deleted
        assert event.scheduled_events == [first, third]
        assert event.start_times == [first.start_time, third.start_time]
        assert event in env.sched.client.events
        assert env.text_channel.sent[-1]["embed"].title == "Occurrence Cancelled"

    def test_cancels_first_occurrence_and_keeps_lists_aligned(self, env):
        event, (first, second, third) = self.make_multi_event(env)
        run(event.cancel_occurrences([first, second]))
        assert first.deleted and second.deleted
        assert event.scheduled_events == [third]
        assert event.start_times == [third.start_time]
        assert event in env.sched.client.events
        assert env.text_channel.sent[-1]["embed"].title == "Occurrences Cancelled"

    def test_selecting_everything_cancels_the_event(self, env):
        event, scheduled_events = self.make_multi_event(env)
        run(event.cancel_occurrences(list(scheduled_events), reason="nope"))
        assert all(se.deleted for se in scheduled_events)
        assert event not in env.sched.client.events
        assert env.text_channel.sent[-1]["embed"].title == "Event Cancelled"

    def test_started_occurrence_is_not_cancelled(self, env):
        event, (first, second, third) = self.make_multi_event(env, started=True)
        run(event.cancel_occurrences([first, third]))
        assert not first.deleted and third.deleted
        assert event.scheduled_events == [first, second]

    def test_stale_selection_is_ignored(self, env):
        event, scheduled_events = self.make_multi_event(env)
        run(event.cancel_occurrences([FakeScheduledEvent(at(5, 20))]))
        assert event.scheduled_events == scheduled_events
        assert env.text_channel.sent == []


class TestCancelModal:
    def make_interaction(self, name="a", nick=None):
        response = SimpleNamespace(defer=noop)
        return SimpleNamespace(response=response, user=SimpleNamespace(name=name, nick=nick))

    def test_multi_event_lists_every_occurrence_selected(self, env):
        scheduled_events = [FakeScheduledEvent(at(day, 20)) for day in (1, 2, 3)]
        event = env.make_event([env.make_participant("a")], multi_event=True, scheduled_events=scheduled_events)

        async def build():
            return env.sched.CancelModal(event=event, title="Cancel")

        modal = run(build())
        assert modal.occurrences.min_values == 1
        assert modal.occurrences.max_values == 3
        assert [option.value for option in modal.occurrences.options] == [str(se.id) for se in scheduled_events]
        assert all(option.default for option in modal.occurrences.options)
        assert modal.occurrences.options[0].label == at(1, 20).strftime("%a %m/%d/%Y %I:%M %p %Z")

    def test_caps_options_at_25(self, env):
        scheduled_events = [FakeScheduledEvent(at(day, 20)) for day in range(1, 31)]
        event = env.make_event([env.make_participant("a")], multi_event=True, scheduled_events=scheduled_events)

        async def build():
            return env.sched.CancelModal(event=event, title="Cancel")

        modal = run(build())
        assert len(modal.occurrences.options) == 25
        assert modal.occurrences.max_values == 25

    @pytest.mark.parametrize("multi_event, count", [(False, 1), (True, 1), (False, 3)])
    def test_no_select_without_multiple_occurrences(self, env, multi_event, count):
        scheduled_events = [FakeScheduledEvent(at(day, 20)) for day in range(1, count + 1)]
        event = env.make_event([env.make_participant("a")], multi_event=multi_event, scheduled_events=scheduled_events)

        async def build():
            return env.sched.CancelModal(event=event, title="Cancel")

        assert run(build()).occurrences is None

    def test_submit_without_select_cancels(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")])
        calls = []

        async def fake_cancel(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(event, "cancel", fake_cancel)

        async def submit():
            modal = env.sched.CancelModal(event=event, title="Cancel")
            await modal.on_submit(self.make_interaction(nick="Nick"))

        run(submit())
        assert calls == [{"reason": "", "canceller": "Nick", "schedule_again": False}]

    def test_schedule_again_unchecked_by_default(self, env):
        event = env.make_event([env.make_participant("a")])

        async def build():
            return env.sched.CancelModal(event=event, title="Cancel")

        modal = run(build())
        assert modal.schedule_again.default is False
        assert modal.schedule_again.value is False

    def test_submit_passes_schedule_again(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")])
        calls = []

        async def fake_cancel(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(event, "cancel", fake_cancel)

        async def submit():
            modal = env.sched.CancelModal(event=event, title="Cancel")
            modal.schedule_again._value = True
            await modal.on_submit(self.make_interaction())

        run(submit())
        assert calls[0]["schedule_again"] is True

    def test_submit_with_select_cancels_chosen_occurrences(self, env, monkeypatch):
        scheduled_events = [FakeScheduledEvent(at(day, 20)) for day in (1, 2, 3)]
        event = env.make_event([env.make_participant("a")], multi_event=True, scheduled_events=scheduled_events)
        calls = []

        async def fake_cancel_occurrences(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(event, "cancel_occurrences", fake_cancel_occurrences)

        async def submit():
            modal = env.sched.CancelModal(event=event, title="Cancel")
            modal.occurrences._values = [str(scheduled_events[2].id), str(scheduled_events[0].id)]
            await modal.on_submit(self.make_interaction(name="a"))

        run(submit())
        assert calls[0]["occurrences"] == [scheduled_events[0], scheduled_events[2]]
        assert calls[0]["canceller"] == "a"
        assert calls[0]["schedule_again"] is False


class TestPersistence:
    def save_data(self, env, event):
        data = event.to_dict()
        env.sched.client.events.remove(event)
        return data

    def load(self, env, monkeypatch, data):
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        return run(env.sched.Event.from_dict(data))

    def test_round_trip(self, env, monkeypatch):
        a = env.make_participant("a", [TimeBlock(at(1, 20), at(2, 22))])
        env.guild.created = [FakeScheduledEvent(at(1, 20)), FakeScheduledEvent(at(2, 20))]
        event = env.make_event([a], multi_event=True, created=True,
                               start_times=[at(1, 20), at(2, 20)], scheduled_events=list(env.guild.created))
        loaded = self.load(env, monkeypatch, self.save_data(env, event))
        assert loaded.name == event.name
        assert loaded.multi_event and loaded.created
        assert loaded.duration == event.duration
        assert loaded.start_times == event.start_times
        assert loaded.scheduled_events == event.scheduled_events
        assert [p.member for p in loaded.participants] == [a.member]

    def test_round_trips_timeout_deadlines(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")], timeout_at=at(3, 12), availability_resent_at=at(0, 1))
        loaded = self.load(env, monkeypatch, self.save_data(env, event))
        assert loaded.timeout_at == at(3, 12)
        assert loaded.availability_resent_at == at(0, 1)

    def test_legacy_timeout_counter_becomes_deadline(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")])
        data = self.save_data(env, event)
        del data["timeout_at"]
        data["timeout_counter"] = 3 * 60 * 60 // env.sched.UPDATE_INTERVAL
        monkeypatch.setattr(env.sched, "now", lambda: at(0, 12))
        loaded = self.load(env, monkeypatch, data)
        assert loaded.timeout_at == at(0, 15)

    def test_round_trips_availability_input_timer(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")], multi_event=True)
        event.start_input_timer()
        started = event.availability_input_timer
        loaded = self.load(env, monkeypatch, self.save_data(env, event))
        assert loaded.availability_input_timer == started
        assert loaded.input_timer_running

    def test_round_trips_attendees(self, env, monkeypatch):
        a = env.make_participant("a")
        b = env.make_participant("b")
        left_at = datetime.now().astimezone().replace(microsecond=0)
        event = env.make_event([a, b], started=True, attendee_ids={a.member.id, b.member.id}, attendees_left_at=left_at)
        loaded = self.load(env, monkeypatch, self.save_data(env, event))
        assert loaded.attendee_ids == {a.member.id, b.member.id}
        assert loaded.attendees_left_at == left_at

    def test_legacy_data_without_attendees(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")], started=True)
        data = self.save_data(env, event)
        del data["attendee_ids"]
        del data["attendees_left_at"]
        loaded = self.load(env, monkeypatch, data)
        assert loaded.attendee_ids == set()
        assert loaded.attendees_left_at is None

    def test_participant_missing_from_cache_is_fetched(self, env, monkeypatch):
        a = env.make_participant("a", [TimeBlock(at(1, 20), at(1, 22))])
        b = env.make_participant("b")
        event = env.make_event([a, b], scheduler=a)
        data = self.save_data(env, event)
        del env.guild.members[a.member.id]
        env.guild.uncached_members[a.member.id] = a.member
        loaded = self.load(env, monkeypatch, data)
        assert [p.member for p in loaded.participants] == [a.member, b.member]
        assert [(tb.start_time, tb.end_time) for tb in loaded.participants[0].availability] == [(at(1, 20), at(1, 22))]
        assert loaded.scheduler is loaded.participants[0]
        assert loaded.unresolved_participants == []

    def test_participant_who_left_is_dropped_without_adding_the_channel(self, env, monkeypatch):
        a = env.make_participant("a")
        b = env.make_participant("b")
        c = env.make_participant("c")
        event = env.make_event([a, b])
        data = self.save_data(env, event)
        del env.guild.members[a.member.id]
        loaded = self.load(env, monkeypatch, data)
        assert [p.member for p in loaded.participants] == [b.member]
        assert c.member not in [p.member for p in loaded.participants]
        assert loaded.unresolved_participants == []

    def test_unresolved_participant_is_kept_retried_and_blocks_creation(self, env, monkeypatch):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        b = env.make_participant("b", [TimeBlock(at(1, 18), at(1, 23))])
        event = env.make_event([a, b], scheduler=b)
        data = self.save_data(env, event)
        del env.guild.members[b.member.id]
        env.guild.fetch_member_error = RuntimeError("Discord is down")
        loaded = self.load(env, monkeypatch, data)
        env.sched.client.events.append(loaded)
        assert [p.member for p in loaded.participants] == [a.member]
        assert [d["member_id"] for d in loaded.unresolved_participants] == [b.member.id]
        assert loaded.scheduler is None
        # Saved back so another restart can still look them up
        saved = loaded.to_dict()
        assert [d["member_id"] for d in saved["participants"]] == [a.member.id, b.member.id]
        assert saved["scheduler_id"] == b.member.id
        run(loaded.create_if_possible())
        assert not loaded.created
        # Discord is back, the next retry adds them
        env.guild.fetch_member_error = None
        env.guild.members[b.member.id] = b.member
        loaded.participants_resolve_retry_at = env.sched.now()
        assert run(loaded.resolve_participants())
        assert [p.member for p in loaded.participants] == [a.member, b.member]
        assert loaded.scheduler is loaded.participants[1]
        assert loaded.unresolved_participants == []

    def test_resolve_participants_waits_between_retries(self, env, monkeypatch):
        a = env.make_participant("a")
        b = env.make_participant("b")
        event = env.make_event([a, b])
        data = self.save_data(env, event)
        del env.guild.members[b.member.id]
        env.guild.fetch_member_error = RuntimeError("Discord is down")
        loaded = self.load(env, monkeypatch, data)
        env.guild.fetch_member_error = None
        env.guild.members[b.member.id] = b.member
        assert not run(loaded.resolve_participants())
        assert loaded.unresolved_participants

    def test_no_saved_participants_adds_the_text_channel(self, env, monkeypatch):
        a = env.make_participant("a")
        a.member.bot = False
        event = env.make_event([a], scheduler=a)
        data = self.save_data(env, event)
        data["participants"] = []
        loaded = self.load(env, monkeypatch, data)
        assert [p.member for p in loaded.participants] == [a.member]
        assert loaded.scheduler is loaded.participants[0]

    def test_drops_start_times_of_guild_events_deleted_while_offline(self, env, monkeypatch):
        a = env.make_participant("a")
        scheduled_events = [FakeScheduledEvent(at(day, 20)) for day in (1, 2, 3)]
        env.guild.created = list(scheduled_events)
        event = env.make_event([a], multi_event=True, created=True,
                               start_times=[at(day, 20) for day in (1, 2, 3)], scheduled_events=list(scheduled_events))
        data = self.save_data(env, event)
        scheduled_events[1].deleted = True
        loaded = self.load(env, monkeypatch, data)
        assert loaded.scheduled_events == [scheduled_events[0], scheduled_events[2]]
        assert loaded.start_times == [at(1, 20), at(3, 20)]

    def test_keeps_start_times_that_were_never_created(self, env, monkeypatch):
        """Regression: shutdown partway through creation. update() creates the rest after loading."""
        a = env.make_participant("a")
        env.guild.created = [FakeScheduledEvent(at(1, 20))]
        event = env.make_event([a], multi_event=True, created=True,
                               start_times=[at(1, 20), at(2, 20)], scheduled_events=list(env.guild.created))
        loaded = self.load(env, monkeypatch, self.save_data(env, event))
        assert loaded.scheduled_events == env.guild.created
        assert loaded.start_times == [at(1, 20), at(2, 20)]

    def test_discards_created_event_when_every_guild_event_was_deleted(self, env, monkeypatch):
        """Regression: loading left a created event with no start times, so rendering its messages raised IndexError."""
        a = env.make_participant("a")
        scheduled_event = FakeScheduledEvent(at(1, 20))
        env.guild.created = [scheduled_event]
        event = env.make_event([a], created=True, start_times=[at(1, 20)], scheduled_events=[scheduled_event])
        data = self.save_data(env, event)
        scheduled_event.deleted = True
        with pytest.raises(Exception, match="discarding event"):
            self.load(env, monkeypatch, data)
        assert "was dropped" in env.text_channel.sent[-1]["content"]
        assert event.name in env.text_channel.sent[-1]["content"]

    def test_attaches_to_matching_guild_events_when_saved_ones_are_gone(self, env, monkeypatch):
        a = env.make_participant("a")
        event = env.make_event([a], created=True, start_times=[at(1, 20)],
                               scheduled_events=[FakeScheduledEvent(at(1, 20))])
        data = self.save_data(env, event)
        replacement = FakeScheduledEvent(at(2, 20), name=event.name, channel=env.voice_channel)
        other_location = FakeScheduledEvent(at(3, 20), name=event.name, entity_type=EntityType.external, location="Park")
        env.guild.created = [other_location, replacement]
        loaded = self.load(env, monkeypatch, data)
        assert loaded.scheduled_events == [replacement]
        assert loaded.start_times == [at(2, 20)]
        assert env.text_channel.sent == []

    def test_duplicate_name_is_rejected(self, env, monkeypatch):
        event = env.make_event([env.make_participant("a")])
        with pytest.raises(Exception, match="already in use"):
            self.load(env, monkeypatch, event.to_dict())


class TestOtherUnansweredEventsEmbed:
    def test_lists_events_still_collecting_availability(self, env):
        a = env.make_participant("a")
        event = env.make_event([a])
        other = env.make_event([Participant(member=a.member)])
        embed = env.sched.get_participants_other_unanswered_events_embed(event, a)
        assert [field.name for field in embed.fields] == [other.get_limited_name(25)]

    def test_skips_created_events(self, env):
        # /create adds participants without asking for their availability
        a = env.make_participant("a")
        event = env.make_event([a])
        env.make_event([Participant(member=a.member)], created=True, start_times=[at(1, 20)])
        assert env.sched.get_participants_other_unanswered_events_embed(event, a) is None

    def test_skips_answered_events(self, env):
        a = env.make_participant("a")
        event = env.make_event([a])
        answered = Participant(member=a.member)
        answered.answered = True
        env.make_event([answered])
        assert env.sched.get_participants_other_unanswered_events_embed(event, a) is None
