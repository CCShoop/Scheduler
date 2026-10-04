"""Fakes standing in for discord.py objects in tests."""

import asyncio
import itertools
from datetime import datetime, timedelta
from types import SimpleNamespace

from discord import EventStatus

_ids = itertools.count(1000)


def next_id() -> int:
    return next(_ids)


def at(days: int, hour: int, minute: int = 0) -> datetime:
    """Local time `days` from today at hour:minute."""
    base = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return (base + timedelta(days=days)).replace(hour=hour, minute=minute)


class FakeScheduledEvent:
    def __init__(self, start_time, status=EventStatus.scheduled):
        self.id = next_id()
        self.start_time = start_time
        self.status = status
        self.deleted = False

    async def delete(self, reason=None):
        self.deleted = True

    async def edit(self, **kwargs):
        pass

    async def start(self, reason=None):
        self.status = EventStatus.active

    def __repr__(self):
        return f"FakeScheduledEvent({self.start_time:%m/%d %H:%M})"


class FakeGuild:
    """Stands in for discord.Guild. create_scheduled_event yields to the loop like a real HTTP call."""

    def __init__(self, fail_on_call=None):
        self.id = 5
        self.created = []
        self.fail_on_call = fail_on_call
        self.channels = {}
        self.voice_channels = []
        self.members = {}

    async def create_scheduled_event(self, start_time, **kwargs):
        await asyncio.sleep(0.01)
        if self.fail_on_call is not None and len(self.created) + 1 == self.fail_on_call:
            self.fail_on_call = None
            return None
        scheduled_event = FakeScheduledEvent(start_time)
        self.created.append(scheduled_event)
        return scheduled_event

    def get_scheduled_event(self, scheduled_event_id):
        return next((se for se in self.created if se.id == scheduled_event_id and not se.deleted), None)

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def get_member(self, member_id):
        return self.members.get(member_id)


class FakeTextChannel:
    def __init__(self, channel_id=1):
        self.id = channel_id
        self.members = []
        self.mention = f"<#{channel_id}>"
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=next_id(), delete=noop, edit=noop, pin=noop, unpin=noop)


async def noop(*args, **kwargs):
    pass


def make_member(member_id, name):
    return SimpleNamespace(id=member_id, name=name, nick=None, avatar=None, mention=f"<@{member_id}>")


def run(coro):
    return asyncio.run(coro)
