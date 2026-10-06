"""Fakes standing in for discord.py objects in tests."""

import asyncio
import itertools
from datetime import datetime, timedelta
from types import SimpleNamespace

from discord import ChannelType, EntityType, EventStatus, NotFound

_ids = itertools.count(1000)


def next_id() -> int:
    return next(_ids)


def at(days: int, hour: int, minute: int = 0) -> datetime:
    """Local time `days` from today at hour:minute."""
    base = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return (base + timedelta(days=days)).replace(hour=hour, minute=minute)


class FakeScheduledEvent:
    def __init__(self, start_time, status=EventStatus.scheduled, **kwargs):
        self.id = next_id()
        self.start_time = start_time
        self.status = status
        self.deleted = False
        # Arguments the guild event was created with, and each edit's arguments
        self.kwargs = kwargs
        self.edits = []

    @property
    def name(self):
        return self.kwargs.get("name")

    @property
    def entity_type(self):
        return self.kwargs.get("entity_type", EntityType.voice)

    @property
    def channel(self):
        return self.kwargs.get("channel")

    @property
    def location(self):
        return self.kwargs.get("location")

    async def delete(self, reason=None):
        self.deleted = True

    async def edit(self, **kwargs):
        self.edits.append(kwargs)

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
        self.stage_channels = []
        self.members = {}
        # Members missing from the cache that fetch_member can still find
        self.uncached_members = {}
        # Raised by fetch_member instead of looking the member up, e.g. a Discord outage
        self.fetch_member_error = None
        self.chunked = True

    async def create_scheduled_event(self, start_time, **kwargs):
        await asyncio.sleep(0.01)
        if self.fail_on_call is not None and len(self.created) + 1 == self.fail_on_call:
            self.fail_on_call = None
            return None
        scheduled_event = FakeScheduledEvent(start_time, **kwargs)
        self.created.append(scheduled_event)
        return scheduled_event

    @property
    def scheduled_events(self):
        return [se for se in self.created if not se.deleted]

    def get_scheduled_event(self, scheduled_event_id):
        return next((se for se in self.created if se.id == scheduled_event_id and not se.deleted), None)

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def get_member(self, member_id):
        return self.members.get(member_id)

    async def fetch_member(self, member_id):
        if self.fetch_member_error is not None:
            raise self.fetch_member_error
        if member_id in self.uncached_members:
            return self.uncached_members[member_id]
        raise NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Member")


class FakeSentMessage:
    """Stands in for a discord.Message sent by the bot, recording edits."""

    def __init__(self, channel, **kwargs):
        self.id = next_id()
        self.channel = channel
        self.kwargs = kwargs
        self.edits = []
        self.deleted = False

    async def delete(self, delay=None):
        self.deleted = True

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        return self

    async def pin(self):
        pass

    async def unpin(self):
        pass


class FakeTextChannel:
    def __init__(self, channel_id=1):
        self.id = channel_id
        self.members = []
        self.mention = f"<#{channel_id}>"
        self.sent = []
        self.messages = {}

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        message = FakeSentMessage(self, **kwargs)
        self.messages[message.id] = message
        return message

    async def fetch_message(self, message_id):
        if message_id not in self.messages or self.messages[message_id].deleted:
            raise LookupError(f"Unknown message {message_id}")
        return self.messages[message_id]


class FakeStageInstance:
    def __init__(self, channel, topic, scheduled_event_id):
        self.channel = channel
        self.topic = topic
        self.scheduled_event_id = scheduled_event_id
        self.deleted = False

    async def delete(self, reason=None):
        self.deleted = True
        self.channel.instance = None


class FakeStageChannel:
    def __init__(self, channel_id=4, name="Stage", fail_create=False):
        self.id = channel_id
        self.name = name
        self.type = ChannelType.stage_voice
        self.members = []
        self.mention = f"<#{channel_id}>"
        self.instance = None
        self.fail_create = fail_create

    async def create_instance(self, topic, scheduled_event, reason=None):
        if self.fail_create:
            raise RuntimeError("Missing Permissions")
        self.instance = FakeStageInstance(self, topic, scheduled_event.id)
        scheduled_event.status = EventStatus.active
        return self.instance


async def noop(*args, **kwargs):
    pass


def make_member(member_id, name):
    return SimpleNamespace(id=member_id, name=name, nick=None, avatar=None, mention=f"<@{member_id}>")


def run(coro):
    return asyncio.run(coro)
