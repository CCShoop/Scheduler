from types import SimpleNamespace

import pytest

from fakes import FakeGuild, FakeTextChannel, at, next_id, run


def make_member(name, roles=(), bot=False):
    member_id = next_id()
    return SimpleNamespace(id=member_id, name=name, nick=None, avatar=None, mention=f"<@{member_id}>",
                           bot=bot, roles=list(roles))


@pytest.fixture
def channel_env(sched):
    """A guild whose text channel has a scheduler, two members with roles, one without, and a bot."""
    gamers = SimpleNamespace(name="Gamers")
    mods = SimpleNamespace(name="Mods")
    guild = FakeGuild()
    guild.roles = [gamers, mods]
    channel = FakeTextChannel()
    scheduler_member = make_member("host", roles=[gamers])
    alice = make_member("alice", roles=[gamers])
    bob = make_member("bob", roles=[mods])
    carol = make_member("carol")
    bot = make_member("botty", roles=[gamers, mods], bot=True)
    for member in (scheduler_member, alice, bob, carol, bot):
        guild.members[member.id] = member
        channel.members.append(member)

    def get(**kwargs):
        kwargs.setdefault("user", scheduler_member)
        participants = sched.get_participants_from_channel(event_name="test", guild=guild, channel=channel, **kwargs)
        return [participant.member.name for participant in participants]

    return SimpleNamespace(sched=sched, guild=guild, channel=channel, get=get, scheduler=scheduler_member,
                           alice=alice, bob=bob, carol=carol, bot=bot)


class TestEveryone:
    def test_adds_scheduler_first_then_everyone_but_bots(self, channel_env):
        assert channel_env.get() == ["host", "alice", "bob", "carol"]

    def test_without_scheduler(self, channel_env):
        assert channel_env.get(user=None) == ["host", "alice", "bob", "carol"]

    def test_bot_scheduler_is_not_added(self, channel_env):
        assert channel_env.get(user=channel_env.bot) == ["host", "alice", "bob", "carol"]

    def test_scheduler_missing_from_member_cache(self, channel_env):
        del channel_env.guild.members[channel_env.scheduler.id]
        assert channel_env.get() == ["host", "alice", "bob", "carol"]

    def test_scheduler_outside_channel_is_still_added(self, channel_env):
        outsider = make_member("outsider")
        channel_env.guild.members[outsider.id] = outsider
        assert channel_env.get(user=outsider) == ["outsider", "host", "alice", "bob", "carol"]

    def test_empty_channel(self, channel_env):
        channel_env.channel.members.clear()
        assert channel_env.get() == ["host"]

    def test_empty_usernames_and_roles_add_everyone(self, channel_env):
        assert channel_env.get(usernames=[], roles="") == ["host", "alice", "bob", "carol"]


class TestUsernames:
    def test_include_by_name(self, channel_env):
        assert channel_env.get(usernames="alice,carol") == ["host", "alice", "carol"]

    def test_include_by_id(self, channel_env):
        assert channel_env.get(usernames=str(channel_env.bob.id)) == ["host", "bob"]

    def test_strips_whitespace(self, channel_env):
        assert channel_env.get(usernames="  alice ,   bob  ") == ["host", "alice", "bob"]

    def test_accepts_a_list(self, channel_env):
        assert channel_env.get(usernames=["alice", str(channel_env.carol.id)]) == ["host", "alice", "carol"]

    def test_exclude(self, channel_env):
        assert channel_env.get(usernames="alice", include_exclude=channel_env.sched.EXCLUDE) == ["host", "bob", "carol"]

    def test_exclude_by_id(self, channel_env):
        assert channel_env.get(usernames=str(channel_env.alice.id),
                               include_exclude=channel_env.sched.EXCLUDE) == ["host", "bob", "carol"]

    def test_listing_scheduler_does_not_add_them_twice(self, channel_env):
        assert channel_env.get(usernames="host,alice") == ["host", "alice"]

    def test_excluding_scheduler_still_adds_them(self, channel_env):
        assert channel_env.get(usernames="host", include_exclude=channel_env.sched.EXCLUDE) == \
            ["host", "alice", "bob", "carol"]

    def test_listing_bot_does_not_add_it(self, channel_env):
        assert channel_env.get(usernames="botty") == ["host"]

    def test_duplicates_add_member_once(self, channel_env):
        assert channel_env.get(usernames="alice,alice") == ["host", "alice"]

    def test_unknown_names_add_only_scheduler(self, channel_env):
        assert channel_env.get(usernames="nobody") == ["host"]

    def test_names_are_case_sensitive(self, channel_env):
        assert channel_env.get(usernames="ALICE") == ["host"]

    def test_without_scheduler(self, channel_env):
        assert channel_env.get(user=None, usernames="host,alice") == ["host", "alice"]

    def test_rejects_other_types(self, channel_env):
        with pytest.raises(Exception, match="incompatible usernames variable type"):
            channel_env.get(usernames=42)

    def test_rejects_non_string_entries(self, channel_env):
        with pytest.raises(Exception, match="Failed to parse username"):
            channel_env.get(usernames=[42])


