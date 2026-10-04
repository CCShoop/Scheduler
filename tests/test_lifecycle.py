from datetime import timedelta
from types import SimpleNamespace

import pytest
from discord import EventStatus

from libs.participant import Participant, TimeBlock
from fakes import FakeScheduledEvent, at, run


class FakeMessage:
    def __init__(self):
        self.deleted = False
        self.edits = []
        self.unpinned = False

    async def delete(self):
        self.deleted = True

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        return self

    async def unpin(self):
        self.unpinned = True


def fake_event_buttons():
    buttons = SimpleNamespace(start_end_button=SimpleNamespace(disabled=False), converted=False)
    buttons.convert = lambda: setattr(buttons, "converted", True)
    return buttons


def make_created_event(env, days=(1,), participants=None, **kwargs):
    if participants is None:
        participants = [env.make_participant("a", [TimeBlock(at(1, 18), at(max(days) + 1, 23))])]
    start_times = [at(day, 20) for day in days]
    scheduled_events = [FakeScheduledEvent(start_time) for start_time in start_times]
    event = env.make_event(participants, multi_event=len(days) > 1, created=True,
                           start_times=list(start_times), scheduled_events=list(scheduled_events), **kwargs)
    event.event_buttons = fake_event_buttons()
    return event, scheduled_events


class TestStart:
    def test_starts_first_occurrence(self, env):
        event, (scheduled_event,) = make_created_event(env)
        reminder = FakeMessage()
        event.reminder_message = reminder
        run(event.start(reason="test"))
        assert event.started
        assert scheduled_event.status == EventStatus.active
        assert abs(event.start_times[0] - env.sched.now()) <= timedelta(minutes=1)
        assert event.event_buttons.converted
        assert reminder.deleted and event.reminder_message is None

    def test_failure_to_start_guild_event_leaves_event_unstarted(self, env):
        event, (scheduled_event,) = make_created_event(env)

        async def fail(reason=None):
            raise RuntimeError("discord said no")

        scheduled_event.start = fail
        run(event.start())
        assert not event.started
        assert event.start_times == [at(1, 20)]

    def test_pushes_back_overlapping_events_in_the_same_voice_channel(self, env):
        event, _ = make_created_event(env)
        now = env.sched.now()
        # Scheduled to start before this event would end
        soon = env.make_event([env.make_participant("b")], created=True,
                              start_times=[now + timedelta(minutes=30)], duration=timedelta(hours=1))
        later = env.make_event([env.make_participant("c")], created=True,
                               start_times=[now + timedelta(days=2)], duration=timedelta(hours=1))
        soon.event_buttons = fake_event_buttons()
        later.event_buttons = fake_event_buttons()
        run(event.start())
        expected_end = event.start_times[0] + event.duration + timedelta(minutes=env.sched.EVENT_BUFFER_MINUTES)
        assert soon.start_times[0] == expected_end
        assert later.start_times[0] == now + timedelta(days=2)
        assert soon.event_buttons.start_end_button.disabled
        assert later.event_buttons.start_end_button.disabled

    def test_pushes_back_events_sharing_participants_in_other_channels(self, env):
        a = env.make_participant("a", [TimeBlock(at(0, 0), at(3, 23))])
        event, _ = make_created_event(env, participants=[a])
        other_voice_channel = SimpleNamespace(id=99, members=[], mention="<#99>")
        now = env.sched.now()
        shared = env.make_event([Participant(member=a.member)], created=True,
                                start_times=[now + timedelta(minutes=30)])
        shared.voice_channel = other_voice_channel
        shared.event_buttons = fake_event_buttons()
        run(event.start())
        assert shared.start_times[0] == event.start_times[0] + event.duration
        # Different channel, so its start button stays enabled
        assert not shared.event_buttons.start_end_button.disabled

    def test_ignores_events_that_are_not_created(self, env):
        event, _ = make_created_event(env)
        now = env.sched.now()
        scheduling = env.make_event([env.make_participant("b")], start_times=[now + timedelta(minutes=30)])
        run(event.start())
        assert scheduling.start_times == [now + timedelta(minutes=30)]


