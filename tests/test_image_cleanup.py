import os
import time
from types import SimpleNamespace

import pytest

from fakes import run


@pytest.fixture
def images(env, tmp_path, monkeypatch):
    """A working directory for image files, with the cleanup due to run."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env.sched, "images_cleaned_up_at", None)

    def make(file_name, age_minutes=60):
        path = tmp_path / file_name
        path.write_bytes(b"png")
        modified_at = time.time() - age_minutes * 60
        os.utime(path, (modified_at, modified_at))
        return path

    return make


class TestCleanUpImages:
    def test_deletes_images_no_event_uses(self, env, images):
        leftover = images("Old Event.png")
        env.sched.clean_up_images()
        assert not leftover.exists()

    def test_keeps_images_of_events(self, env, images):
        event = env.make_event([env.make_participant("a")])
        image = images(event.image_path)
        env.sched.clean_up_images()
        assert image.exists()

    def test_keeps_images_of_schedule_again_events(self, env, images):
        event = env.make_event([env.make_participant("a")])
        env.sched.client.events.remove(event)
        env.sched.client.schedule_again_events.append(SimpleNamespace(event=event))
        image = images(event.image_path)
        env.sched.clean_up_images()
        assert image.exists()

    def test_keeps_image_of_renamed_event(self, env, images):
        event = env.make_event([env.make_participant("a")])
        image = images(event.image_path)
        event.name = "Renamed"
        env.sched.clean_up_images()
        assert image.exists()

    def test_keeps_new_images(self, env, images):
        # /create downloads an event's image before adding the event
        downloading = images("New Event.png", age_minutes=1)
        env.sched.clean_up_images()
        assert downloading.exists()

    def test_ignores_other_files(self, env, images):
        other = images("data.json")
        env.sched.clean_up_images()
        assert other.exists()

    def test_runs_once_per_interval(self, env, images):
        env.sched.clean_up_images()
        leftover = images("Old Event.png")
        env.sched.clean_up_images()
        assert leftover.exists()
        env.sched.images_cleaned_up_at -= env.sched.IMAGE_CLEANUP_INTERVAL
        env.sched.clean_up_images()
        assert not leftover.exists()

    def test_image_is_written_only_after_download(self, env, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        event = env.make_event([env.make_participant("a")], image_url="https://example.invalid/image.png")
        seen_during_download = []

        class FakeResponse:
            status = 200

            async def read(self):
                seen_during_download.append(os.path.exists(event.image_path))
                return b"png"

        class FakeContext:
            def __init__(self, value):
                self.value = value

            async def __aenter__(self):
                return self.value

            async def __aexit__(self, *args):
                return False

        class FakeSession:
            def get(self, url):
                return FakeContext(FakeResponse())

        monkeypatch.setattr(env.sched.aiohttp, "ClientSession", lambda: FakeContext(FakeSession()))
        run(event.save_image_to_file())
        assert seen_during_download == [False]
        assert (tmp_path / event.image_path).read_bytes() == b"png"
