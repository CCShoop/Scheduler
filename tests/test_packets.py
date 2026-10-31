from types import SimpleNamespace

import pytest

from fakes import at, next_id, run


@pytest.fixture
def packet_env(env, monkeypatch):
    """
    A guild the client can find, with a bot, a person who owns the event, a Mods member, and someone else.
    Members in `hidden` can't see the voice channel.
    """
    monkeypatch.setattr(env.sched.client, "get_guild", lambda guild_id: env.guild if guild_id == env.guild.id else None)
    mods = SimpleNamespace(id=next_id(), name="Mods")
    env.guild.roles = [mods]
    bot, person, mod, other = (env.make_participant(name).member for name in ("bot", "person", "mod", "other"))
    for member in (bot, person, mod, other):
        member.bot = False
        member.roles = []
    bot.bot = True
    mod.roles = [mods]
    hidden = set()
    env.voice_channel.permissions_for = lambda member: SimpleNamespace(view_channel=member.id not in hidden)

    def packet(packet_type="schedule", **fields):
        """A packet without a scheduler, with the given fields added or replaced."""
        data = {"type": packet_type, "name": "Game Night", "guild_id": env.guild.id,
                "text_channel_id": env.text_channel.id, "voice_channel_id": None, "location": None,
                "image_url": None, "include_exclude": "INCLUDE", "usernames": None, "roles": None, "duration": 0}
        if packet_type == "schedule":
            data["multi_event"] = False
        else:
            data["start_time"] = at(1, 20).isoformat()
            data["duration"] = 60
        data.update(fields)
        return data

    def send(data):
        run(env.sched.client.handle_packet(data))
        return env.sched.client.events[-1]

    return SimpleNamespace(env=env, packet=packet, send=send, bot=bot, person=person, mod=mod, other=other,
                           hidden=hidden)


def names(event):
    return [participant.member.name for participant in event.participants]


PACKET_TYPES = pytest.mark.parametrize("packet_type", ["schedule", "create"])


class TestOwnerAsScheduler:
    """A bot sending a packet for an event someone owns, e.g. a range reservation, with them as the scheduler."""

    @PACKET_TYPES
    def test_owner_alone(self, packet_env, packet_type):
        event = packet_env.send(packet_env.packet(packet_type, scheduler_id=str(packet_env.person.id), usernames=""))
        assert names(event) == ["person"]
        assert event.scheduler.member is packet_env.person

    @PACKET_TYPES
    def test_owner_and_roles(self, packet_env, packet_type):
        event = packet_env.send(packet_env.packet(packet_type, scheduler_id=packet_env.person.id, roles="Mods"))
        assert names(event) == ["person", "mod"]
        assert event.scheduler.member is packet_env.person

    @PACKET_TYPES
    def test_owner_and_usernames(self, packet_env, packet_type):
        event = packet_env.send(packet_env.packet(packet_type, scheduler_id=packet_env.person.id,
                                                  usernames=[packet_env.other.id]))
        assert names(event) == ["person", "other"]

    @PACKET_TYPES
    def test_owner_is_invited_even_when_excluded(self, packet_env, packet_type):
        event = packet_env.send(packet_env.packet(packet_type, scheduler_id=packet_env.person.id,
                                                  usernames="person", include_exclude="EXCLUDE"))
        assert names(event) == ["person", "mod", "other"]

    @PACKET_TYPES
    def test_owner_outside_text_channel_is_rejected(self, packet_env, packet_type):
        packet_env.env.text_channel.members.remove(packet_env.person)
        with pytest.raises(Exception, match="the scheduler can't see text channel scheduling"):
            packet_env.send(packet_env.packet(packet_type, scheduler_id=packet_env.person.id, roles="Mods",
                                              location="123 Main St"))
        assert packet_env.env.sched.client.events == []

    def test_ids_as_strings(self, packet_env):
        env = packet_env.env
        event = packet_env.send(packet_env.packet(guild_id=str(env.guild.id), text_channel_id=str(env.text_channel.id),
                                                  voice_channel_id=str(env.voice_channel.id),
                                                  scheduler_id=str(packet_env.person.id)))
        assert event.voice_channel is env.voice_channel
        assert event.scheduler.member is packet_env.person


