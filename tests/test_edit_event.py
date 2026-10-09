from datetime import timedelta
from types import SimpleNamespace

import pytest
from discord import EntityType

from libs.participant import Participant, TimeBlock
from fakes import FakeScheduledEvent, at, run


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
        assert event.scheduled_events[0].edits == [{"entity_type": EntityType.voice, "channel": other_channel}]
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

    @pytest.mark.parametrize("duration", [121, 0])
    def test_cannot_extend_created_event(self, env, duration):
        event = make_created(env)
        embed = run(env.sched.edit_event(event, duration=duration))
        assert event.duration == timedelta(hours=2)
        assert fields(embed) == [("Duration (Unchanged)", "The duration of a created event cannot be extended")]

    def test_cannot_extend_created_automatic_event_past_default(self, env):
        event = make_created(env, duration=timedelta(0))
        embed = run(env.sched.edit_event(event, duration=env.sched.DEFAULT_EVENT_DURATION + 1))
        assert event.duration == timedelta(0)
        assert fields(embed) == [("Duration (Unchanged)", "The duration of a created event cannot be extended")]

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

    def test_shortening_created_voice_event_leaves_guild_events(self, env):
        event = make_created(env)
        run(env.sched.edit_event(event, duration=60))
        assert event.scheduled_events[0].edits == []

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
