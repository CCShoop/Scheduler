from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from libs.participant import (Participant, TimeBlock, RemovedTime, parse_time_string,
                              DEFAULT_HOURS_PAST_MIDNIGHT_CUTOFF)


def at(days: int, hour: int, minute: int = 0) -> datetime:
    """Local time `days` from today at hour:minute."""
    base = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return (base + timedelta(days=days)).replace(hour=hour, minute=minute)


def date_string(days: int, with_year: bool = True) -> str:
    day = at(days, 0)
    return day.strftime("%Y-%m-%d") if with_year else day.strftime("%m-%d")


def make_participant(**kwargs) -> Participant:
    member = SimpleNamespace(id=1, name="tester", nick=None)
    return Participant(member=member, **kwargs)


def blocks(participant: Participant) -> list[tuple]:
    return [(tb.start_time, tb.end_time) for tb in participant.availability]


class TestParseTimeString:
    @pytest.mark.parametrize("entered, expected", [
        ("9", "0900"),
        ("09", "0900"),
        ("930", "0930"),
        ("1530", "1530"),
        ("1:12pm", "1312"),
        ("12pm", "1200"),
        ("12am", "0000"),
        ("12:30am", "0030"),
        ("11pm", "2300"),
        ("", ""),
        ("pm", ""),
    ])
    def test_valid(self, entered, expected):
        assert parse_time_string(entered, "start") == expected

    @pytest.mark.parametrize("entered", ["13pm", "0am", "2400", "1260", "12345"])
    def test_invalid(self, entered):
        with pytest.raises(Exception, match="Invalid start time"):
            parse_time_string(entered, "start")


class TestTimeBlock:
    def test_duration(self):
        assert TimeBlock(at(1, 8), at(1, 11)).duration == timedelta(hours=3)

    @pytest.mark.parametrize("other, expected", [
        ((at(1, 11), at(1, 12)), False),  # touching after
        ((at(1, 7), at(1, 8)), False),    # touching before
        ((at(1, 10), at(1, 12)), True),   # overlaps end
        ((at(1, 7), at(1, 9)), True),     # overlaps start
        ((at(1, 9), at(1, 10)), True),    # inside
        ((at(1, 7), at(1, 12)), True),    # contains
    ])
    def test_overlaps_with(self, other, expected):
        block = TimeBlock(at(1, 8), at(1, 11))
        assert block.overlaps_with(TimeBlock(*other)) is expected

    def test_subtract_no_overlap_returns_self(self):
        block = TimeBlock(at(1, 8), at(1, 11))
        assert block.subtract(TimeBlock(at(1, 12), at(1, 13))) == [block]

    def test_subtract_middle_splits(self):
        result = TimeBlock(at(1, 8), at(1, 11)).subtract(TimeBlock(at(1, 9), at(1, 10)))
        assert [(tb.start_time, tb.end_time) for tb in result] == [(at(1, 8), at(1, 9)), (at(1, 10), at(1, 11))]

    def test_subtract_start_trims(self):
        result = TimeBlock(at(1, 8), at(1, 11)).subtract(TimeBlock(at(1, 7), at(1, 9)))
        assert [(tb.start_time, tb.end_time) for tb in result] == [(at(1, 9), at(1, 11))]

    def test_subtract_covering_removes(self):
        assert TimeBlock(at(1, 8), at(1, 11)).subtract(TimeBlock(at(1, 7), at(1, 12))) == []

    def test_dict_round_trip(self):
        block = TimeBlock(at(1, 8), at(1, 11))
        restored = TimeBlock.from_dict(block.to_dict())
        assert (restored.start_time, restored.end_time) == (block.start_time, block.end_time)

    def test_from_dict_none(self):
        assert TimeBlock.from_dict(None) is None


class TestRemovedTime:
    def test_dict_round_trip(self):
        removed = RemovedTime("event", TimeBlock(at(1, 8), at(1, 10)), TimeBlock(at(1, 9), at(1, 10)))
        restored = RemovedTime.from_dict(removed.to_dict())
        assert restored.event_name == "event"
        assert (restored.start_time, restored.end_time) == (at(1, 8), at(1, 10))
        assert restored.removed_timeblock.start_time == at(1, 9)

    def test_dict_round_trip_without_removed_timeblock(self):
        removed = RemovedTime("event", TimeBlock(at(1, 8), at(1, 10)), None)
        assert RemovedTime.from_dict(removed.to_dict()).removed_timeblock is None


