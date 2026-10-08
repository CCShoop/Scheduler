import json
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from discord import EntityType, EventStatus

from libs.participant import Participant, TimeBlock
from fakes import FakeScheduledEvent, at, noop, run
import scheduler

# The env fixture stubs these out, keep the real ones for tests that check message sends
REAL_UPDATE_AVAILABILITY_MESSAGE = scheduler.Event.update_availability_message
REAL_DELETE_AVAILABILITY_MESSAGE = scheduler.Event.delete_availability_message


class FakeMessage:
    def __init__(self):
        self.deleted = False
        self.edits = []
        self.unpinned = False

    async def delete(self, delay=None):
        self.deleted = True

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        return self

    async def unpin(self):
        self.unpinned = True


class FakeInteraction:
    """Stands in for discord.Interaction, recording responses and followups."""

    def __init__(self, member):
        self.user = member
        self.responses = []
        self.followups = []
        self.response = SimpleNamespace(defer=self._defer, send_message=self._send_message, send_modal=self._send_modal)
        self.followup = SimpleNamespace(send=self._followup_send)

    async def _defer(self, **kwargs):
        self.responses.append(("defer", kwargs))

    async def _send_message(self, **kwargs):
        self.responses.append(("send_message", kwargs))

    async def _send_modal(self, modal):
        self.responses.append(("send_modal", modal))

    async def _followup_send(self, **kwargs):
        self.followups.append(kwargs)
        return FakeMessage()


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

    def test_does_not_disable_end_button_of_active_event_in_the_same_voice_channel(self, env):
        active, _ = make_created_event(env, started=True)
        event, _ = make_created_event(env)
        run(event.start())
        assert not active.event_buttons.start_end_button.disabled

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
        waiting = env.make_event([env.make_participant("b")], created=True,
                                 start_times=[env.sched.now() + timedelta(minutes=30)])
        waiting.event_buttons = fake_event_buttons()
        waiting.event_buttons.start_end_button.disabled = True
        run(event.end())
        assert not waiting.event_buttons.start_end_button.disabled

    def test_keeps_start_buttons_disabled_while_another_event_is_active(self, env):
        event, _ = make_created_event(env, started=True)
        other_active, _ = make_created_event(env, started=True)
        waiting = env.make_event([env.make_participant("b")], created=True,
                                 start_times=[env.sched.now() + timedelta(minutes=30)])
        waiting.event_buttons = fake_event_buttons()
        waiting.event_buttons.start_end_button.disabled = True
        run(event.end())
        assert waiting.event_buttons.start_end_button.disabled

    def test_keeps_start_buttons_disabled_before_start_button_lead(self, env):
        event, _ = make_created_event(env, started=True)
        waiting = env.make_event([env.make_participant("b")], created=True, start_times=[at(2, 20)])
        waiting.event_buttons = fake_event_buttons()
        waiting.event_buttons.start_end_button.disabled = True
        run(event.end())
        assert waiting.event_buttons.start_end_button.disabled

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


