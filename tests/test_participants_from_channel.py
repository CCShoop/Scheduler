import asyncio
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
    gamers = SimpleNamespace(id=next_id(), name="Gamers")
    mods = SimpleNamespace(id=next_id(), name="Mods")
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

    def test_role_ids(self, channel_env):
        gamers, mods = channel_env.guild.roles
        assert channel_env.get(roles=f"{gamers.id},{mods.id}") == ["host", "alice", "bob"]

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


class TestPromptForParticipants:
    @staticmethod
    def make_role(name, position, default=False, managed=False):
        return SimpleNamespace(id=next_id(), name=name, position=position, managed=managed, is_default=lambda: default)

    @staticmethod
    def make_member(name, roles=(), bot=False):
        return SimpleNamespace(id=next_id(), name=name, display_name=name.title(), bot=bot, roles=list(roles))

    def prompt(self, sched, members, user, choose=None):
        """Runs the prompt, choosing from its view with `choose(view)` once it's sent, and returns (result, view)."""
        sent = {}

        async def send(view, **kwargs):
            sent["view"] = view
            if choose is not None:
                asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(choose(view)))
            return SimpleNamespace(delete=lambda: asyncio.sleep(0))

        interaction = SimpleNamespace(channel=SimpleNamespace(members=members), user=user,
                                      followup=SimpleNamespace(send=send))
        return run(sched.prompt_for_participants(interaction, sched.INCLUDE)), sent.get("view")

    @staticmethod
    def selection_interaction():
        return SimpleNamespace(response=SimpleNamespace(defer=lambda: asyncio.sleep(0)),
                               delete_original_response=lambda: asyncio.sleep(0))

    def test_lists_members_except_bots_and_user_and_their_roles(self, sched):
        everyone = self.make_role("@everyone", 0, default=True)
        managed = self.make_role("Bot Role", 1, managed=True)
        low, high = self.make_role("Low", 2), self.make_role("High", 3)
        host = self.make_member("host", roles=[everyone, high])
        alice = self.make_member("alice", roles=[everyone, low])
        bob = self.make_member("bob", roles=[everyone, low, high])
        botty = self.make_member("botty", roles=[everyone, managed], bot=True)

        async def choose(view):
            view.users_select._values = [str(alice.id)]
            await view.users_select_callback(self.selection_interaction())

        result, view = self.prompt(sched, [host, alice, bob, botty], host, choose)
        assert [option.value for option in view.users_select.options] == [str(alice.id), str(bob.id)]
        assert [option.label for option in view.roles_select.options] == ["High", "Low"]
        assert result == (str(alice.id), None)

    def test_choosing_roles(self, sched):
        gamers, mods = self.make_role("Gamers", 1), self.make_role("Mods", 2)
        host = self.make_member("host")
        alice, bob = self.make_member("alice", roles=[gamers]), self.make_member("bob", roles=[mods])

        async def choose(view):
            view.roles_select._values = [str(gamers.id), str(mods.id)]
            await view.roles_select_callback(self.selection_interaction())

        result, _ = self.prompt(sched, [host, alice, bob], host, choose)
        assert result == (None, f"{gamers.id},{mods.id}")

    def test_only_the_first_selection_counts(self, sched):
        gamers = self.make_role("Gamers", 1)
        host, alice = self.make_member("host"), self.make_member("alice", roles=[gamers])
        deletes = []

        def selection_interaction():
            interaction = self.selection_interaction()
            interaction.delete_original_response = lambda: asyncio.sleep(0, deletes.append(1))
            return interaction

        async def choose(view):
            view.users_select._values = [str(alice.id)]
            view.roles_select._values = [str(gamers.id)]
            await asyncio.gather(view.users_select_callback(selection_interaction()),
                                 view.roles_select_callback(selection_interaction()))

        result, _ = self.prompt(sched, [host, alice], host, choose)
        assert result == (str(alice.id), None)
        assert len(deletes) == 1

    def test_no_roles_dropdown_without_roles(self, sched):
        host, alice = self.make_member("host"), self.make_member("alice")

        async def choose(view):
            view.users_select._values = [str(alice.id)]
            await view.users_select_callback(self.selection_interaction())

        _, view = self.prompt(sched, [host, alice], host, choose)
        assert len(view.children) == 1

    def test_large_channels_get_searchable_dropdowns(self, sched):
        roles = [self.make_role(f"Role {i}", i) for i in range(30)]
        host = self.make_member("host")
        members = [self.make_member(f"user{i}", roles=[roles[i]]) for i in range(30)]

        async def choose(view):
            view.users_select._values = []
            await view.users_select_callback(self.selection_interaction())

        _, view = self.prompt(sched, [host] + members, host, choose)
        assert type(view.users_select).__name__ == "UserSelect"
        assert type(view.roles_select).__name__ == "RoleSelect"

    def test_alone_in_channel_skips_the_prompt(self, sched):
        host = self.make_member("host")
        assert self.prompt(sched, [host], host) == ((None, None), None)