class TestAvailabilityEditing:
    def test_add_merges_overlapping_and_touching_blocks(self):
        participant = make_participant()
        participant.add_to_availability(TimeBlock(at(1, 13), at(1, 15)))
        participant.add_to_availability(TimeBlock(at(1, 8), at(1, 10)))
        participant.add_to_availability(TimeBlock(at(1, 10), at(1, 11)))  # touching
        participant.add_to_availability(TimeBlock(at(1, 14), at(1, 16)))  # overlapping
        assert blocks(participant) == [(at(1, 8), at(1, 11)), (at(1, 13), at(1, 16))]
        assert participant.answered

    def test_add_none_is_ignored(self):
        participant = make_participant()
        participant.add_to_availability(None)
        assert participant.availability == []

    def test_is_available_at(self):
        participant = make_participant(availability=[TimeBlock(at(1, 8), at(1, 11))])
        assert participant.is_available_at(at(1, 8), timedelta(hours=3))
        assert participant.is_available_at(at(1, 9), timedelta(hours=1))
        assert not participant.is_available_at(at(1, 10), timedelta(hours=2))
        assert not participant.is_available_at(at(1, 7), timedelta(hours=1))

    def test_remove_from_availability_splits(self):
        participant = make_participant(availability=[TimeBlock(at(1, 8), at(1, 12))])
        participant.remove_from_availability(TimeBlock(at(1, 9), at(1, 10)))
        assert blocks(participant) == [(at(1, 8), at(1, 9)), (at(1, 10), at(1, 12))]

    def test_get_availability_overlap(self):
        participant = make_participant(availability=[TimeBlock(at(1, 8), at(1, 10)), TimeBlock(at(1, 13), at(1, 15))])
        overlap = participant.get_availability_overlap(TimeBlock(at(1, 14), at(1, 16)))
        assert (overlap.start_time, overlap.end_time) == (at(1, 14), at(1, 15))
        assert participant.get_availability_overlap(TimeBlock(at(1, 11), at(1, 12))) is None

    def test_remove_and_restore_availability_for_event(self):
        participant = make_participant(availability=[TimeBlock(at(1, 8), at(1, 12))])
        participant.remove_availability_for_event("other", [TimeBlock(at(1, 9), at(1, 10))])
        assert blocks(participant) == [(at(1, 8), at(1, 9)), (at(1, 10), at(1, 12))]
        assert len(participant.removed_times) == 1

        participant.restore_availability_for_event("other")
        assert blocks(participant) == [(at(1, 8), at(1, 12))]
        assert participant.removed_times == []

    def test_restore_only_affects_named_event(self):
        participant = make_participant(availability=[TimeBlock(at(1, 8), at(1, 12))])
        participant.remove_availability_for_event("first", [TimeBlock(at(1, 8), at(1, 9))])
        participant.remove_availability_for_event("second", [TimeBlock(at(1, 11), at(1, 12))])
        participant.restore_availability_for_event("first")
        assert blocks(participant) == [(at(1, 8), at(1, 11))]
        assert [rt.event_name for rt in participant.removed_times] == ["second"]

    def test_remove_availability_for_event_replaces_previous_removal(self):
        participant = make_participant(availability=[TimeBlock(at(1, 8), at(1, 12))])
        participant.remove_availability_for_event("other", [TimeBlock(at(1, 8), at(1, 9))])
        participant.remove_availability_for_event("other", [TimeBlock(at(1, 11), at(1, 12))])
        assert blocks(participant) == [(at(1, 8), at(1, 11))]
        assert len(participant.removed_times) == 1

    def test_confirm_answered_drops_past_and_trims_current_blocks(self):
        now = datetime.now().astimezone().replace(second=0, microsecond=0)
        participant = make_participant(availability=[TimeBlock(now - timedelta(hours=3), now - timedelta(hours=1)),
                                                     TimeBlock(now - timedelta(hours=1), now + timedelta(hours=2))],
                                       answered=True)
        participant.confirm_answered(duration=timedelta(minutes=30))
        assert blocks(participant) == [(now, now + timedelta(hours=2))]
        assert participant.answered

    def test_confirm_answered_drops_blocks_too_short_for_duration(self):
        now = datetime.now().astimezone().replace(second=0, microsecond=0)
        participant = make_participant(availability=[TimeBlock(now, now + timedelta(minutes=20))],
                                       answered=True, full_availability_flag=True)
        participant.confirm_answered(duration=timedelta(minutes=30))
        assert participant.availability == []
        assert not participant.answered
        assert not participant.full_availability_flag

    def test_update_removed_times_drops_past_removals(self):
        participant = make_participant()
        participant.removed_times = [RemovedTime("past", TimeBlock(at(-1, 8), at(-1, 9)), None),
                                     RemovedTime("future", TimeBlock(at(1, 8), at(1, 9)), None)]
        participant.update_removed_times()
        assert [rt.event_name for rt in participant.removed_times] == ["future"]

    def test_set_no_availability_for_one_day(self):
        participant = make_participant(availability=[TimeBlock(at(1, 8), at(1, 10)), TimeBlock(at(2, 8), at(2, 10))],
                                       full_availability_flag=True)
        tomorrow = at(1, 0)
        participant.set_no_availability(tomorrow.date())
        assert blocks(participant) == [(at(2, 8), at(2, 10))]
        assert not participant.full_availability_flag

    def test_set_no_availability_clears_all(self):
        participant = make_participant(availability=[TimeBlock(at(1, 8), at(1, 10))])
        participant.set_no_availability()
        assert participant.availability == []

    def test_dict_round_trip(self):
        member = SimpleNamespace(id=1, name="tester", nick=None)
        participant = Participant(member=member, availability=[TimeBlock(at(1, 8), at(1, 10))],
                                  answered=True, note="hi",
                                  removed_times=[RemovedTime("other", TimeBlock(at(2, 8), at(2, 9)), None)])
        guild = SimpleNamespace(get_member=lambda member_id: member if member_id == 1 else None)
        restored = Participant.from_dict(guild, participant.to_dict())
        assert restored.member is member
        assert restored.note == "hi"
        assert restored.answered
        assert blocks(restored) == blocks(participant)
        assert restored.removed_times[0].event_name == "other"


