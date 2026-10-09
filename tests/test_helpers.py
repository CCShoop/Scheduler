from datetime import timedelta
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("minutes, expected", [
    (0, "0 minutes"),
    (1, "1 minute"),
    (45, "45 minutes"),
    (60, "1 hour"),
    (61, "1 hour, 1 minute"),
    (125, "2 hours, 5 minutes"),
    (60 * 24, "1 day"),
    # Minutes are dropped once days are shown
    (60 * 24 + 5, "1 day"),
    (60 * 25, "1 day, 1 hour"),
    (60 * 24 * 3 + 60 * 2, "3 days, 2 hours"),
    # Hours are dropped once weeks are shown
    (60 * 24 * 7, "1 week"),
    (60 * 24 * 8 + 60, "1 week, 1 day"),
    (60 * 24 * 14, "2 weeks"),
    (-61, "1 hour, 1 minute"),
])
def test_get_time_str_from_minutes(sched, minutes, expected):
    assert sched.get_time_str_from_minutes(minutes) == expected


@pytest.mark.parametrize("entered, expected", [("5", "05"), ("0", "00"), ("10", "10"), ("05", "05")])
def test_double_digit_string(sched, entered, expected):
    assert sched.double_digit_string(entered) == expected


def test_double_digit_string_invalid(sched):
    with pytest.raises(ValueError):
        sched.double_digit_string("a")


class TestParseStartTime:
    @pytest.mark.parametrize("entered, hour, minute", [
        ("1630", 16, 30),
        ("16:30", 16, 30),
        ("0030", 0, 30),
        ("930", 9, 30),
        ("7", 7, 0),
        ("23", 23, 0),
    ])
    def test_clock_times_are_next_occurrence(self, sched, entered, hour, minute):
        result = sched.parse_start_time(entered)
        assert (result.hour, result.minute) == (hour, minute)
        assert sched.now() < result <= sched.now() + timedelta(days=1)

    def test_future_iso_is_unchanged(self, sched):
        future = sched.now() + timedelta(days=3)
        assert sched.parse_start_time(future.isoformat()) == future

    def test_past_iso_moves_to_next_day(self, sched):
        past = sched.now() - timedelta(days=2, hours=1)
        result = sched.parse_start_time(past.isoformat())
        assert result > sched.now()
        assert result - past == timedelta(days=3)

    def test_iso_without_offset_is_local_time(self, sched):
        future = (sched.now() + timedelta(days=3)).replace(tzinfo=None)
        result = sched.parse_start_time(future.isoformat())
        assert result.tzinfo is not None
        assert result.replace(tzinfo=None) == future

    def test_iso_without_offset_past_dst_change(self, sched, dst_timezone):
        result = sched.parse_start_time(f"{dst_timezone.isoformat()}T19:00")
        assert result.astimezone().hour == 19

    @pytest.mark.parametrize("entered", ["abc", "12345", "", "25:00"])
    def test_invalid(self, sched, entered):
        with pytest.raises(Exception):
            sched.parse_start_time(entered)


def test_participant_filters(sched):
    a = SimpleNamespace(subscribed=True, answered=True, unavailable=False)
    b = SimpleNamespace(subscribed=False, answered=False, unavailable=True)
    people = [a, b]
    assert sched.get_subscribed_participants(people) == [a]
    assert sched.get_unsubscribed_participants(people) == [b]
    assert sched.get_answered_participants(people) == [a]
    assert sched.get_unanswered_participants(people) == [b]
    assert sched.get_available_participants(people) == [a]
    assert sched.get_unavailable_participants(people) == [b]
