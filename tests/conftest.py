'''Test setup: import scheduler.py without connecting to Discord or touching real data.'''

import os
import sys
import shutil
import signal
import time
import tempfile
import importlib

import discord
import pytest
from datetime import date, datetime, timedelta
from types import SimpleNamespace

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Values the modules read from .env at import time
os.environ.setdefault("OWNER_ID", "0")
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("PORT", "0")
os.environ.setdefault("HOST", "127.0.0.1")


def _import_scheduler():
    # scheduler.py opens scheduler.log and its data files relative to the working directory,
    # installs signal handlers, and calls client.run() at import
    original_cwd = os.getcwd()
    original_run = discord.Client.run
    original_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    temp_dir = tempfile.mkdtemp(prefix="scheduler-tests-")
    os.chdir(temp_dir)
    discord.Client.run = lambda *args, **kwargs: None
    try:
        module = importlib.import_module("scheduler")
        # Persistence resolves its path at read/write time, so pin it to the temp dir
        for persist in (*module.DATA_FILES, module.legacy_persist):
            persist.filename = os.path.join(temp_dir, os.path.basename(persist.filename))
        module.backups.folder = os.path.join(temp_dir, "backups")
        module.backups.filenames = [persist.filename for persist in (*module.DATA_FILES, module.legacy_persist)]
        return module
    finally:
        discord.Client.run = original_run
        os.chdir(original_cwd)
        for sig, handler in original_handlers.items():
            signal.signal(sig, handler)


scheduler = _import_scheduler()

from libs.participant import Participant  # noqa: E402
from fakes import FakeGuild, FakeTextChannel, make_member, next_id, noop  # noqa: E402


@pytest.fixture
def sched(monkeypatch):
    """The scheduler module, with a clean client event list and empty data files for each test."""
    # Loading and saving state, restored after each test
    for flag in ("loaded_json", "events_loaded", "settings_loaded"):
        monkeypatch.setattr(scheduler.client, flag, getattr(scheduler.client, flag))
    for persist in (*scheduler.DATA_FILES, scheduler.legacy_persist):
        monkeypatch.setattr(persist, "last_written", None)
        monkeypatch.setattr(persist, "paused", False)
        if os.path.exists(persist.filename):
            os.remove(persist.filename)
    shutil.rmtree(scheduler.backups.folder, ignore_errors=True)
    monkeypatch.setattr(scheduler.backups, "last_backed_up_at", None)
    scheduler.client.events.clear()
    scheduler.client.schedule_again_events.clear()
    scheduler.participant_lib.users.clear()
    scheduler.announcer_lib.guild_engines.clear()
    yield scheduler
    scheduler.client.events.clear()
    scheduler.client.schedule_again_events.clear()
    scheduler.participant_lib.users.clear()
    scheduler.announcer_lib.guild_engines.clear()


@pytest.fixture
def env(sched, monkeypatch):
    """A guild with one text and one voice channel, and message updates stubbed out."""
    update_messages_calls = []
    update_availability_message_calls = []

    async def fake_update_messages(self):
        update_messages_calls.append(self)

    async def fake_update_availability_message(self, rescheduler=None):
        update_availability_message_calls.append(self)

    monkeypatch.setattr(sched.Event, "update_messages", fake_update_messages)
    monkeypatch.setattr(sched.Event, "update_event_buttons_message", noop)
    monkeypatch.setattr(sched.Event, "update_availability_message", fake_update_availability_message)
    monkeypatch.setattr(sched.Event, "delete_availability_message", noop)

    guild = FakeGuild()
    text_channel = FakeTextChannel()
    voice_channel = SimpleNamespace(id=2, name="General", members=[], mention="<#2>")
    guild.channels = {text_channel.id: text_channel, voice_channel.id: voice_channel}
    guild.voice_channels = [voice_channel]

    def make_participant(name, availability=()):
        member = make_member(len(guild.members) + 1, name)
        guild.members[member.id] = member
        text_channel.members.append(member)
        participant = Participant(member=member, availability=list(availability))
        participant.answered = bool(availability)
        return participant

    def make_event(participants, multi_event=False, duration=timedelta(hours=2), **kwargs):
        event = sched.Event(name=f"unit-test-{next_id()}",
                            voice_channel=kwargs.pop("voice_channel", voice_channel),
                            guild=guild,
                            text_channel=text_channel,
                            participants=participants,
                            duration=duration,
                            multi_event=multi_event,
                            **kwargs)
        sched.client.events.append(event)
        return event

    return SimpleNamespace(sched=sched, guild=guild, text_channel=text_channel, voice_channel=voice_channel,
                           make_participant=make_participant, make_event=make_event,
                           update_messages_calls=update_messages_calls,
                           update_availability_message_calls=update_availability_message_calls)


@pytest.fixture
def dst_timezone(monkeypatch):
    """
    Runs the test in a timezone with DST and returns the first date after its next DST change.
    Times built from now()'s fixed UTC offset are an hour off on that date.
    """
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    today = date.today()
    offset = datetime.now().astimezone().utcoffset()
    day = next(today + timedelta(days=days) for days in range(1, 367)
               if datetime.combine(today + timedelta(days=days), datetime.min.time()).astimezone().utcoffset() != offset)
    yield day
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def eastern(monkeypatch):
    """Runs the test with the bot's local time in Eastern, as on the server."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()
