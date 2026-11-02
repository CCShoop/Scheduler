from datetime import timedelta
from types import SimpleNamespace

import pytest
from discord import EntityType

from libs.participant import Participant, RemovedTime, TimeBlock
from fakes import FakeScheduledEvent, at, make_member, run
from test_lifecycle import FakeInteraction


def make_created(env, days=(1,), **kwargs):
    participants = kwargs.pop("participants", None) or [env.make_participant("a")]
    start_times = [at(day, 20) for day in days]
    return env.make_event(participants, multi_event=len(days) > 1, created=True, start_times=list(start_times),
                          scheduled_events=[FakeScheduledEvent(start_time) for start_time in start_times], **kwargs)


def fields(embed):
    return [(field.name, field.value) for field in embed.fields]


class TestValidation:
    @pytest.mark.parametrize("kwargs, message", [
        ({"name": ""}, "Event name must be"),
        ({"name": "x" * 101}, "Event name must be"),
        ({"duration": -1}, "Duration must be"),
        ({"duration": 24 * 60 + 1}, "Duration must be"),
        ({"timeout_days": 0}, "Timeout must be"),
        ({"timeout_days": 31}, "Timeout must be"),
    ])
    def test_invalid_options_change_nothing(self, env, kwargs, message):
        event = env.make_event([env.make_participant("a")])
        name, duration, timeout_at = event.name, event.duration, event.timeout_at
        with pytest.raises(Exception, match=message):
            run(env.sched.edit_event(event, **kwargs))
        assert (event.name, event.duration, event.timeout_at) == (name, duration, timeout_at)

    def test_limits_are_inclusive(self, env):
        event = env.make_event([env.make_participant("a")])
        run(env.sched.edit_event(event, name="x" * 100, duration=24 * 60, timeout_days=30))
        assert event.name == "x" * 100
        assert event.duration == timedelta(days=1)

    def test_no_options(self, env):
        event = env.make_event([env.make_participant("a")])
        embed = run(env.sched.edit_event(event))
        assert embed.title == f"{event} Edited"
        assert embed.fields == []
        assert embed.thumbnail.url is None

    def test_reports_every_edited_option_in_order(self, env):
        event = env.make_event([env.make_participant("a")])
        embed = run(env.sched.edit_event(event, name="New", location="Park", image_url="https://x/new.png",
                                         duration=60, multi_event=True, timeout_days=5))
        assert [name for name, _ in fields(embed)] == ["Name", "Location", "Image", "Duration", "Multi Event", "Timeout"]