class TestEndWhenAttendeesLeave:
    def make_running_event(self, env, count):
        participants = [env.make_participant(f"p{i}") for i in range(count)]
        event, _ = make_created_event(env, participants=participants, started=True)
        members = [participant.member for participant in participants]
        env.voice_channel.members[:] = members
        run(event.end_if_participants_leave_vc())
        return event, members

    def leave(self, env, *members):
        for member in members:
            env.voice_channel.members.remove(member)

    def expire_grace(self, env, event):
        event.attendees_left_at -= env.sched.ATTENDEES_LEFT_GRACE + timedelta(seconds=1)

    def test_ends_after_half_of_four_attendees_are_gone_past_grace(self, env):
        event, members = self.make_running_event(env, 4)
        self.leave(env, *members[:2])
        run(event.end_if_participants_leave_vc())
        assert not event.ended
        assert event.attendees_left_at is not None
        self.expire_grace(env, event)
        run(event.end_if_participants_leave_vc())
        assert event.ended

    def test_stays_running_within_grace(self, env):
        event, members = self.make_running_event(env, 4)
        self.leave(env, *members[:2])
        run(event.end_if_participants_leave_vc())
        event.attendees_left_at -= env.sched.ATTENDEES_LEFT_GRACE - timedelta(seconds=1)
        run(event.end_if_participants_leave_vc())
        assert not event.ended

    def test_returning_resets_grace(self, env):
        event, members = self.make_running_event(env, 4)
        self.leave(env, *members[:2])
        run(event.end_if_participants_leave_vc())
        env.voice_channel.members.append(members[0])
        run(event.end_if_participants_leave_vc())
        assert event.attendees_left_at is None

    def test_less_than_half_leaving_keeps_running(self, env):
        event, members = self.make_running_event(env, 5)
        self.leave(env, *members[:2])
        run(event.end_if_participants_leave_vc())
        assert event.attendees_left_at is None

    def test_large_group_counts_once_five_remain(self, env):
        event, members = self.make_running_event(env, 16)
        # 10 of 16 gone is over half, but 6 remain
        self.leave(env, *members[:10])
        run(event.end_if_participants_leave_vc())
        assert event.attendees_left_at is None
        self.leave(env, members[10])
        run(event.end_if_participants_leave_vc())
        assert event.attendees_left_at is not None
        self.expire_grace(env, event)
        run(event.end_if_participants_leave_vc())
        assert event.ended

    def test_ten_attendees_still_use_half(self, env):
        event, members = self.make_running_event(env, 10)
        self.leave(env, *members[:5])
        run(event.end_if_participants_leave_vc())
        assert event.attendees_left_at is not None

    def test_eleven_attendees_need_five_remaining(self, env):
        event, members = self.make_running_event(env, 11)
        # 5 of 11 gone leaves 6, one more leaves 5
        self.leave(env, *members[:5])
        run(event.end_if_participants_leave_vc())
        assert event.attendees_left_at is None
        self.leave(env, members[5])
        run(event.end_if_participants_leave_vc())
        assert event.attendees_left_at is not None

    def test_grace_survives_restart_downtime(self, env):
        event, members = self.make_running_event(env, 4)
        self.leave(env, *members[:2])
        # Restored from saved data, the attendees left before the bot went down
        event.attendees_left_at = datetime.now().astimezone() - timedelta(minutes=10)
        run(event.end_if_participants_leave_vc())
        assert event.ended

    def test_groups_under_four_only_end_when_everyone_leaves(self, env):
        event, members = self.make_running_event(env, 3)
        self.leave(env, *members[:2])
        run(event.end_if_participants_leave_vc())
        assert event.attendees_left_at is None
        assert not event.ended

    def test_participants_who_never_joined_are_not_counted(self, env):
        event, members = self.make_running_event(env, 6)
        # Two participants never showed up, and one of the four attendees left
        env.voice_channel.members[:] = members[:4]
        event.attendee_ids = {member.id for member in members[:4]}
        self.leave(env, members[0])
        run(event.end_if_participants_leave_vc())
        assert event.attendees_left_at is None

    def test_everyone_leaving_ends_immediately(self, env):
        event, members = self.make_running_event(env, 4)
        self.leave(env, *members)
        run(event.end_if_participants_leave_vc())
        assert event.ended

    def test_starting_resets_attendees(self, env):
        event, _ = make_created_event(env)
        event.attendee_ids = {1, 2, 3, 4}
        event.attendees_left_at = env.sched.now()
        run(event.start())
        assert event.attendee_ids == set()
        assert event.attendees_left_at is None


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


class TestTimeout:
    def test_cancels_once_deadline_passes(self, env):
        event = env.make_event([env.make_participant("a")])
        assert run(event.update_timeout()) is False
        assert event in env.sched.client.events
        event.timeout_at = env.sched.now() - timedelta(minutes=1)
        assert run(event.update_timeout()) is True
        assert event not in env.sched.client.events
        assert env.text_channel.sent[-1]["embed"].title == "Event Cancelled"

    def test_resends_availability_message_after_interval(self, env, monkeypatch):
        resends = []

        async def fake_update_availability_message(self):
            resends.append(self)

        monkeypatch.setattr(env.sched.Event, "update_availability_message", fake_update_availability_message)
        monkeypatch.setattr(env.sched, "now", lambda: at(0, 12))
        event = env.make_event([env.make_participant("a")])
        run(event.update_timeout())
        assert resends == []
        event.availability_resent_at = env.sched.now() - env.sched.RESEND_INTERVAL
        run(event.update_timeout())
        assert resends == [event]
        assert event.availability_resent_at == at(0, 12)


