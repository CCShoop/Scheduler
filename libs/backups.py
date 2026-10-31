import os
import shutil
from datetime import datetime, timedelta

# A snapshot's folder is named after when it was taken
SNAPSHOT_FORMAT = "%Y-%m-%d_%H-%M-%S"


class Backups:
    """
    Copies data files into timestamped snapshot folders, and deletes old snapshots.

    Every snapshot from the last `keep_all_for` is kept. Older ones are thinned to the last snapshot of
    each day until `keep_daily_for`, then deleted. The newest snapshot is always kept, so unchanged
    data that's no longer snapshotted still has a backup.
    """

    def __init__(self, folder: str, filenames: list[str], interval: timedelta = timedelta(hours=1),
                 keep_all_for: timedelta = timedelta(days=2), keep_daily_for: timedelta = timedelta(days=30)):
        self.folder = folder
        self.filenames = filenames
        self.interval = interval
        self.keep_all_for = keep_all_for
        self.keep_daily_for = keep_daily_for
        self.last_backed_up_at = None

    def snapshots(self) -> list[tuple[datetime, str]]:
        """
        The existing snapshots as (time taken, path), oldest first.
        """
        if not os.path.isdir(self.folder):
            return []
        snapshots = []
        for name in os.listdir(self.folder):
            try:
                taken_at = datetime.strptime(name, SNAPSHOT_FORMAT)
            except ValueError:
                # Not a snapshot, so leave it alone
                continue
            snapshots.append((taken_at, os.path.join(self.folder, name)))
        return sorted(snapshots)

    def _unchanged_since(self, snapshot: str, existing: list[str]) -> bool:
        if sorted(os.listdir(snapshot)) != sorted(os.path.basename(filename) for filename in existing):
            return False
        for filename in existing:
            with open(filename, "rb") as current, open(os.path.join(snapshot, os.path.basename(filename)), "rb") as saved:
                if current.read() != saved.read():
                    return False
        return True

    def back_up(self, now: datetime = None) -> str | None:
        """
        Copies the data files into a new snapshot and deletes old snapshots.
        Returns the snapshot's path, or None if there was nothing new to back up.
        """
        now = now or datetime.now()
        self.last_backed_up_at = now
        existing = [filename for filename in self.filenames if os.path.isfile(filename)]
        snapshots = self.snapshots()
        if not existing or (snapshots and self._unchanged_since(snapshots[-1][1], existing)):
            self.prune(now)
            return None
        path = os.path.join(self.folder, now.strftime(SNAPSHOT_FORMAT))
        # Copy into a temporary folder and rename it, so a crash can't leave a partial snapshot
        temp_path = f"{path}.tmp"
        shutil.rmtree(temp_path, ignore_errors=True)
        os.makedirs(temp_path)
        for filename in existing:
            shutil.copy2(filename, temp_path)
        os.replace(temp_path, path)
        self.prune(now)
        return path

    def back_up_if_due(self, now: datetime = None) -> str | None:
        """
        Backs up if `interval` has passed since the last backup this run. Cheap to call often.
        """
        now = now or datetime.now()
        if self.last_backed_up_at is not None and now - self.last_backed_up_at < self.interval:
            return None
        return self.back_up(now)

    def prune(self, now: datetime = None) -> None:
        """
        Deletes snapshots that are past their retention.
        """
        now = now or datetime.now()
        snapshots = self.snapshots()
        kept_days = set()
        # Newest first, so the first snapshot seen on a day is that day's last
        for index, (taken_at, path) in enumerate(reversed(snapshots)):
            age = now - taken_at
            if index == 0 or age < self.keep_all_for:
                kept_days.add(taken_at.date())
                continue
            if age < self.keep_daily_for and taken_at.date() not in kept_days:
                kept_days.add(taken_at.date())
                continue
            shutil.rmtree(path, ignore_errors=True)