class TestEnd:
    def test_ending_last_occurrence_removes_event(self, env):
        event, (scheduled_event,) = make_created_event(env, started=True)
        run(event.end(reason="done"))
        assert scheduled_event.deleted
        assert event.ended
        assert event not in env.sched.client.events

    def test_ending_multi_event_moves_to_next_occurrence(self, env):
        event, (first, second, third) = make_created_event(env, days=(1, 2, 3), started=True)
        run(event.end())
        assert first.deleted and not second.deleted
        assert event.scheduled_events == [second, third]
        assert event.start_times == [second.start_time, third.start_time]
        assert not event.started and not event.ended
        assert event in env.sched.client.events

    def test_edits_and_unpins_event_buttons_message(self, env):
        event, _ = make_created_event(env, days=(1, 2), started=True)
        message = FakeMessage()
        event.event_buttons_message = message
        event.start_times[0] = env.sched.now() - timedelta(minutes=45)
        run(event.end())
        assert message.unpinned
        # More occurrences remain, so no Schedule Again buttons
        assert message.edits[0]["view"] is None
        assert message.edits[0]["embeds"]
        # Released so a fresh message is sent for the next occurrence
        assert event.event_buttons_message is None

    def test_reenables_start_buttons_in_the_same_voice_channel(self, env):
        event, _ = make_created_event(env, started=True)
        waiting = env.make_event([env.make_participant("b")], created=True, start_times=[at(2, 20)])
        waiting.event_buttons = fake_event_buttons()
        waiting.event_buttons.start_end_button.disabled = True
        run(event.end())
        assert not waiting.event_buttons.start_end_button.disabled

    def test_restores_shared_participant_availability_for_last_occurrence(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        event, _ = make_created_event(env, participants=[a], started=True)
        b_view_of_a = Participant(member=a.member, availability=[TimeBlock(at(1, 18), at(1, 23))])
        other = env.make_event([b_view_of_a])
        env.sched.remove_times_from_availabilities_for_events()
        assert b_view_of_a.removed_times
        run(event.end())
        assert b_view_of_a.removed_times == []
        assert [(tb.start_time, tb.end_time) for tb in b_view_of_a.availability] == [(at(1, 18), at(1, 23))]
        assert other in env.update_messages_calls

    def test_keeps_shared_participant_blocked_for_remaining_occurrences(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(2, 23))])
        event, _ = make_created_event(env, days=(1, 2), participants=[a], started=True)
        b_view_of_a = Participant(member=a.member, availability=[TimeBlock(at(2, 18), at(2, 23))])
        env.make_event([b_view_of_a])
        env.sched.remove_times_from_availabilities_for_events()
        run(event.end())
        # Day 2 is still scheduled, so it should stay out of the other event's availability
        assert not b_view_of_a.is_available_at(at(2, 20), event.duration)