class TestNoScheduler:
    """A bot sending a packet for an event nobody in particular asked for, e.g. an episode release."""

    @PACKET_TYPES
    @pytest.mark.parametrize("scheduler", ["missing", None])
    def test_everyone(self, packet_env, packet_type, scheduler):
        data = packet_env.packet(packet_type)
        if scheduler is None:
            data["scheduler_id"] = None
        event = packet_env.send(data)
        assert names(event) == ["person", "mod", "other"]
        assert event.scheduler is None

    @PACKET_TYPES
    def test_roles(self, packet_env, packet_type):
        event = packet_env.send(packet_env.packet(packet_type, roles="Mods"))
        assert names(event) == ["mod"]
        assert event.scheduler is None

    @PACKET_TYPES
    def test_excluded_roles(self, packet_env, packet_type):
        event = packet_env.send(packet_env.packet(packet_type, roles="Mods", include_exclude="EXCLUDE"))
        assert names(event) == ["person", "other"]

    @PACKET_TYPES
    def test_empty_usernames_is_everyone(self, packet_env, packet_type):
        event = packet_env.send(packet_env.packet(packet_type, usernames=[]))
        assert names(event) == ["person", "mod", "other"]

    @PACKET_TYPES
    def test_excluding_everyone_is_an_error(self, packet_env, packet_type):
        with pytest.raises(Exception, match="No one was found to invite"):
            packet_env.send(packet_env.packet(packet_type, include_exclude="EXCLUDE", usernames="person, mod, other"))
        assert packet_env.env.sched.client.events == []

    @PACKET_TYPES
    def test_voice_channel_is_not_checked(self, packet_env, packet_type):
        packet_env.hidden.update({packet_env.person.id, packet_env.mod.id, packet_env.other.id})
        event = packet_env.send(packet_env.packet(packet_type, voice_channel_id=packet_env.env.voice_channel.id))
        assert event.voice_channel is packet_env.env.voice_channel


class TestBots:
    """Bots only take part when named in usernames, and are always available."""

    @PACKET_TYPES
    def test_bot_scheduler_is_shown_but_not_invited(self, packet_env, packet_type):
        event = packet_env.send(packet_env.packet(packet_type, scheduler_id=packet_env.bot.id,
                                                  usernames=str(packet_env.person.id), roles="Mods"))
        assert names(event) == ["person", "mod"]
        assert event.scheduler.member is packet_env.bot
        assert event.scheduler not in event.participants

    @PACKET_TYPES
    def test_bot_scheduler_named_in_usernames_is_invited(self, packet_env, packet_type):
        event = packet_env.send(packet_env.packet(packet_type, scheduler_id=packet_env.bot.id,
                                                  usernames=[packet_env.bot.id, packet_env.person.id]))
        assert names(event) == ["bot", "person"]
        assert event.scheduler.member is packet_env.bot

    @PACKET_TYPES
    def test_bot_scheduler_with_empty_usernames_is_everyone(self, packet_env, packet_type):
        event = packet_env.send(packet_env.packet(packet_type, scheduler_id=packet_env.bot.id, usernames=""))
        assert names(event) == ["person", "mod", "other"]

    @PACKET_TYPES
    def test_named_bot_is_invited(self, packet_env, packet_type):
        event = packet_env.send(packet_env.packet(packet_type, scheduler_id=packet_env.person.id,
                                                  usernames=[packet_env.bot.id]))
        assert names(event) == ["person", "bot"]

    @PACKET_TYPES
    def test_everyone_and_roles_skip_bots(self, packet_env, packet_type):
        packet_env.bot.roles = list(packet_env.mod.roles)
        assert names(packet_env.send(packet_env.packet(packet_type, name="a"))) == ["person", "mod", "other"]
        assert names(packet_env.send(packet_env.packet(packet_type, name="b", roles="Mods",
                                                       location="123 Main St"))) == ["mod"]

    @PACKET_TYPES
    def test_only_bots_is_rejected(self, packet_env, packet_type):
        with pytest.raises(Exception, match="at least one person, not just bots"):
            packet_env.send(packet_env.packet(packet_type, usernames=[packet_env.bot.id]))
        assert packet_env.env.sched.client.events == []