class TestRoles:
    def test_include(self, channel_env):
        assert channel_env.get(roles="Gamers") == ["host", "alice"]

    def test_include_several(self, channel_env):
        assert channel_env.get(roles="Gamers, Mods") == ["host", "alice", "bob"]

    def test_role_names_are_case_insensitive(self, channel_env):
        assert channel_env.get(roles="  gAmErS ") == ["host", "alice"]

    def test_exclude(self, channel_env):
        assert channel_env.get(roles="Gamers", include_exclude=channel_env.sched.EXCLUDE) == ["host", "bob", "carol"]

    @pytest.mark.parametrize("include_exclude", ["INCLUDE", "EXCLUDE"])
    def test_unknown_role_is_an_error(self, channel_env, include_exclude):
        with pytest.raises(Exception, match="Role\\(s\\) not found: Nonexistent$"):
            channel_env.get(roles="Nonexistent", include_exclude=include_exclude)

    def test_error_names_only_unknown_roles(self, channel_env):
        with pytest.raises(Exception, match="Role\\(s\\) not found: Nope, Missing$"):
            channel_env.get(roles="Gamers, Nope, Mods, Missing")

    def test_blank_role_names_are_ignored(self, channel_env):
        assert channel_env.get(roles="Gamers, ,") == ["host", "alice"]

    def test_only_blank_role_names_is_an_error(self, channel_env):
        with pytest.raises(Exception, match="No role names were given"):
            channel_env.get(roles=" , ")

    def test_roles_take_priority_over_usernames(self, channel_env):
        assert channel_env.get(roles="Mods", usernames="alice") == ["host", "bob"]

    def test_bots_with_role_are_skipped(self, channel_env):
        assert "botty" not in channel_env.get(roles="Gamers,Mods")

    def test_without_scheduler(self, channel_env):
        assert channel_env.get(user=None, roles="Gamers") == ["host", "alice"]

    def test_rejects_unparseable_roles(self, channel_env):
        with pytest.raises(Exception, match="Failed to parse role"):
            channel_env.get(roles=["Gamers"])


class TestCreateWithUsernameList:
    @pytest.fixture
    def members(self, env):
        a, b = env.make_participant("a"), env.make_participant("b")
        for member in env.text_channel.members:
            member.bot = False
        return a.member, b.member

    @pytest.mark.parametrize("to_entry", [lambda member: member.id, lambda member: str(member.id),
                                          lambda member: member.name])
    def test_create(self, env, members, to_entry):
        a, b = members
        event = run(env.sched.create(event_name="Game Night", guild=env.guild, text_channel=env.text_channel,
                                     voice_channel=env.voice_channel, start_time=at(1, 20),
                                     scheduler_id=a.id, usernames=[to_entry(b)]))
        assert [participant.member for participant in event.participants] == [a, b]

    @pytest.mark.parametrize("to_entry", [lambda member: member.id, lambda member: member.name])
    def test_schedule(self, env, members, to_entry):
        a, b = members
        event = run(env.sched.schedule(event_name="Game Night", guild=env.guild, text_channel=env.text_channel,
                                       voice_channel=env.voice_channel, scheduler_id=a.id, usernames=[to_entry(b)]))
        assert [participant.member for participant in event.participants] == [a, b]
