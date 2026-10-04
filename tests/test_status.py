from datetime import datetime, timedelta

import pytest

from libs.participant import TimeBlock
from fakes import at


class TestSchedulingStatus:
    @pytest.mark.parametrize("flags, expected", [
        ({"ended": True, "started": True, "created": True}, "Event ended"),
        ({"started": True, "created": True}, "Event started"),
        ({"created": True}, "Event created"),
        ({"ready_to_create": True}, "Creating event"),
    ])
    def test_lifecycle_flags_take_priority(self, env, flags, expected):
        event = env.make_event([env.make_participant("a")])
        for name, value in flags.items():
            setattr(event, name, value)
        assert event.scheduling_status == expected

    def test_awaiting_availability(self, env):
        event = env.make_event([env.make_participant("a")])
        assert event.scheduling_status == "Awaiting availability"

    def test_no_common_availability_once_everyone_answered(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 8), at(1, 12))])
        event = env.make_event([a])
        assert event.scheduling_status == "No common availability"

    def test_waiting_while_input_timer_runs(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 8), at(1, 12))])
        event = env.make_event([a], multi_event=True)
        event.start_input_timer()
        assert event.scheduling_status == "Waiting for final availability changes"

    def test_unsubscribed_participants_do_not_block(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 8), at(1, 12))])
        b = env.make_participant("b")
        b.subscribed = False
        event = env.make_event([a, b])
        assert event.everyone_answered


class TestGetNamesString:
    @pytest.fixture
    def people(self, env):
        answered = env.make_participant("answered", [TimeBlock(at(1, 8), at(1, 12))])
        waiting = env.make_participant("waiting")
        unsubscribed = env.make_participant("unsubscribed")
        unsubscribed.subscribed = False
        event = env.make_event([answered, waiting, unsubscribed])
        return event, answered, waiting, unsubscribed

    def test_everyone(self, people):
        event, *_ = people
        assert event.get_names_string() == "answered\nwaiting\nunsubscribed"

    def test_mentions(self, people):
        event, answered, waiting, unsubscribed = people
        assert event.get_names_string(mention=True) == "\n".join(p.member.mention for p in (answered, waiting, unsubscribed))

    def test_nickname_preferred_over_name(self, people):
        event, answered, *_ = people
        answered.member.nick = "Nick"
        assert event.get_names_string().split("\n")[0] == "Nick"

    def test_subscribed_only(self, people):
        event, *_ = people
        assert event.get_names_string(subscribed_only=True) == "answered\nwaiting"

    def test_unsubscribed_only(self, people):
        event, *_ = people
        assert event.get_names_string(unsubscribed_only=True) == "unsubscribed"

    def test_subscribed_and_unsubscribed_cancel_out(self, people):
        event, *_ = people
        assert event.get_names_string(subscribed_only=True, unsubscribed_only=True) == event.get_names_string()

    def test_unanswered_only(self, people):
        event, *_ = people
        assert event.get_names_string(unanswered_only=True) == "waiting\nunsubscribed"

    def test_subscribed_and_unanswered(self, people):
        event, *_ = people
        assert event.get_names_string(subscribed_only=True, unanswered_only=True) == "waiting"

    def test_unsubscribed_and_unanswered(self, people):
        event, *_ = people
        assert event.get_names_string(unsubscribed_only=True, unanswered_only=True) == "unsubscribed"

    def test_not_in_voice_channel_only(self, env, people):
        event, answered, *_ = people
        env.voice_channel.members.append(answered.member)
        assert event.get_names_string(subscribed_only=True, not_in_voice_channel_only=True) == "waiting"

    def test_empty_when_nobody_matches(self, env):
        event = env.make_event([env.make_participant("a")])
        assert event.get_names_string(unsubscribed_only=True) == ""