class TestSchedulerVoiceChannel:
    """A scheduler who can't see the event's voice channel can't put an event in it."""

    def rejected(self, packet_env, **fields):
        packet_env.hidden.add(packet_env.person.id)
        with pytest.raises(Exception, match="the scheduler can't see voice channel General"):
            packet_env.send(packet_env.packet(scheduler_id=packet_env.person.id, **fields))
        assert packet_env.env.sched.client.events == []

    def test_voice_channel_id(self, packet_env):
        self.rejected(packet_env, voice_channel_id=packet_env.env.voice_channel.id)

    def test_location_naming_the_voice_channel(self, packet_env):
        self.rejected(packet_env, location="General")

    def test_default_voice_channel(self, packet_env):
        self.rejected(packet_env)

    def test_create(self, packet_env):
        packet_env.hidden.add(packet_env.person.id)
        with pytest.raises(Exception, match="the scheduler can't see voice channel"):
            packet_env.send(packet_env.packet("create", scheduler_id=packet_env.person.id))

    def test_bot_scheduler_that_is_not_a_participant_is_not_checked(self, packet_env):
        packet_env.hidden.add(packet_env.bot.id)
        event = packet_env.send(packet_env.packet(scheduler_id=packet_env.bot.id))
        assert event.scheduler.member is packet_env.bot

    @pytest.mark.parametrize("usernames", ["BOT_ID", "bot, person", "BOT_ID_LIST"])
    def test_bot_scheduler_named_in_usernames_is_checked(self, packet_env, usernames):
        usernames = {"BOT_ID": packet_env.bot.id, "BOT_ID_LIST": [str(packet_env.bot.id)]}.get(usernames, usernames)
        packet_env.hidden.add(packet_env.bot.id)
        with pytest.raises(Exception, match="the scheduler can't see voice channel"):
            packet_env.send(packet_env.packet(scheduler_id=packet_env.bot.id, usernames=usernames))

    def test_bot_scheduler_must_see_the_text_channel(self, packet_env):
        packet_env.env.text_channel.members.remove(packet_env.bot)
        with pytest.raises(Exception, match="the scheduler can't see text channel"):
            packet_env.send(packet_env.packet(scheduler_id=packet_env.bot.id))

    def test_external_location_is_not_checked(self, packet_env):
        packet_env.hidden.add(packet_env.person.id)
        event = packet_env.send(packet_env.packet(scheduler_id=packet_env.person.id, location="123 Main St"))
        assert event.location == "123 Main St"

    def test_scheduler_missing_from_cache_is_fetched(self, packet_env):
        env = packet_env.env
        del env.guild.members[packet_env.person.id]
        env.guild.uncached_members[packet_env.person.id] = packet_env.person
        packet_env.hidden.add(packet_env.person.id)
        with pytest.raises(Exception, match="the scheduler can't see voice channel"):
            packet_env.send(packet_env.packet(scheduler_id=packet_env.person.id))

    def test_unknown_scheduler(self, packet_env):
        with pytest.raises(Exception, match="scheduler 999 not found in Test Server"):
            packet_env.send(packet_env.packet(scheduler_id=999))
        assert packet_env.env.sched.client.events == []