class TestReschedule:
    def test_clears_guild_events_and_resets_state(self, env, monkeypatch):
        monkeypatch.setattr(env.sched, "now", lambda: at(0, 12))
        event, scheduled_events = make_created_event(env, days=(1, 2), reminder_flag=True)
        event.timeout_at = at(-1, 0)
        reminder = FakeMessage()
        event.reminder_message = reminder
        run(event.reschedule())
        assert all(se.deleted for se in scheduled_events)
        assert event.scheduled_events == [] and event.start_times == []
        assert not event.created and not event.ready_to_create and not event.reminder_flag
        assert event.timeout_at == at(0, 12) + env.sched.DEFAULT_EVENT_TIMEOUT
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

    def test_guild_event_started_in_discord_ends_active_event_at_location(self, env):
        active, (active_scheduled_event,) = make_created_event(env, started=True)
        event, (scheduled_event,) = make_created_event(env)
        scheduled_event.status = EventStatus.active
        run(event.update())
        assert event.started
        assert active.ended
        assert active_scheduled_event.deleted

    def test_guild_event_moved_in_discord_moves_event(self, env):
        event, scheduled_events = make_created_event(env, days=(1, 2))
        new_voice_channel = SimpleNamespace(id=99, members=[], mention="<#99>")
        scheduled_events[0].kwargs["channel"] = new_voice_channel
        run(event.update())
        assert event.voice_channel is new_voice_channel
        assert scheduled_events[1].edits == [{"entity_type": EntityType.voice, "channel": new_voice_channel}]

    def test_guild_event_moved_to_external_location_in_discord(self, env):
        event, (scheduled_event,) = make_created_event(env)
        scheduled_event.kwargs.update(entity_type=EntityType.external, location="Park")
        run(event.update())
        assert event.location == "Park"
        assert event.voice_channel is None

    def test_unsubscribed_participant_joining_voice_channel_is_resubscribed(self, env):
        a = env.make_participant("a")
        b = env.make_participant("b")
        b.subscribed = False
        event, _ = make_created_event(env, participants=[a, b], started=True)
        env.voice_channel.members[:] = [a.member, b.member]
        run(event.update())
        assert b.subscribed

    def test_unsubscribed_participant_outside_voice_channel_stays_unsubscribed(self, env):
        a = env.make_participant("a")
        b = env.make_participant("b")
        b.subscribed = False
        event, _ = make_created_event(env, participants=[a, b], started=True)
        env.voice_channel.members[:] = [a.member]
        run(event.update())
        assert not b.subscribed

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


class TestCancelIfMissed:
    def missed_start(self, env, event, extra=timedelta(minutes=1)):
        return env.sched.now() - event.scheduled_duration - env.sched.MISSED_OCCURRENCE_GRACE - extra

    def test_keeps_occurrence_within_grace_period(self, env):
        event, _ = make_created_event(env)
        event.start_times[0] = self.missed_start(env, event, extra=-timedelta(minutes=1))
        assert not run(event.cancel_if_missed())
        assert event in env.sched.client.events

    def test_cancels_only_occurrence(self, env):
        event, (scheduled_event,) = make_created_event(env)
        event.start_times[0] = self.missed_start(env, event)
        run(event.update())
        assert event.cancelled
        assert scheduled_event.deleted
        assert event not in env.sched.client.events
        assert env.text_channel.sent[-1]["embed"].title == "Event Cancelled"

    def test_cancels_only_current_occurrence_of_multi_event(self, env):
        event, scheduled_events = make_created_event(env, days=(1, 2))
        event.start_times[0] = self.missed_start(env, event)
        run(event.update())
        assert not event.cancelled
        assert scheduled_events[0].deleted
        assert event.scheduled_events == [scheduled_events[1]]
        assert event.start_times == [at(2, 20)]
        assert env.text_channel.sent[-1]["embed"].title == "Occurrence Cancelled"

    def test_started_occurrence_is_not_cancelled(self, env):
        event, _ = make_created_event(env, started=True)
        event.start_times[0] = self.missed_start(env, event)
        assert not run(event.cancel_if_missed())
        assert not event.cancelled