class TestName:
    def test_unchanged(self, env):
        event = make_created(env)
        embed = run(env.sched.edit_event(event, name=event.name))
        assert fields(embed) == [("Name (Unchanged)", "The new name is the same as the old name")]
        assert event.scheduled_events[0].edits == []

    def test_renames_uncreated_event(self, env):
        event = env.make_event([env.make_participant("a")])
        old_name = event.name
        embed = run(env.sched.edit_event(event, name="Game Night"))
        assert event.name == "Game Night"
        assert fields(embed) == [("Name", f"{old_name} -> Game Night")]

    def test_renames_every_guild_event(self, env):
        event = make_created(env, days=(1, 2))
        run(env.sched.edit_event(event, name="Game Night"))
        assert [scheduled_event.edits for scheduled_event in event.scheduled_events] == \
            [[{"name": "Game Night"}], [{"name": "Game Night"}]]

    def test_long_names_are_shortened_in_embed(self, env):
        event = env.make_event([env.make_participant("a")])
        event.name = "Original name that is long"
        embed = run(env.sched.edit_event(event, name="A new name that is also very long"))
        assert fields(embed) == [("Name", "Original name that i -> A new name that i...")]

    def test_moves_removed_availability_to_new_name(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        event = make_created(env, participants=[a])
        other_a = Participant(member=a.member, availability=[TimeBlock(at(1, 18), at(1, 23))])
        env.make_event([other_a, env.make_participant("b")])
        env.sched.remove_times_from_availabilities_for_events()
        assert [removed.event_name for removed in other_a.removed_times] == [event.name]

        run(env.sched.edit_event(event, name="Renamed"))
        assert [removed.event_name for removed in other_a.removed_times] == ["Renamed"]
        assert [(block.start_time, block.end_time) for block in other_a.availability] == \
            [(at(1, 18), at(1, 20)), (at(1, 22), at(1, 23))]


class TestLocation:
    def test_voice_to_other_voice_channel(self, env):
        other_channel = SimpleNamespace(id=3, name="Other", members=[], mention="<#3>")
        event = make_created(env)
        embed = run(env.sched.edit_event(event, voice_channel=other_channel))
        assert event.voice_channel is other_channel
        assert event.scheduled_events[0].edits == [{"entity_type": EntityType.voice, "channel": other_channel,
                                                   "end_time": at(1, 22)}]
        assert fields(embed) == [("Location", f"{env.voice_channel.mention} -> <#3>")]

    def test_same_voice_channel_is_unchanged(self, env):
        event = make_created(env)
        embed = run(env.sched.edit_event(event, voice_channel=env.voice_channel))
        assert fields(embed) == [("Location (Unchanged)", f"Location is already {env.voice_channel.mention}")]
        assert event.scheduled_events[0].edits == []

    def test_external_location_takes_priority_over_voice_channel(self, env):
        event = env.make_event([env.make_participant("a")])
        run(env.sched.edit_event(event, location="Park", voice_channel=env.voice_channel))
        assert event.location == "Park" and event.voice_channel is None

    def test_external_to_other_external(self, env):
        event = env.make_event([env.make_participant("a")], location="Park")
        embed = run(env.sched.edit_event(event, location="Library"))
        assert event.location == "Library"
        assert fields(embed) == [("Location", "Park -> Library")]

    def test_uncreated_event_has_no_guild_events_to_edit(self, env):
        event = env.make_event([env.make_participant("a")])
        run(env.sched.edit_event(event, location="Park"))
        assert event.location == "Park"
        assert event.scheduled_events == []

    def test_each_occurrence_gets_its_own_end_time(self, env):
        event = make_created(env, days=(1, 2))
        run(env.sched.edit_event(event, location="Park"))
        assert [scheduled_event.edits[0]["end_time"] for scheduled_event in event.scheduled_events] == \
            [at(1, 22), at(2, 22)]


@pytest.fixture
def image_dir(tmp_path, monkeypatch):
    """Image files are saved relative to the working directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def fake_download(monkeypatch, env, succeeds=True):
    async def save_image_to_file(self):
        if succeeds:
            with open(self.image_path, "wb") as file:
                file.write(self.image_url.encode())
        else:
            self.image_url = None

    monkeypatch.setattr(env.sched.Event, "save_image_to_file", save_image_to_file)


class TestImage:
    def test_sets_image_of_uncreated_event_without_downloading(self, env, image_dir, monkeypatch):
        fake_download(monkeypatch, env, succeeds=False)
        event = env.make_event([env.make_participant("a")])
        embed = run(env.sched.edit_event(event, image_url="https://x/new.png"))
        assert event.image_url == "https://x/new.png"
        assert not event.has_image_saved
        assert fields(embed) == [("Image", "None -> https://x/new.png")]
        assert embed.thumbnail.url == "https://x/new.png"

    def test_downloads_and_sets_image_of_every_guild_event(self, env, image_dir, monkeypatch):
        fake_download(monkeypatch, env)
        event = make_created(env, days=(1, 2), image_url="https://x/old.png")
        (image_dir / event.image_path).write_bytes(b"old")
        embed = run(env.sched.edit_event(event, image_url="https://x/new.png"))
        assert event.image_url == "https://x/new.png"
        assert (image_dir / event.image_path).read_bytes() == b"https://x/new.png"
        assert [scheduled_event.edits for scheduled_event in event.scheduled_events] == \
            [[{"image": b"https://x/new.png"}]] * 2
        assert fields(embed) == [("Image", "https://x/old.png -> https://x/new.png")]

    def test_failed_download_keeps_old_image(self, env, image_dir, monkeypatch):
        fake_download(monkeypatch, env, succeeds=False)
        event = make_created(env, image_url="https://x/old.png")
        embed = run(env.sched.edit_event(event, image_url="https://x/broken.png"))
        assert event.image_url == "https://x/old.png"
        assert event.scheduled_events[0].edits == []
        assert fields(embed) == [("Image (Unchanged)", "The new image could not be downloaded")]
        assert embed.thumbnail.url == "https://x/old.png"

    def test_failed_download_without_old_image(self, env, image_dir, monkeypatch):
        fake_download(monkeypatch, env, succeeds=False)
        event = make_created(env)
        embed = run(env.sched.edit_event(event, image_url="https://x/broken.png"))
        assert event.image_url is None
        assert fields(embed) == [("Image (Unchanged)", "The new image could not be downloaded")]
        assert embed.thumbnail.url is None

    def test_same_image_keeps_saved_file(self, env, image_dir, monkeypatch):
        fake_download(monkeypatch, env, succeeds=False)
        event = make_created(env, image_url="https://x/old.png")
        (image_dir / event.image_path).write_bytes(b"old")
        embed = run(env.sched.edit_event(event, image_url="https://x/old.png"))
        assert event.has_image_saved
        assert event.scheduled_events[0].edits == []
        assert fields(embed) == [("Image (Unchanged)", "image is already https://x/old.png")]


class TestDuration:
    def test_unchanged(self, env):
        event = env.make_event([env.make_participant("a")])
        embed = run(env.sched.edit_event(event, duration=120))
        assert fields(embed) == [("Duration (Unchanged)", "The new duration is the same as the old duration")]

    def test_changed(self, env):
        event = env.make_event([env.make_participant("a")])
        embed = run(env.sched.edit_event(event, duration=90))
        assert event.duration == timedelta(minutes=90)
        assert fields(embed) == [("Duration", "2 hours -> 1 hour, 30 minutes")]

    def test_automatic_duration(self, env):
        event = env.make_event([env.make_participant("a")])
        embed = run(env.sched.edit_event(event, duration=0))
        assert event.duration == timedelta(0)
        assert fields(embed) == [("Duration", "2 hours -> 0 minutes")]

    def test_extends_uncreated_event(self, env):
        event = env.make_event([env.make_participant("a")])
        embed = run(env.sched.edit_event(event, duration=180))
        assert event.duration == timedelta(hours=3)
        assert fields(embed) == [("Duration", "2 hours -> 3 hours")]

    def test_extends_created_event(self, env):
        event = make_created(env, days=(1, 2))
        embed = run(env.sched.edit_event(event, duration=180))
        assert event.duration == timedelta(hours=3)
        assert fields(embed) == [("Duration", "2 hours -> 3 hours")]
        assert [scheduled_event.edits for scheduled_event in event.scheduled_events] == \
            [[{"end_time": at(1, 23)}], [{"end_time": at(2, 23)}]]

    def test_extension_stops_at_next_event_in_voice_channel(self, env):
        event = make_created(env)
        other = env.make_event([env.make_participant("b")], created=True, start_times=[at(1, 22, 30)],
                               scheduled_events=[FakeScheduledEvent(at(1, 22, 30))])
        embed = run(env.sched.edit_event(event, duration=240))
        assert event.duration == timedelta(hours=2, minutes=30)
        assert event.scheduled_events[0].edits == [{"end_time": at(1, 22, 30)}]
        assert fields(embed) == [("Duration", "2 hours -> 2 hours, 30 minutes"
                                  f"\nLimited to 2 hours, 30 minutes so it ends before {other} starts in the same voice channel.")]

    def test_extension_leaves_buffer_before_next_event(self, env, monkeypatch):
        monkeypatch.setattr(env.sched, "EVENT_BUFFER_MINUTES", 10)
        event = make_created(env)
        env.make_event([env.make_participant("b")], created=True, start_times=[at(1, 22, 30)],
                       scheduled_events=[FakeScheduledEvent(at(1, 22, 30))])
        run(env.sched.edit_event(event, duration=240))
        assert event.duration == timedelta(hours=2, minutes=20)
        assert event.scheduled_events[0].edits == [{"end_time": at(1, 22, 20)}]

    def test_cannot_extend_into_buffer(self, env, monkeypatch):
        monkeypatch.setattr(env.sched, "EVENT_BUFFER_MINUTES", 10)
        event = make_created(env)
        env.make_event([env.make_participant("b")], created=True, start_times=[at(1, 22, 10)],
                       scheduled_events=[FakeScheduledEvent(at(1, 22, 10))])
        embed = run(env.sched.edit_event(event, duration=180))
        assert event.duration == timedelta(hours=2)
        assert fields(embed)[0][0] == "Duration (Unchanged)"

    def test_extension_stops_at_own_next_occurrence(self, env):
        event = make_created(env, days=(1, 2))
        run(env.sched.edit_event(event, duration=24 * 60))
        assert event.duration == timedelta(hours=24)

    def test_cannot_extend_when_next_event_starts_at_end(self, env):
        event = make_created(env)
        other = env.make_event([env.make_participant("b")], created=True, start_times=[at(1, 22)],
                               scheduled_events=[FakeScheduledEvent(at(1, 22))])
        embed = run(env.sched.edit_event(event, duration=180))
        assert event.duration == timedelta(hours=2)
        assert event.scheduled_events[0].edits == []
        assert fields(embed) == [("Duration (Unchanged)", f"The duration cannot be extended because {other} "
                                  "starts right after this event ends in the same voice channel")]

    def test_uncreated_and_other_channel_events_do_not_limit(self, env):
        event = make_created(env)
        env.make_event([env.make_participant("b")], start_times=[at(1, 21)])
        other_channel = SimpleNamespace(id=3, name="Other", members=[], mention="<#3>")
        env.make_event([env.make_participant("c")], created=True, voice_channel=other_channel,
                       start_times=[at(1, 21)], scheduled_events=[FakeScheduledEvent(at(1, 21))])
        run(env.sched.edit_event(event, duration=180))
        assert event.duration == timedelta(hours=3)

    def test_external_event_is_not_limited(self, env):
        event = make_created(env, location="Park")
        env.make_event([env.make_participant("b")], location="Park", created=True, start_times=[at(1, 21)],
                       scheduled_events=[FakeScheduledEvent(at(1, 21))])
        run(env.sched.edit_event(event, duration=180))
        assert event.duration == timedelta(hours=3)

    def test_extension_stops_at_end_of_common_availability(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        b = env.make_participant("b", [TimeBlock(at(1, 20), at(1, 22, 45))])
        event = make_created(env, participants=[a, b, env.make_participant("unanswered")])
        embed = run(env.sched.edit_event(event, duration=240))
        assert event.duration == timedelta(hours=2, minutes=45)
        assert event.scheduled_events[0].edits == [{"end_time": at(1, 22, 45)}]
        assert fields(embed) == [("Duration", "2 hours -> 2 hours, 45 minutes"
                                  "\nLimited to 2 hours, 45 minutes because 4 hours is longer than the participants' "
                                  "common availability. To make the event duration 4 hours, you will need to press "
                                  "Reschedule Event, then set the duration again to find a time that fits it.")]

    def test_unsubscribed_participants_do_not_limit(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        b = env.make_participant("b", [TimeBlock(at(1, 20), at(1, 22))])
        b.subscribed = False
        event = make_created(env, participants=[a, b])
        run(env.sched.edit_event(event, duration=180))
        assert event.duration == timedelta(hours=3)

    def test_multi_event_uses_shortest_occurrence_availability(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23)), TimeBlock(at(2, 19), at(2, 22, 30))])
        event = make_created(env, days=(1, 2), participants=[a])
        run(env.sched.edit_event(event, duration=240))
        assert event.duration == timedelta(hours=2, minutes=30)

    def test_cannot_extend_when_common_availability_ends_with_event(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 22))])
        event = make_created(env, participants=[a])
        embed = run(env.sched.edit_event(event, duration=180))
        assert event.duration == timedelta(hours=2)
        assert event.scheduled_events[0].edits == []
        assert fields(embed) == [("Duration (Unchanged)", "The duration cannot be extended because the participants' "
                                  "common availability ends when this event ends. To make the event duration 3 hours, "
                                  "you will need to press Reschedule Event, then set the duration again to find a "
                                  "time that fits it.")]

    def test_started_event_does_not_suggest_rescheduling(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        event = make_created(env, participants=[a], started=True)
        embed = run(env.sched.edit_event(event, duration=240))
        assert event.duration == timedelta(hours=3)
        assert fields(embed) == [("Duration", "2 hours -> 3 hours\nLimited to 3 hours because 4 hours is longer "
                                  "than the participants' common availability.")]

    def test_earlier_voice_channel_limit_is_reported(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        event = make_created(env, participants=[a])
        other = env.make_event([env.make_participant("b")], created=True, start_times=[at(1, 22, 30)],
                               scheduled_events=[FakeScheduledEvent(at(1, 22, 30))])
        embed = run(env.sched.edit_event(event, duration=240))
        assert event.duration == timedelta(hours=2, minutes=30)
        assert fields(embed)[0][1].endswith(f"so it ends before {other} starts in the same voice channel.")

    def make_invited(self, env, name):
        """A participant invited with /create, who counts as answered without entering availability."""
        participant = env.make_participant(name)
        participant.answered = True
        return participant

    def test_created_participants_without_availability_do_not_limit(self, env):
        event = make_created(env, participants=[self.make_invited(env, "a"), self.make_invited(env, "b")])
        embed = run(env.sched.edit_event(event, duration=300))
        assert event.duration == timedelta(hours=5)
        assert fields(embed) == [("Duration", "2 hours -> 5 hours")]

    def test_participants_other_event_limits_without_availability(self, env, monkeypatch):
        monkeypatch.setattr(env.sched, "EVENT_BUFFER_MINUTES", 10)
        a = self.make_invited(env, "a")
        event = make_created(env, participants=[a, self.make_invited(env, "b")])
        other_channel = SimpleNamespace(id=3, name="Other", members=[], mention="<#3>")
        other = env.make_event([Participant(member=a.member)], created=True, voice_channel=other_channel,
                               start_times=[at(1, 23)],
                               scheduled_events=[FakeScheduledEvent(at(1, 23))])
        env.sched.remove_times_from_availabilities_for_events()
        embed = run(env.sched.edit_event(event, duration=300))
        assert event.duration == timedelta(hours=2, minutes=50)
        assert fields(embed) == [("Duration", "2 hours -> 2 hours, 50 minutes"
                                  f"\nLimited to 2 hours, 50 minutes so it ends before {other} starts, "
                                  "which shares participants with it.")]

    def test_cannot_extend_into_participants_next_event(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        event = make_created(env, participants=[a])
        other_channel = SimpleNamespace(id=3, name="Other", members=[], mention="<#3>")
        other = env.make_event([Participant(member=a.member, availability=[TimeBlock(at(1, 18), at(1, 23))])],
                               created=True, voice_channel=other_channel, start_times=[at(1, 22)],
                               scheduled_events=[FakeScheduledEvent(at(1, 22))])
        env.sched.remove_times_from_availabilities_for_events()
        embed = run(env.sched.edit_event(event, duration=180))
        assert event.duration == timedelta(hours=2)
        assert fields(embed) == [("Duration (Unchanged)", f"The duration cannot be extended because {other}, which "
                                  "shares participants with this event, starts right after it ends")]

    def test_earlier_and_unsubscribed_participant_events_do_not_limit(self, env):
        a = self.make_invited(env, "a")
        b = self.make_invited(env, "b")
        b.subscribed = False
        event = make_created(env, participants=[a, b])
        a.removed_times = [RemovedTime("earlier", TimeBlock(at(1, 18), at(1, 19)))]
        b.removed_times = [RemovedTime("later", TimeBlock(at(1, 21), at(1, 22)))]
        run(env.sched.edit_event(event, duration=180))
        assert event.duration == timedelta(hours=3)

    def make_started(self, env, elapsed, **kwargs):
        start = env.sched.now() - elapsed
        return env.make_event(kwargs.pop("participants", None) or [env.make_participant("a")], created=True, started=True,
                              start_times=[start], scheduled_events=[FakeScheduledEvent(start)], **kwargs)

    def test_started_event_running_past_availability_cannot_extend(self, env):
        now = env.sched.now()
        a = env.make_participant("a", [TimeBlock(now - timedelta(hours=3), now - timedelta(minutes=15))])
        event = self.make_started(env, timedelta(hours=2, minutes=15), participants=[a])
        embed = run(env.sched.edit_event(event, duration=240))
        assert event.duration == timedelta(hours=2)
        assert fields(embed)[0][0] == "Duration (Unchanged)"

    def test_started_event_availability_cut_to_now_still_limits(self, env):
        now = env.sched.now()
        # Availability is cut to start now once confirmed, after the event started
        a = env.make_participant("a", [TimeBlock(now, now + timedelta(hours=1))])
        event = self.make_started(env, timedelta(hours=1), participants=[a])
        run(env.sched.edit_event(event, duration=240))
        assert event.duration == timedelta(hours=2)

    def test_failed_guild_event_edit_is_reported(self, env):
        class FailingScheduledEvent(FakeScheduledEvent):
            async def edit(self, **kwargs):
                raise RuntimeError("404 Unknown Guild Scheduled Event")

        event = make_created(env, days=(1, 2))
        event.scheduled_events = [FailingScheduledEvent(at(1, 20)), FailingScheduledEvent(at(2, 20))]
        embed = run(env.sched.edit_event(event, duration=60))
        assert event.duration == timedelta(hours=1)
        assert fields(embed) == [("Duration", "2 hours -> 1 hour"
                                  "\nThe Discord event couldn't be updated, so it may still show the old end time.")]

    def test_shortening_started_external_event_below_elapsed_time_warns(self, env):
        event = self.make_started(env, timedelta(minutes=90), location="Park")
        embed = run(env.sched.edit_event(event, duration=60))
        assert fields(embed) == [("Duration", "2 hours -> 1 hour\nIt has already run longer than that, so it will end now.")]

    @pytest.mark.parametrize("location, elapsed", [("Park", timedelta(minutes=30)), (None, timedelta(minutes=90))])
    def test_no_end_warning_otherwise(self, env, location, elapsed):
        event = self.make_started(env, elapsed, location=location)
        embed = run(env.sched.edit_event(event, duration=60))
        assert fields(embed) == [("Duration", "2 hours -> 1 hour")]

    def test_cannot_make_created_event_automatic(self, env):
        event = make_created(env)
        embed = run(env.sched.edit_event(event, duration=0))
        assert event.duration == timedelta(hours=2)
        assert fields(embed) == [("Duration (Unchanged)", "The duration of a created event cannot be made automatic")]

    def test_extends_created_automatic_event_past_default(self, env):
        event = make_created(env, duration=timedelta(0))
        run(env.sched.edit_event(event, duration=env.sched.DEFAULT_EVENT_DURATION + 1))
        assert event.duration == timedelta(minutes=env.sched.DEFAULT_EVENT_DURATION + 1)

    def test_sets_created_automatic_event_within_default(self, env):
        event = make_created(env, duration=timedelta(0))
        run(env.sched.edit_event(event, duration=env.sched.DEFAULT_EVENT_DURATION))
        assert event.duration == timedelta(minutes=env.sched.DEFAULT_EVENT_DURATION)

    def test_shortening_created_event_frees_shared_availability(self, env):
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        event = make_created(env, participants=[a])
        other_a = Participant(member=a.member, availability=[TimeBlock(at(1, 18), at(1, 23))])
        env.make_event([other_a, env.make_participant("b")])
        env.sched.remove_times_from_availabilities_for_events()

        run(env.sched.edit_event(event, duration=60))
        assert event.duration == timedelta(hours=1)
        assert [(block.start_time, block.end_time) for block in other_a.availability] == \
            [(at(1, 18), at(1, 20)), (at(1, 21), at(1, 23))]

    def test_shortening_created_voice_event_moves_guild_event_end_times(self, env):
        event = make_created(env)
        run(env.sched.edit_event(event, duration=60))
        assert event.scheduled_events[0].edits == [{"end_time": at(1, 21)}]

    def test_shortening_created_external_event_moves_guild_event_end_times(self, env):
        event = make_created(env, days=(1, 2), location="Park")
        run(env.sched.edit_event(event, duration=60))
        assert [scheduled_event.edits for scheduled_event in event.scheduled_events] == \
            [[{"end_time": at(1, 21)}], [{"end_time": at(2, 21)}]]


class TestMultiEvent:
    def test_cannot_change_after_starting(self, env):
        event = env.make_event([env.make_participant("a")], started=True)
        embed = run(env.sched.edit_event(event, multi_event=True))
        assert not event.multi_event
        assert fields(embed) == [("Multi Event (Unchanged)", "Multi Event cannot be changed after starting the event")]

    def test_unchanged(self, env):
        event = env.make_event([env.make_participant("a")])
        embed = run(env.sched.edit_event(event, multi_event=False))
        assert fields(embed) == [("Multi Event (Unchanged)", "Multi Event already False")]

    @pytest.fixture
    def rescheduling(self, env, monkeypatch):
        calls = []

        async def reschedule(self, rescheduler=None):
            calls.append("reschedule")

        async def create_if_possible(self):
            calls.append("create_if_possible")

        monkeypatch.setattr(env.sched.Event, "reschedule", reschedule)
        monkeypatch.setattr(env.sched.Event, "create_if_possible", create_if_possible)
        return calls

    def test_enabling_on_created_event_reschedules(self, env, rescheduling):
        event = make_created(env)
        embed = run(env.sched.edit_event(event, multi_event=True))
        assert event.multi_event
        assert rescheduling == ["reschedule", "create_if_possible"]
        assert fields(embed) == [("Multi Event", "False -> True")]

    def test_enabling_on_uncreated_event_does_not_reschedule(self, env, rescheduling):
        event = env.make_event([env.make_participant("a")])
        run(env.sched.edit_event(event, multi_event=True))
        assert event.multi_event
        assert rescheduling == []

    def test_cannot_disable_on_created_event(self, env, rescheduling):
        event = make_created(env, days=(1, 2))
        embed = run(env.sched.edit_event(event, multi_event=False))
        assert event.multi_event
        assert rescheduling == []
        assert fields(embed) == [("Multi Event (Unchanged)", "Multi Event cannot be turned off after creating the event")]

    def test_disabling_on_uncreated_event(self, env, rescheduling):
        event = env.make_event([env.make_participant("a")], multi_event=True)
        embed = run(env.sched.edit_event(event, multi_event=False))
        assert not event.multi_event
        assert rescheduling == []
        assert fields(embed) == [("Multi Event", "True -> False")]


class TestTimeout:
    def test_unchanged(self, env):
        event = env.make_event([env.make_participant("a")], timeout_at=env.sched.now() + timedelta(days=3, seconds=30))
        timeout_at = event.timeout_at
        embed = run(env.sched.edit_event(event, timeout_days=3))
        assert event.timeout_at == timeout_at
        assert fields(embed) == [("Timeout (Unchanged)", "The new timeout is the same as the old timeout")]

    def test_restarts_timeout_from_now(self, env):
        event = env.make_event([env.make_participant("a")], timeout_at=env.sched.now() + timedelta(hours=1, seconds=30))
        embed = run(env.sched.edit_event(event, timeout_days=2))
        assert abs(event.timeout_at - (env.sched.now() + timedelta(days=2))) < timedelta(seconds=5)
        assert fields(embed) == [("Timeout", "1 hour -> 2 days")]

    def test_expired_timeout(self, env):
        event = env.make_event([env.make_participant("a")], timeout_at=env.sched.now() - timedelta(days=1))
        embed = run(env.sched.edit_event(event, timeout_days=1))
        assert fields(embed) == [("Timeout", "0 minutes -> 1 day")]


class TestEditCommand:
    def interaction(self, env):
        interaction = FakeInteraction(make_member(7, "editor"))
        interaction.guild = env.guild
        interaction.channel = env.text_channel
        return interaction

    def test_edits_the_only_event_in_the_channel(self, env):
        event = env.make_event([env.make_participant("a")])
        interaction = self.interaction(env)
        run(env.sched.edit_command.callback(interaction, duration=90))
        assert event.duration == timedelta(minutes=90)
        (followup,) = interaction.followups
        assert followup["embed"].footer.text == "Edited by " + str(interaction.user)
        assert env.update_messages_calls == [event]

    def test_error_is_answered(self, env, monkeypatch):
        env.make_event([env.make_participant("a")])

        async def failing_edit_event(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(env.sched, "edit_event", failing_edit_event)
        interaction = self.interaction(env)
        run(env.sched.edit_command.callback(interaction, duration=90))
        assert interaction.followups == [{"content": "Error editing event: boom", "ephemeral": True}]

    def test_picks_event_from_dropdown_when_channel_has_several(self, env):
        env.make_event([env.make_participant("a")])
        second = env.make_event([env.make_participant("b")])
        interaction = self.interaction(env)
        run(env.sched.edit_command.callback(interaction, duration=90))
        (select,) = interaction.followups[0]["view"].children
        select._values = [second.get_limited_name(25)]
        pick = self.interaction(env)
        run(select.callback(pick))
        assert second.duration == timedelta(minutes=90)
        assert "embed" in pick.followups[0]