class TestSetSpecificAvailability:
    def test_simple_range(self):
        participant = make_participant()
        participant.set_specific_availability("8-11", date_string(1))
        assert blocks(participant) == [(at(1, 8), at(1, 11))]

    def test_multiple_ranges_and_am_pm(self):
        participant = make_participant()
        participant.set_specific_availability("8-11, 1pm-3pm", date_string(1))
        assert blocks(participant) == [(at(1, 8), at(1, 11)), (at(1, 13), at(1, 15))]

    def test_minutes_with_colons(self):
        participant = make_participant()
        participant.set_specific_availability("15:30-17", date_string(1))
        assert blocks(participant) == [(at(1, 15, 30), at(1, 17))]

    def test_end_before_start_rolls_to_next_day(self):
        participant = make_participant()
        participant.set_specific_availability("22-2", date_string(1))
        assert blocks(participant) == [(at(1, 22), at(2, 2))]

    def test_missing_end_is_midnight(self):
        participant = make_participant()
        participant.set_specific_availability("20-", date_string(1))
        assert blocks(participant) == [(at(1, 20), at(2, 0))]

    def test_missing_start_on_future_day_is_midnight(self):
        participant = make_participant()
        participant.set_specific_availability("-6", date_string(1))
        assert blocks(participant) == [(at(1, 0), at(1, 6))]

    def test_extension_repeats_on_following_days(self):
        participant = make_participant()
        participant.set_specific_availability("20-22x3", date_string(1))
        assert blocks(participant) == [(at(1, 20), at(1, 22)), (at(2, 20), at(2, 22)), (at(3, 20), at(3, 22))]

    @pytest.mark.parametrize("zone, offset", [("et", 0), ("ct", 1), ("mt", 2), ("pt", 3), ("at", -1), ("cst", 1), ("pdt", 3)])
    def test_timezone_offsets(self, zone, offset):
        participant = make_participant()
        participant.set_specific_availability(f"8-11 {zone}", date_string(1))
        assert blocks(participant) == [(at(1, 8 + offset), at(1, 11 + offset))]

    def test_date_without_year(self):
        participant = make_participant()
        participant.set_specific_availability("8-11", date_string(1, with_year=False))
        assert blocks(participant) == [(at(1, 8), at(1, 11))]

    def test_two_digit_year(self):
        participant = make_participant()
        tomorrow = at(1, 0)
        participant.set_specific_availability("8-11", tomorrow.strftime("%y-%m-%d"))
        assert blocks(participant) == [(at(1, 8), at(1, 11))]

    def test_full_keyword(self):
        participant = make_participant()
        participant.set_specific_availability("full", date_string(1))
        assert blocks(participant) == [(at(1, 0), at(2, DEFAULT_HOURS_PAST_MIDNIGHT_CUTOFF))]
        assert participant.full_availability_flag
        assert participant.answered

    def test_clear_keyword_only_clears_that_day(self):
        participant = make_participant(availability=[TimeBlock(at(1, 8), at(1, 10)), TimeBlock(at(2, 8), at(2, 10))])
        participant.set_specific_availability("clear", date_string(1))
        assert blocks(participant) == [(at(2, 8), at(2, 10))]

    def test_clear_keyword_extension_clears_following_days(self):
        participant = make_participant(availability=[TimeBlock(at(1, 8), at(1, 10)), TimeBlock(at(2, 8), at(2, 10)),
                                                     TimeBlock(at(3, 8), at(3, 10))])
        participant.set_specific_availability("clear x2", date_string(1))
        assert blocks(participant) == [(at(3, 8), at(3, 10))]

    def test_none_keyword_clears_everything(self):
        participant = make_participant(availability=[TimeBlock(at(1, 8), at(1, 10)), TimeBlock(at(2, 8), at(2, 10))])
        participant.set_specific_availability("none", date_string(1))
        assert participant.availability == []

    def test_empty_string_does_nothing(self):
        participant = make_participant()
        participant.set_specific_availability("", date_string(1))
        assert participant.availability == []

    @pytest.mark.parametrize("avail, match", [
        ("8--11", "double hyphen"),
        ("8-now", "cannot use current time as end time"),
        ("8-11xz", "Invalid extension"),
        ("25-26", "Invalid start time"),
    ])
    def test_invalid_times(self, avail, match):
        with pytest.raises(Exception, match=match):
            make_participant().set_specific_availability(avail, date_string(1))

    @pytest.mark.parametrize("date_entered, match", [
        ("2030-13-01", "Invalid month"),
        ("2030/02/30", "Invalid day"),
        ("02/15/2030", "must be in YYYY-MM-DD format"),
        ("2-15-2030", "must be in YYYY-MM-DD format"),
        ("abc", "Invalid date format"),
        ("a/b", "Invalid month"),
    ])
    def test_invalid_dates(self, date_entered, match):
        with pytest.raises(Exception, match=match):
            make_participant().set_specific_availability("8-11", date_entered)

    def test_past_date_with_year_raises(self):
        with pytest.raises(Exception, match="Cannot set availability for a past date"):
            make_participant().set_specific_availability("8-11", date_string(-1))

    def test_past_date_without_year_means_next_year(self):
        participant = make_participant()
        yesterday = at(-1, 0)
        # Feb 29 may not exist next year; that case raises instead
        if (yesterday.month, yesterday.day) == (2, 29):
            pytest.skip("Feb 29 has no equivalent next year")
        participant.set_specific_availability("8-11", yesterday.strftime("%m-%d"))
        start = participant.availability[0].start_time
        assert (start.year, start.month, start.day, start.hour) == (yesterday.year + 1, yesterday.month, yesterday.day, 8)