class TestStartButtonLead:
    def test_disabled_before_start_button_lead(self, env):
        event, _ = make_created_event(env)
        event.start_times[0] = env.sched.now() + env.sched.START_BUTTON_LEAD + timedelta(minutes=1)
        assert env.sched.EventButtons(event).start_end_button.disabled

    def test_enabled_within_start_button_lead(self, env):
        event, _ = make_created_event(env)
        event.start_times[0] = env.sched.now() + env.sched.START_BUTTON_LEAD
        assert not env.sched.EventButtons(event).start_end_button.disabled

    def test_update_enables_button_once_within_start_button_lead(self, env):
        event, _ = make_created_event(env)
        event.event_buttons.start_end_button.disabled = True
        event.start_times[0] = env.sched.now() + env.sched.START_BUTTON_LEAD - timedelta(minutes=1)
        run(event.sync_start_button())
        assert not event.event_buttons.start_end_button.disabled

    def test_update_disables_button_when_rescheduled_later(self, env):
        event, _ = make_created_event(env)
        event.start_times[0] = env.sched.now() + timedelta(hours=2)
        run(event.sync_start_button())
        assert event.event_buttons.start_end_button.disabled

    def test_start_callback_refuses_too_early(self, env, monkeypatch):
        event, _ = make_created_event(env)
        event.start_times[0] = env.sched.now() + timedelta(hours=2)
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
        assert "can't be started until" in interaction.followups[0]["content"]

    def test_start_callback_refuses_while_location_is_active(self, env, monkeypatch):
        active, _ = make_created_event(env, started=True)
        active.event_buttons_message = SimpleNamespace(jump_url="https://discord.com/channels/5/6/7")
        event, _ = make_created_event(env)
        event.start_times[0] = env.sched.now() + timedelta(minutes=10)
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
        assert interaction.followups[0]["content"] == (f"{active} is currently happening in {env.voice_channel.mention}\n"
                                                       "https://discord.com/channels/5/6/7")

    def test_simultaneous_starts_at_the_same_location_start_one_event(self, env):
        first, (first_scheduled_event,) = make_created_event(env)
        second, (second_scheduled_event,) = make_created_event(env)
        for scheduled_event in (first_scheduled_event, second_scheduled_event):
            async def slow_start(reason=None, scheduled_event=scheduled_event):
                # Yield like a real HTTP call so the other start can run in between
                await asyncio.sleep(0)
                scheduled_event.status = EventStatus.active
            scheduled_event.start = slow_start

        async def start_both():
            return await asyncio.gather(first.start(), second.start())

        assert run(start_both()) == [True, False]
        assert first.started
        assert not second.started
        assert second_scheduled_event.status == EventStatus.scheduled


