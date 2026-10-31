import os
from datetime import datetime, timedelta

import pytest

from libs.backups import Backups, SNAPSHOT_FORMAT
from fakes import read_json, write_json

LEGACY = {
    "events": [{"name": "event"}],
    "schedule_again_events": [{"name": "again"}],
    "users": [{"id": 7, "name": "user", "cutoff": 3}],
    "tts_engines": {"5": "espeak-ng"},
}


class TestMigration:
    def test_splits_the_old_data_file(self, sched):
        write_json(sched.legacy_persist.filename, LEGACY)
        sched.migrate_legacy_data()
        assert read_json(sched.event_persist.filename) == {"events": [{"name": "event"}], "schedule_again_events": [{"name": "again"}]}
        assert read_json(sched.user_persist.filename) == {"users": [{"id": 7, "name": "user", "cutoff": 3}]}
        assert read_json(sched.guild_persist.filename) == {"5": {"tts_engine": "espeak-ng"}}

    def test_deletes_the_old_file(self, sched):
        write_json(sched.legacy_persist.filename, LEGACY)
        sched.migrate_legacy_data()
        assert not os.path.exists(sched.legacy_persist.filename)

    def test_old_file_without_newer_sections(self, sched):
        write_json(sched.legacy_persist.filename, {"events": []})
        sched.migrate_legacy_data()
        assert read_json(sched.user_persist.filename) == {"users": []}
        assert read_json(sched.guild_persist.filename) == {}

    def test_ignored_once_split(self, sched):
        write_json(sched.legacy_persist.filename, LEGACY)
        write_json(sched.user_persist.filename, {"users": []})
        sched.migrate_legacy_data()
        assert read_json(sched.legacy_persist.filename) == LEGACY
        assert read_json(sched.user_persist.filename) == {"users": []}
        assert not os.path.exists(sched.event_persist.filename)

    def test_unreadable_old_file_is_moved_aside(self, sched, tmp_path):
        with open(sched.legacy_persist.filename, "w") as file:
            file.write("{not json")
        sched.migrate_legacy_data()
        assert not os.path.exists(sched.legacy_persist.filename)
        assert not os.path.exists(sched.event_persist.filename)
        folder = os.path.dirname(sched.legacy_persist.filename)
        assert any(name.startswith("data.json.corrupt-") for name in os.listdir(folder))

    def test_settings_are_loaded_from_the_old_file(self, sched, monkeypatch):
        monkeypatch.setattr(sched.client, "settings_loaded", False)
        write_json(sched.legacy_persist.filename, LEGACY)
        sched.load_settings()
        assert [user.cutoff for user in sched.participant_lib.users] == [3]
        assert sched.announcer_lib.guild_engines == {5: "espeak-ng"}


START = datetime(2026, 10, 31, 12, 0)


@pytest.fixture
def files(tmp_path):
    """Two data files, and backups of them in a backups folder."""
    paths = [str(tmp_path / "a.json"), str(tmp_path / "b.json")]
    for path in paths:
        write_json(path, {"version": 1})
    return paths, Backups(str(tmp_path / "backups"), paths)


def snapshot_times(backups):
    return [taken_at for taken_at, _ in backups.snapshots()]


class TestBackups:
    def test_copies_the_files_into_a_snapshot(self, files):
        paths, backups = files
        snapshot = backups.back_up(START)
        assert os.path.basename(snapshot) == START.strftime(SNAPSHOT_FORMAT)
        assert sorted(os.listdir(snapshot)) == ["a.json", "b.json"]
        assert read_json(os.path.join(snapshot, "a.json")) == {"version": 1}

    def test_skips_missing_files(self, files):
        paths, backups = files
        os.remove(paths[1])
        assert os.listdir(backups.back_up(START)) == ["a.json"]

    def test_nothing_to_back_up(self, tmp_path):
        backups = Backups(str(tmp_path / "backups"), [str(tmp_path / "missing.json")])
        assert backups.back_up(START) is None
        assert backups.snapshots() == []

    def test_unchanged_files_are_not_backed_up_again(self, files):
        paths, backups = files
        backups.back_up(START)
        assert backups.back_up(START + timedelta(hours=1)) is None
        write_json(paths[0], {"version": 2})
        assert backups.back_up(START + timedelta(hours=2)) is not None
        assert snapshot_times(backups) == [START, START + timedelta(hours=2)]

    def test_a_deleted_file_counts_as_a_change(self, files):
        paths, backups = files
        backups.back_up(START)
        os.remove(paths[1])
        assert backups.back_up(START + timedelta(hours=1)) is not None

    def test_backs_up_once_per_interval(self, files):
        paths, backups = files
        assert backups.back_up_if_due(START) is not None
        write_json(paths[0], {"version": 2})
        assert backups.back_up_if_due(START + timedelta(minutes=59)) is None
        assert backups.back_up_if_due(START + timedelta(hours=1)) is not None

    def test_ignores_other_folders(self, files):
        paths, backups = files
        os.makedirs(os.path.join(backups.folder, "notes"))
        backups.back_up(START)
        backups.prune(START + timedelta(days=365))
        assert "notes" in os.listdir(backups.folder)


class TestRetention:
    def take_hourly(self, files, hours):
        paths, backups = files
        for hour in range(hours):
            write_json(paths[0], {"version": hour})
            backups.back_up(START + timedelta(hours=hour))
        return START + timedelta(hours=hours - 1)

    def test_keeps_every_snapshot_for_2_days(self, files):
        now = self.take_hourly(files, 48)
        assert len(snapshot_times(files[1])) == 48

    def test_keeps_the_last_snapshot_of_each_older_day(self, files):
        _, backups = files
        now = self.take_hourly(files, 24 * 5)
        times = snapshot_times(backups)
        recent = [taken_at for taken_at in times if now - taken_at < timedelta(days=2)]
        older = [taken_at for taken_at in times if now - taken_at >= timedelta(days=2)]
        assert len(recent) == 48
        # One per older day, the last one taken that day
        assert len({taken_at.date() for taken_at in older}) == len(older)
        assert all(taken_at.hour == 23 for taken_at in older)

    def test_deletes_snapshots_older_than_30_days(self, files):
        paths, backups = files
        backups.back_up(START)
        write_json(paths[0], {"version": 2})
        backups.back_up(START + timedelta(days=31))
        assert snapshot_times(backups) == [START + timedelta(days=31)]

    def test_always_keeps_the_newest_snapshot(self, files):
        paths, backups = files
        backups.back_up(START)
        # Nothing changed for months, so nothing new was backed up
        assert backups.back_up(START + timedelta(days=90)) is None
        assert snapshot_times(backups) == [START]


class TestSchedulerBackups:
    def test_backs_up_on_startup_before_migrating(self, sched, monkeypatch):
        monkeypatch.setattr(sched.client, "settings_loaded", False)
        write_json(sched.legacy_persist.filename, LEGACY)
        sched.load_settings()
        ((_, snapshot),) = sched.backups.snapshots()
        assert read_json(os.path.join(snapshot, "data.json")) == LEGACY

    def test_backup_failure_doesnt_stop_loading(self, sched, monkeypatch):
        monkeypatch.setattr(sched.client, "settings_loaded", False)

        def fail(now=None):
            raise OSError("disk full")

        monkeypatch.setattr(sched.backups, "back_up", fail)
        write_json(sched.user_persist.filename, {"users": [{"id": 7, "name": "user", "cutoff": 3}]})
        sched.load_settings()
        assert [user.cutoff for user in sched.participant_lib.users] == [3]