class TestAvailabilityString:
    def test_includes_note_full_flag_and_removed_times_in_order(self):
        participant = make_participant(availability=[TimeBlock(at(1, 8), at(1, 9)), TimeBlock(at(1, 12), at(1, 13))],
                                       note="late", full_availability_flag=True)
        participant.removed_times = [RemovedTime("busy", TimeBlock(at(1, 10), at(1, 11)), None)]
        lines = participant.availability_string.strip().split("\n")
        assert lines[0] == '[Note] "late"'
        assert lines[1] == "[Full Availability]"
        assert lines[2].startswith("[Free]")
        assert lines[3].startswith("[Busy] [busy]")
        assert lines[4].startswith("[Free]")


class TestAvailabilityAcrossDstChange:
    """Times on a date past a DST change keep the entered time of day, rather than shifting by an hour."""

    @staticmethod
    def local_times(participant: Participant) -> list[tuple]:
        return [(tb.start_time.astimezone().strftime("%m-%d %H:%M"), tb.end_time.astimezone().strftime("%m-%d %H:%M"))
                for tb in participant.availability]

    def test_specific_range(self, dst_timezone):
        participant = make_participant()
        participant.set_specific_availability("7pm-9pm", dst_timezone.strftime("%Y-%m-%d"))
        day = dst_timezone.strftime("%m-%d")
        assert self.local_times(participant) == [(f"{day} 19:00", f"{day} 21:00")]

    def test_missing_end_is_that_days_midnight(self, dst_timezone):
        participant = make_participant()
        participant.set_specific_availability("20-", dst_timezone.strftime("%Y-%m-%d"))
        next_day = (dst_timezone + timedelta(days=1)).strftime("%m-%d")
        assert self.local_times(participant)[0][1] == f"{next_day} 00:00"

    def test_extension_crossing_the_change(self, dst_timezone):
        participant = make_participant()
        day_before = dst_timezone - timedelta(days=2)
        participant.set_specific_availability("19-21x3", day_before.strftime("%Y-%m-%d"))
        assert [start[6:] for start, _ in self.local_times(participant)] == ["19:00"] * 3

    def test_full_availability(self, dst_timezone):
        participant = make_participant()
        participant.set_specific_availability("full", dst_timezone.strftime("%Y-%m-%d"))
        day = dst_timezone.strftime("%m-%d")
        next_day = (dst_timezone + timedelta(days=1)).strftime("%m-%d")
        assert self.local_times(participant) == [(f"{day} 00:00", f"{next_day} 00:00")]