class TestAutoCancelWhenAllUnsubscribed:
    """Regression: auto cancelling after everyone unsubscribes must not send another availability message."""

    @pytest.fixture
    def real_availability_message(self, env, monkeypatch):
        monkeypatch.setattr(env.sched.Event, "update_availability_message", REAL_UPDATE_AVAILABILITY_MESSAGE)
        monkeypatch.setattr(env.sched.Event, "delete_availability_message", REAL_DELETE_AVAILABILITY_MESSAGE)

    def unsubscribed_participants(self, env):
        participants = [env.make_participant("a"), env.make_participant("b")]
        for participant in participants:
            participant.subscribed = False
            participant.answered = True
        return participants

    def assert_cancelled_once(self, env, event):
        assert event not in env.sched.client.events
        assert len(env.text_channel.sent) == 1
        assert env.text_channel.sent[0]["embed"].title == "Event Cancelled"
        assert env.text_channel.sent[0].get("view") is None

    def test_unsubscribe_button_path(self, env, real_availability_message):
        participants = self.unsubscribed_participants(env)
        event = env.make_event(participants)
        old_message = FakeMessage()
        event.availability_message = old_message
        run(event.handle_input_received(exclude=[participants[-1]]))
        self.assert_cancelled_once(env, event)
        assert old_message.deleted
        assert event.availability_message is None

    def test_multi_event_cooldown_path(self, env, real_availability_message):
        event = env.make_event(self.unsubscribed_participants(env), multi_event=True)
        event.availability_message = FakeMessage()
        event.availability_input_timer = env.sched.now() - timedelta(seconds=env.sched.AVAILABILITY_COOLDOWN_SECONDS + 1)
        run(event.update())
        self.assert_cancelled_once(env, event)
        assert event.availability_message is None
        assert env.update_messages_calls == []

    def test_concurrent_callers_cancel_once(self, env, real_availability_message):
        event = env.make_event(self.unsubscribed_participants(env))

        async def both():
            await asyncio.gather(event.create_if_possible(), event.create_if_possible())

        run(both())
        self.assert_cancelled_once(env, event)

    def test_cancelled_flag_set(self, env, real_availability_message):
        event = env.make_event(self.unsubscribed_participants(env))
        run(event.create_if_possible())
        assert event.cancelled

    def test_created_event_cancelled_by_update(self, env, real_availability_message):
        event, (scheduled_event,) = make_created_event(env, participants=self.unsubscribed_participants(env))
        run(event.update())
        self.assert_cancelled_once(env, event)
        assert event.cancelled
        assert scheduled_event.deleted

    def test_created_multi_event_cancels_every_occurrence_with_one_message(self, env, real_availability_message):
        event, scheduled_events = make_created_event(env, days=(1, 2, 3), participants=self.unsubscribed_participants(env))
        run(event.update())
        self.assert_cancelled_once(env, event)
        assert all(scheduled_event.deleted for scheduled_event in scheduled_events)

    def test_concurrent_multi_event_cancellation_cancels_once(self, env, real_availability_message):
        event, scheduled_events = make_created_event(env, days=(1, 2, 3), participants=self.unsubscribed_participants(env))

        # Yield like a real HTTP call so the second caller runs while occurrences are being deleted
        def yielding_delete(scheduled_event):
            async def delete(reason=None):
                await asyncio.sleep(0.01)
                scheduled_event.deleted = True
            return delete

        for scheduled_event in scheduled_events:
            scheduled_event.delete = yielding_delete(scheduled_event)

        async def both():
            await asyncio.gather(event.update(), event.cancel_if_everyone_unsubscribed())

        run(both())
        self.assert_cancelled_once(env, event)

    def test_started_event_is_left_to_end(self, env, real_availability_message):
        event, (scheduled_event,) = make_created_event(env, participants=self.unsubscribed_participants(env), started=True)
        scheduled_event.status = EventStatus.active
        assert not run(event.cancel_if_everyone_unsubscribed())
        assert event in env.sched.client.events
        assert not event.cancelled
        assert not scheduled_event.deleted

    def test_remaining_subscriber_keeps_event(self, env, real_availability_message):
        participants = self.unsubscribed_participants(env)
        participants[0].subscribed = True
        event, _ = make_created_event(env, participants=participants)
        assert not run(event.cancel_if_everyone_unsubscribed())
        assert event in env.sched.client.events

    def test_everyone_left_the_text_channel(self, env, real_availability_message):
        participants = self.unsubscribed_participants(env)
        for participant in participants:
            participant.subscribed = True
            env.text_channel.members.remove(participant.member)
        event, _ = make_created_event(env, participants=participants)
        run(event.update())
        self.assert_cancelled_once(env, event)
        reason = env.text_channel.sent[0]["embed"].fields[0].value
        assert reason == f"All participants left {env.text_channel.mention}."

    def test_unsubscribed_reason(self, env, real_availability_message):
        event = env.make_event(self.unsubscribed_participants(env))
        run(event.create_if_possible())
        assert env.text_channel.sent[0]["embed"].fields[0].value == "All participants unsubscribed."

    def test_event_buttons_unsubscribe_cancels_created_event(self, env, real_availability_message):
        participants = self.unsubscribed_participants(env)
        last = participants[0]
        last.subscribed = True
        event, (scheduled_event,) = make_created_event(env, participants=participants)

        async def click():
            buttons = env.sched.EventButtons(event)
            await buttons.unsubscribe_button.callback(FakeInteraction(last.member))

        run(click())
        self.assert_cancelled_once(env, event)
        assert scheduled_event.deleted

    def test_cancelled_event_buttons_do_not_revive_event(self, env, real_availability_message):
        participants = self.unsubscribed_participants(env)
        event = env.make_event(participants)
        run(event.create_if_possible())
        interaction = FakeInteraction(participants[0].member)

        async def click():
            buttons = env.sched.AvailabilityButtons(event)
            unsub_button = next(item for item in buttons.children if getattr(item, "label", None) == buttons.unsub_label)
            await unsub_button.callback(interaction)

        run(click())
        assert event not in env.sched.client.events
        assert not participants[0].subscribed
        assert interaction.responses == [("send_message", {"content": f"{event} has been cancelled.", "ephemeral": True})]
        self.assert_cancelled_once(env, event)

    def test_cancelling_one_occurrence_does_not_set_flag(self, env):
        event, (first, second) = make_created_event(env, days=(1, 2))
        run(event.cancel())
        assert not event.cancelled
        assert event in env.sched.client.events

    def test_recreate_after_manual_cancellation_cleans_up(self, env, real_availability_message):
        participants = self.unsubscribed_participants(env)
        event, (scheduled_event,) = make_created_event(env, participants=participants)
        # Reminder already sent, so a fall through would try to start the cancelled event
        event.start_times[0] = env.sched.now() + timedelta(minutes=5)
        event.reminder_flag = True
        scheduled_event.status = EventStatus.cancelled
        buttons_message = FakeMessage()
        event.event_buttons_message = buttons_message
        start_attempts = []

        async def record_start(reason=None):
            start_attempts.append(reason)

        event.start = record_start
        run(event.update())
        self.assert_cancelled_once(env, event)
        assert start_attempts == []
        assert buttons_message.deleted
        assert event.event_buttons_message is None