class TestPacketErrors:
    def error(self, packet_env, data, match):
        with pytest.raises(Exception, match=match):
            run(packet_env.env.sched.client.handle_packet(data))
        assert packet_env.env.sched.client.events == []

    @pytest.mark.parametrize("data", [[], "schedule", 5])
    def test_not_an_object(self, packet_env, data):
        self.error(packet_env, data, "must be a JSON object")

    def test_missing_fields_are_all_named(self, packet_env):
        data = packet_env.packet()
        del data["usernames"], data["multi_event"]
        self.error(packet_env, data, "missing field\\(s\\): usernames, multi_event$")

    def test_create_rejects_zero_duration(self, packet_env):
        self.error(packet_env, packet_env.packet("create", duration=0), "Duration must be 1 to 1440 minutes")

    def test_create_needs_start_time(self, packet_env):
        data = packet_env.packet("create")
        del data["start_time"]
        self.error(packet_env, data, "missing field\\(s\\): start_time$")

    def test_unknown_server(self, packet_env):
        self.error(packet_env, packet_env.packet(guild_id=999), "server 999 not found")

    def test_unknown_text_channel(self, packet_env):
        self.error(packet_env, packet_env.packet(text_channel_id=999), "text channel 999 not found")

    def test_voice_channel_as_text_channel(self, packet_env):
        voice_channel_id = packet_env.env.voice_channel.id
        self.error(packet_env, packet_env.packet(text_channel_id=voice_channel_id), "is not a text channel")

    def test_unknown_voice_channel(self, packet_env):
        self.error(packet_env, packet_env.packet(voice_channel_id=999), "voice channel 999 not found")

    def test_location_and_voice_channel(self, packet_env):
        data = packet_env.packet(location="123 Main St", voice_channel_id=packet_env.env.voice_channel.id)
        self.error(packet_env, data, 'set "location" or "voice_channel_id", not both')

    def test_location_too_long(self, packet_env):
        self.error(packet_env, packet_env.packet(location="x" * 101), "Location must be 100 characters or fewer")

    def test_name_too_long(self, packet_env):
        self.error(packet_env, packet_env.packet(name="x" * 101), "Event name must be")

    def test_duration_out_of_range(self, packet_env):
        self.error(packet_env, packet_env.packet(duration=-5), "Duration must be")

    @pytest.mark.parametrize("field, value, match", [
        ("name", 5, '"name" must be a string'),
        ("guild_id", "abc", '"guild_id" must be an id'),
        ("guild_id", True, '"guild_id" must be an id'),
        ("text_channel_id", 1.5, '"text_channel_id" must be an id'),
        ("voice_channel_id", "General", '"voice_channel_id" must be an id'),
        ("scheduler_id", "abc", '"scheduler_id" must be an id'),
        ("location", 5, '"location" must be a string or null'),
        ("image_url", 5, '"image_url" must be a string or null'),
        ("include_exclude", 1, '"include_exclude" must be'),
        ("include_exclude", "both", "include_exclude must be INCLUDE or EXCLUDE"),
        ("usernames", {"a": 1}, '"usernames" must be null'),
        ("usernames", [True], '"usernames" must be null'),
        ("usernames", True, '"usernames" must be null'),
        ("roles", [None], '"roles" must be null'),
        ("duration", "60", '"duration" must be a whole number'),
        ("duration", True, '"duration" must be a whole number'),
        ("multi_event", "false", '"multi_event" must be true or false'),
    ])
    def test_bad_values(self, packet_env, field, value, match):
        self.error(packet_env, packet_env.packet(**{field: value}), match)

    def test_bad_start_time(self, packet_env):
        self.error(packet_env, packet_env.packet("create", start_time=1900), '"start_time" must be a string')


class TestPacketLocation:
    def test_blank_location_uses_first_voice_channel(self, packet_env):
        event = packet_env.send(packet_env.packet(location="  "))
        assert event.location is None and event.voice_channel is packet_env.env.voice_channel

    def test_location_naming_a_voice_channel_uses_it(self, packet_env):
        event = packet_env.send(packet_env.packet(location="general"))
        assert event.location is None and event.voice_channel is packet_env.env.voice_channel

    def test_external_location(self, packet_env):
        event = packet_env.send(packet_env.packet(location=" 123 Main St "))
        assert event.location == "123 Main St" and event.voice_channel is None