class TestCancel:
    def test_cancelling_current_occurrence_keeps_remaining_occurrences_blocked(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(2, 23))])
        event, (first, second) = make_created_event(env, days=(1, 2), participants=[a])
        b_view_of_a = Participant(member=a.member, availability=[TimeBlock(at(1, 18), at(2, 23))])
        env.make_event([b_view_of_a])
        env.sched.remove_times_from_availabilities_for_events()
        run(event.cancel(reason="sick"))
        assert first.deleted and not second.deleted
        # Day 1 is free again, day 2 is still taken
        assert b_view_of_a.is_available_at(at(1, 20), event.duration)
        assert not b_view_of_a.is_available_at(at(2, 20), event.duration)

    def test_cancelling_last_occurrence_frees_everything(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        event, _ = make_created_event(env, participants=[a])
        b_view_of_a = Participant(member=a.member, availability=[TimeBlock(at(1, 18), at(1, 23))])
        env.make_event([b_view_of_a])
        env.sched.remove_times_from_availabilities_for_events()
        run(event.cancel())
        assert event not in env.sched.client.events
        assert b_view_of_a.removed_times == []
        assert b_view_of_a.is_available_at(at(1, 20), event.duration)


class TestPrepNextScheduledEvent:
    def test_shifts_lists_and_clears_occurrence_state(self, env):
        event, (first, second) = make_created_event(env, days=(1, 2), started=True)
        buttons_message = FakeMessage()
        reminder = FakeMessage()
        event.event_buttons_message = buttons_message
        event.reminder_message = reminder
        event.ended = True
        run(event.prep_next_scheduled_event())
        assert event.scheduled_events == [second]
        assert event.start_times == [second.start_time]
        assert buttons_message.edits == [{"view": None}]
        assert reminder.deleted
        assert event.event_buttons is None
        assert not event.started and not event.ended

    @pytest.mark.parametrize("minutes_away, expected", [(5, True), (60 * 24, False)])
    def test_reminder_flag_matches_next_start(self, env, minutes_away, expected):
        event, _ = make_created_event(env, days=(1, 2))
        event.start_times[1] = env.sched.now() + timedelta(minutes=minutes_away)
        run(event.prep_next_scheduled_event())
        assert event.reminder_flag is expected

    def test_last_occurrence_removes_event(self, env):
        event, _ = make_created_event(env)
        run(event.prep_next_scheduled_event())
        assert event not in env.sched.client.events


class TestReschedule:
    def test_clears_guild_events_and_resets_state(self, env):
        event, scheduled_events = make_created_event(env, days=(1, 2), reminder_flag=True)
        event.timeout_counter = 5
        reminder = FakeMessage()
        event.reminder_message = reminder
        run(event.reschedule())
        assert all(se.deleted for se in scheduled_events)
        assert event.scheduled_events == [] and event.start_times == []
        assert not event.created and not event.ready_to_create and not event.reminder_flag
        assert event.timeout_counter == env.sched.DEFAULT_EVENT_TIMEOUT
        assert reminder.deleted

    def test_rescheduler_availability_is_cleared(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(2, 23))])
        b = env.make_participant("b", [TimeBlock(at(1, 18), at(2, 23))])
        event, _ = make_created_event(env, participants=[a, b])
        run(event.reschedule(rescheduler=a))
        assert a.availability == []
        assert b.availability != []

    def test_deletion_errors_do_not_stop_reschedule(self, env):
        event, (scheduled_event,) = make_created_event(env)

        async def fail(reason=None):
            raise RuntimeError("already gone")

        scheduled_event.delete = fail
        run(event.reschedule())
        assert event.scheduled_events == []
        assert not event.created

    def test_restores_availability_for_other_events(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        event, _ = make_created_event(env, participants=[a])
        b_view_of_a = Participant(member=a.member, availability=[TimeBlock(at(1, 18), at(1, 23))])
        env.make_event([b_view_of_a])
        env.sched.remove_times_from_availabilities_for_events()
        run(event.reschedule())
        assert b_view_of_a.removed_times == []
        assert b_view_of_a.is_available_at(at(1, 20), event.duration)


class TestUpdateWhileCreated:
    @pytest.fixture(autouse=True)
    def bot_user(self, env, monkeypatch):
        # Reminder embeds use the bot's avatar in the footer
        bot_user = SimpleNamespace(avatar=SimpleNamespace(url="https://example.invalid/avatar.png"))
        monkeypatch.setattr(type(env.sched.client), "user", property(lambda self: bot_user))

    def test_guild_event_started_in_discord_starts_event(self, env):
        event, (scheduled_event,) = make_created_event(env)
        scheduled_event.status = EventStatus.active
        run(event.update())
        assert event.started

    def test_guild_event_ended_in_discord_ends_event(self, env):
        event, (scheduled_event,) = make_created_event(env)
        scheduled_event.status = EventStatus.ended
        run(event.update())
        assert event not in env.sched.client.events

    def test_cancelled_in_discord_while_started_cancels(self, env, monkeypatch):
        event, (scheduled_event,) = make_created_event(env, started=True)
        scheduled_event.status = EventStatus.cancelled
        calls = []

        async def fake_cancel(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(event, "cancel", fake_cancel)
        run(event.update())
        assert calls == [{"reason": "Event cancelled manually."}]

    def test_ends_when_everyone_leaves_voice_channel(self, env):
        event, _ = make_created_event(env, started=True)
        run(event.update())
        assert event.ended

    def test_keeps_running_while_someone_is_in_voice_channel(self, env):
        event, _ = make_created_event(env, started=True)
        env.voice_channel.members.append(event.participants[0].member)
        run(event.update())
        assert not event.ended

    def test_sends_reminder_when_start_is_near(self, env):
        event, _ = make_created_event(env)
        event.start_times[0] = env.sched.now() + timedelta(minutes=env.sched.REMINDER_TIME_MINUTES - 1)
        run(event.update())
        assert event.reminder_flag
        assert event.reminder_message is not None
        assert env.text_channel.sent[-1]["embed"].title == "Event Reminder!"

    def test_no_reminder_when_everyone_is_already_in_voice_channel(self, env):
        event, _ = make_created_event(env)
        event.start_times[0] = env.sched.now() + timedelta(minutes=env.sched.REMINDER_TIME_MINUTES - 1)
        env.voice_channel.members.append(event.participants[0].member)
        run(event.update())
        assert event.reminder_flag
        assert event.reminder_message is None

    def test_starts_once_everyone_joins_after_reminder(self, env):
        event, _ = make_created_event(env, reminder_flag=True)
        event.start_times[0] = env.sched.now() + timedelta(minutes=5)
        env.voice_channel.members.append(event.participants[0].member)
        run(event.update())
        assert event.started

    def test_does_not_start_while_another_event_is_running_in_channel(self, env):
        event, _ = make_created_event(env, reminder_flag=True)
        event.start_times[0] = env.sched.now() + timedelta(minutes=5)
        running = env.make_event([env.make_participant("b")], created=True, started=True, start_times=[env.sched.now()])
        running.voice_channel = env.voice_channel
        env.voice_channel.members.append(event.participants[0].member)
        run(event.update())
        assert not event.started
