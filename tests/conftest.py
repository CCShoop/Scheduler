'''Test setup: import scheduler.py without connecting to Discord or touching real data.'''

import os
import sys
import signal
import tempfile
import importlib

import discord
import pytest
from datetime import timedelta
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
    # scheduler.py opens scheduler.log and data.json relative to the working directory,
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
        module.persist.filename = os.path.join(temp_dir, "data.json")
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
def sched():
    """The scheduler module, with a clean client event list for each test."""
    scheduler.client.events.clear()
    scheduler.client.schedule_again_events.clear()
    scheduler.participant_lib.user_cutoffs.clear()
    yield scheduler
    scheduler.client.events.clear()
    scheduler.client.schedule_again_events.clear()
    scheduler.participant_lib.user_cutoffs.clear()


@pytest.fixture
def env(sched, monkeypatch):
    """A guild with one text and one voice channel, and message updates stubbed out."""
    update_messages_calls = []

    async def fake_update_messages(self):
        update_messages_calls.append(self)

    monkeypatch.setattr(sched.Event, "update_messages", fake_update_messages)
    monkeypatch.setattr(sched.Event, "update_event_buttons_message", noop)
    monkeypatch.setattr(sched.Event, "update_availability_message", noop)
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
                           update_messages_calls=update_messages_calls)