class TestScheduleAgain:
    def cancel_with_schedule_again(self, env, **kwargs):
        event = env.make_event([env.make_participant("a")], **kwargs)
        run(event.cancel(schedule_again=True))
        (after_buttons,) = env.sched.client.schedule_again_events
        return event, after_buttons

    def restore(self, env, monkeypatch, data):
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        return run(env.sched.AfterButtons.from_dict(data))

    def test_cancel_has_no_buttons_by_default(self, env):
        event = env.make_event([env.make_participant("a")])
        run(event.cancel())
        assert env.text_channel.sent[-1].get("view") is None
        assert env.sched.client.schedule_again_events == []

    def test_cancel_with_schedule_again_attaches_buttons(self, env):
        event, after_buttons = self.cancel_with_schedule_again(env)
        assert env.text_channel.sent[-1]["view"] is after_buttons
        assert after_buttons.message is not None

    def test_cancelling_one_occurrence_ignores_schedule_again(self, env):
        event, _ = make_created_event(env, days=(1, 2))
        run(event.cancel(schedule_again=True))
        assert env.text_channel.sent[-1].get("view") is None
        assert env.sched.client.schedule_again_events == []

    def test_cancelling_every_occurrence_passes_schedule_again(self, env):
        event, scheduled_events = make_created_event(env, days=(1, 2, 3))
        run(event.cancel_occurrences(list(scheduled_events), schedule_again=True))
        (after_buttons,) = env.sched.client.schedule_again_events
        assert env.text_channel.sent[-1]["view"] is after_buttons

    def test_saved_like_a_normal_event(self, env):
        event, after_buttons = self.cancel_with_schedule_again(env)
        (saved,) = env.sched.client.events_dict["schedule_again_events"]
        assert {key: value for key, value in saved.items() if key in event.to_dict()} == event.to_dict()
        assert saved["after_buttons_message_id"] == after_buttons.message.id
        assert saved["schedule_again_expires_at"] == after_buttons.expires_at.isoformat()
        json.dumps(saved)

    def test_round_trip_re_enables_buttons(self, env, monkeypatch):
        event, after_buttons = self.cancel_with_schedule_again(env, duration=timedelta(minutes=90), image_url="https://example.com/a.png")
        after_buttons.expires_at = at(3, 12)
        run(after_buttons.disable())
        (saved,) = env.sched.client.events_dict["schedule_again_events"]
        env.sched.client.schedule_again_events.clear()
        restored = self.restore(env, monkeypatch, saved)
        assert env.sched.client.schedule_again_events == [restored]
        assert restored.event.name == event.name
        assert restored.event.voice_channel is env.voice_channel
        assert restored.event.duration == timedelta(minutes=90)
        assert restored.event.image_url == "https://example.com/a.png"
        assert [participant.member.id for participant in restored.event.participants] == [event.participants[0].member.id]
        assert restored.event.after_buttons is restored
        assert restored.expires_at == at(3, 12)
        assert restored.message is after_buttons.message
        assert restored.message.edits[-1]["view"] is restored
        assert not restored.schedule_again_button.disabled and not restored.forget_button.disabled
        assert restored.event not in env.sched.client.events

    def test_restores_legacy_tick_counter_as_deadline(self, env, monkeypatch):
        _, after_buttons = self.cancel_with_schedule_again(env)
        (saved,) = env.sched.client.events_dict["schedule_again_events"]
        env.sched.client.schedule_again_events.clear()
        del saved["schedule_again_expires_at"]
        saved["schedule_again_timeout"] = 2 * 60 * 60 // env.sched.UPDATE_INTERVAL
        monkeypatch.setattr(env.sched, "now", lambda: at(0, 12))
        restored = self.restore(env, monkeypatch, saved)
        assert restored.expires_at == at(0, 14)

    def test_expires_at_deadline(self, env):
        _, after_buttons = self.cancel_with_schedule_again(env)
        run(after_buttons.update())
        assert env.sched.client.schedule_again_events == [after_buttons]
        after_buttons.expires_at = env.sched.now() - timedelta(minutes=1)
        run(after_buttons.update())
        assert env.sched.client.schedule_again_events == []

    def test_missing_message_is_dropped(self, env, monkeypatch):
        _, after_buttons = self.cancel_with_schedule_again(env)
        (saved,) = env.sched.client.events_dict["schedule_again_events"]
        env.sched.client.schedule_again_events.clear()
        after_buttons.message.deleted = True
        with pytest.raises(LookupError):
            self.restore(env, monkeypatch, saved)
        assert env.sched.client.schedule_again_events == []

    def test_disable_keeps_buttons_saved(self, env):
        _, after_buttons = self.cancel_with_schedule_again(env)
        run(after_buttons.disable())
        assert after_buttons.schedule_again_button.disabled and after_buttons.forget_button.disabled
        assert after_buttons.message.edits[-1]["view"] is after_buttons
        assert env.sched.client.schedule_again_events == [after_buttons]

    def test_shutdown_disables_instead_of_removing(self, env, monkeypatch):
        _, after_buttons = self.cancel_with_schedule_again(env)
        monkeypatch.setattr(env.sched.client, "close", lambda: env.sched.asyncio.sleep(0))
        run(env.sched.cleanup())
        assert after_buttons.schedule_again_button.disabled
        assert env.sched.client.schedule_again_events == [after_buttons]

    def test_retrieve_events_restores_schedule_again_events(self, env, monkeypatch):
        _, after_buttons = self.cancel_with_schedule_again(env)
        env.sched.persist.write(env.sched.client.events_dict)
        env.sched.client.schedule_again_events.clear()
        monkeypatch.setattr(env.sched.client, "loaded_json", False)
        monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild)
        run(env.sched.client.retrieve_events())
        (restored,) = env.sched.client.schedule_again_events
        assert restored.message is after_buttons.message

    def test_retrieve_events_without_schedule_again_events(self, env, monkeypatch):
        env.sched.persist.write({"events": []})
        monkeypatch.setattr(env.sched.client, "loaded_json", False)
        run(env.sched.client.retrieve_events())
        assert env.sched.client.schedule_again_events == []

    def test_retrieve_events_loads_started_events_first(self, env, monkeypatch):
        env.sched.persist.write({"events": [{"name": "waiting", "started": False},
                                            {"name": "active", "started": True},
                                            {"name": "legacy"}]})
        monkeypatch.setattr(env.sched.client, "loaded_json", False)
        monkeypatch.setattr(env.sched.asyncio, "sleep", noop)
        loaded = []

        async def fake_from_dict(data):
            loaded.append(data["name"])

        monkeypatch.setattr(env.sched.Event, "from_dict", fake_from_dict)
        run(env.sched.client.retrieve_events())
        assert loaded == ["active", "waiting", "legacy"]
