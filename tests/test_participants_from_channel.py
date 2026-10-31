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

    def test_bot_scheduler_named_in_usernames_is_added(self, channel_env):
        assert channel_env.get(user=channel_env.bot, usernames="botty, alice") == ["alice", "botty"]
        assert channel_env.get(user=channel_env.bot, usernames="botty", roles="Mods") == ["botty", "bob"]

    def test_this_bot_is_never_added(self, channel_env, monkeypatch):
        monkeypatch.setattr(channel_env.sched, "is_this_bot", lambda member: member is channel_env.bot)
        assert channel_env.get(usernames="botty, alice") == ["host", "alice"]
        assert channel_env.get(usernames="carol", roles="Mods") == ["host", "carol", "bob"]

    def test_scheduler_missing_from_member_cache(self, channel_env):
        del channel_env.guild.members[channel_env.scheduler.id]
        assert channel_env.get() == ["host", "alice", "bob", "carol"]

    def test_scheduler_outside_channel_is_not_added(self, channel_env):
        outsider = make_member("outsider")
        channel_env.guild.members[outsider.id] = outsider
        assert channel_env.get(user=outsider) == ["host", "alice", "bob", "carol"]

    def test_scheduler_with_permission_but_not_in_channel_members_is_added(self, channel_env):
        # channel.members leaves out members missing from the member cache
        channel_env.channel.members.remove(channel_env.scheduler)
        channel_env.channel.permissions_for = lambda member: SimpleNamespace(view_channel=True)
        assert channel_env.get() == ["host", "alice", "bob", "carol"]

    def test_channel_with_only_the_scheduler(self, channel_env):
        channel_env.channel.members[:] = [channel_env.scheduler]
        assert channel_env.get() == ["host"]

    def test_null_usernames_and_roles_add_everyone(self, channel_env):
        assert channel_env.get(usernames=None, roles=None) == ["host", "alice", "bob", "carol"]


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

    def test_listing_bot_adds_it(self, channel_env):
        assert channel_env.get(usernames="botty") == ["host", "botty"]
        assert channel_env.get(usernames=[channel_env.bot.id]) == ["host", "botty"]

    def test_excluding_does_not_add_bots(self, channel_env):
        assert channel_env.get(usernames="alice", include_exclude=channel_env.sched.EXCLUDE) == ["host", "bob", "carol"]

    def test_duplicates_add_member_once(self, channel_env):
        assert channel_env.get(usernames="alice,alice") == ["host", "alice"]

    def test_unknown_names_add_only_scheduler(self, channel_env):
        assert channel_env.get(usernames="nobody") == ["host"]

    def test_names_are_case_sensitive(self, channel_env):
        assert channel_env.get(usernames="ALICE") == ["host"]

    def test_without_scheduler(self, channel_env):
        assert channel_env.get(user=None, usernames="host,alice") == ["host", "alice"]

    def test_members_outside_channel_are_skipped(self, channel_env):
        outsider = make_member("outsider")
        channel_env.guild.members[outsider.id] = outsider
        assert channel_env.get(usernames=f"alice,outsider,{outsider.id}") == ["host", "alice"]

    def test_accepts_a_single_id(self, channel_env):
        assert channel_env.get(usernames=channel_env.bob.id) == ["host", "bob"]

    def test_accepts_ids_as_numbers(self, channel_env):
        assert channel_env.get(usernames=[channel_env.bob.id, "carol"]) == ["host", "bob", "carol"]

    def test_blank_entries_are_ignored(self, channel_env):
        assert channel_env.get(usernames="alice, ,") == ["host", "alice"]

    @pytest.mark.parametrize("usernames", ["", " , ", [], [" "]])
    def test_blank_include_with_human_scheduler_adds_only_them(self, channel_env, usernames):
        assert channel_env.get(usernames=usernames) == ["host"]

    @pytest.mark.parametrize("usernames", ["", " , ", [], [" "]])
    def test_blank_include_with_bot_scheduler_adds_everyone(self, channel_env, usernames):
        assert channel_env.get(user=channel_env.bot, usernames=usernames) == ["host", "alice", "bob", "carol"]

    @pytest.mark.parametrize("usernames", ["", []])
    def test_blank_include_with_scheduler_outside_channel_adds_everyone(self, channel_env, usernames):
        outsider = make_member("outsider")
        channel_env.guild.members[outsider.id] = outsider
        assert channel_env.get(user=outsider, usernames=usernames) == ["host", "alice", "bob", "carol"]

    @pytest.mark.parametrize("usernames", ["", []])
    def test_blank_include_without_scheduler_adds_everyone(self, channel_env, usernames):
        assert channel_env.get(user=None, usernames=usernames) == ["host", "alice", "bob", "carol"]

    @pytest.mark.parametrize("usernames", ["", []])
    def test_blank_exclude_adds_everyone(self, channel_env, usernames):
        assert channel_env.get(usernames=usernames, include_exclude=channel_env.sched.EXCLUDE) == \
            ["host", "alice", "bob", "carol"]

    def test_rejects_other_types(self, channel_env):
        with pytest.raises(Exception, match="incompatible usernames variable type"):
            channel_env.get(usernames=1.5)

    def test_rejects_non_string_entries(self, channel_env):
        with pytest.raises(Exception, match="Failed to parse username"):
            channel_env.get(usernames=[None])


class TestIncludeExclude:
    @pytest.mark.parametrize("include_exclude", [None, "include", "Include"])
    def test_null_and_lowercase_include(self, channel_env, include_exclude):
        assert channel_env.get(usernames="alice", include_exclude=include_exclude) == ["host", "alice"]
        assert channel_env.get(roles="Mods", include_exclude=include_exclude) == ["host", "bob"]

    def test_lowercase_exclude(self, channel_env):
        assert channel_env.get(usernames="alice", include_exclude="exclude") == ["host", "bob", "carol"]
        assert channel_env.get(roles="Mods", include_exclude="exclude") == ["host", "alice", "carol"]

    @pytest.mark.parametrize("include_exclude", ["both", "", 1])
    def test_rejects_other_values(self, channel_env, include_exclude):
        with pytest.raises(Exception, match="include_exclude must be INCLUDE or EXCLUDE"):
            channel_env.get(usernames="alice", include_exclude=include_exclude)


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

    def test_usernames_with_roles_are_always_added_first(self, channel_env):
        assert channel_env.get(roles="Mods", usernames="carol") == ["host", "carol", "bob"]

    def test_usernames_with_excluded_roles_are_still_added(self, channel_env):
        assert channel_env.get(roles="Mods", usernames="bob", include_exclude=channel_env.sched.EXCLUDE) == \
            ["host", "bob", "alice", "carol"]

    def test_usernames_with_the_role_are_added_once(self, channel_env):
        assert channel_env.get(roles="Gamers", usernames="alice") == ["host", "alice"]

    def test_bot_scheduler_with_username_and_roles(self, channel_env):
        assert channel_env.get(user=channel_env.bot, usernames=str(channel_env.carol.id), roles="Mods") == \
            ["carol", "bob"]

    def test_bots_named_with_roles_are_added(self, channel_env):
        assert channel_env.get(usernames="botty", roles="Gamers") == ["host", "botty", "alice"]

    def test_blank_usernames_with_roles_use_only_roles(self, channel_env):
        assert channel_env.get(roles="Mods", usernames="") == ["host", "bob"]

    def test_bots_with_role_are_skipped(self, channel_env):
        assert "botty" not in channel_env.get(roles="Gamers,Mods")

    def test_without_scheduler(self, channel_env):
        assert channel_env.get(user=None, roles="Gamers") == ["host", "alice"]

    def test_accepts_a_list(self, channel_env):
        gamers, mods = channel_env.guild.roles
        assert channel_env.get(roles=[" gamers ", str(mods.id)]) == ["host", "alice", "bob"]

    def test_empty_list_adds_everyone(self, channel_env):
        assert channel_env.get(roles=[]) == ["host", "alice", "bob", "carol"]

    def test_accepts_a_single_id(self, channel_env):
        gamers, mods = channel_env.guild.roles
        assert channel_env.get(roles=mods.id) == ["host", "bob"]

    def test_accepts_ids_as_numbers(self, channel_env):
        gamers, mods = channel_env.guild.roles
        assert channel_env.get(roles=[gamers.id, "Mods"]) == ["host", "alice", "bob"]

    def test_rejects_other_types(self, channel_env):
        with pytest.raises(Exception, match="Failed to parse role"):
            channel_env.get(roles=1.5)

    def test_rejects_non_string_entries(self, channel_env):
        with pytest.raises(Exception, match="Failed to parse role"):
            channel_env.get(roles=[None])


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

    def test_single_id(self, env, members):
        a, b = members
        event = run(env.sched.create(event_name="Game Night", guild=env.guild, text_channel=env.text_channel,
                                     voice_channel=env.voice_channel, start_time=at(1, 20),
                                     scheduler_id=a.id, usernames=b.id))
        assert [participant.member for participant in event.participants] == [a, b]
        event = run(env.sched.schedule(event_name="Game Night 2", guild=env.guild, text_channel=env.text_channel,
                                       voice_channel=env.voice_channel, scheduler_id=a.id, usernames=b.id))
        assert [participant.member for participant in event.participants] == [a, b]

    @pytest.mark.parametrize("usernames", ["", []])
    def test_blank_usernames(self, env, members, usernames):
        a, b = members
        event = run(env.sched.schedule(event_name="Game Night", guild=env.guild, text_channel=env.text_channel,
                                       voice_channel=env.voice_channel, scheduler_id=a.id, usernames=usernames))
        assert [participant.member for participant in event.participants] == [a]
        a.bot = True
        event = run(env.sched.create(event_name="Game Night 2", guild=env.guild, text_channel=env.text_channel,
                                     voice_channel=env.voice_channel, start_time=at(1, 20),
                                     scheduler_id=a.id, usernames=usernames))
        assert [participant.member for participant in event.participants] == [b]

    def test_mixed_ids_and_names(self, env, members):
        a, b = members
        c = env.make_participant("c").member
        c.bot = False
        event = run(env.sched.create(event_name="Game Night", guild=env.guild, text_channel=env.text_channel,
                                     voice_channel=env.voice_channel, start_time=at(1, 20),
                                     scheduler_id=a.id, usernames=[b.id, "c"]))
        assert [participant.member for participant in event.participants] == [a, b, c]
        event = run(env.sched.schedule(event_name="Game Night 2", guild=env.guild, text_channel=env.text_channel,
                                       voice_channel=env.voice_channel, scheduler_id=a.id, usernames=[str(b.id), c.id]))
        assert [participant.member for participant in event.participants] == [a, b, c]


class TestCreateWithRolesOnly:
    """JSON packets can filter by roles alone, with usernames null or empty."""

    @pytest.fixture
    def members(self, env):
        gamers = SimpleNamespace(id=next_id(), name="Gamers")
        env.guild.roles = [gamers]
        a, b, c = env.make_participant("a"), env.make_participant("b"), env.make_participant("c")
        for member in env.text_channel.members:
            member.bot = False
            member.roles = []
        b.member.roles = [gamers]
        return a.member, b.member, c.member

    @pytest.mark.parametrize("usernames", [None, "", []])
    @pytest.mark.parametrize("roles", ["Gamers", ["Gamers"]])
    @pytest.mark.parametrize("include_exclude, expected", [("INCLUDE", "ab"), ("EXCLUDE", "ac")])
    def test_create(self, env, members, usernames, roles, include_exclude, expected):
        a = members[0]
        event = run(env.sched.create(event_name="Game Night", guild=env.guild, text_channel=env.text_channel,
                                     voice_channel=env.voice_channel, start_time=at(1, 20), scheduler_id=a.id,
                                     include_exclude=include_exclude, usernames=usernames, roles=roles))
        assert "".join(participant.member.name for participant in event.participants) == expected

    @pytest.mark.parametrize("usernames", [None, "", []])
    @pytest.mark.parametrize("roles", ["Gamers", ["Gamers"]])
    @pytest.mark.parametrize("include_exclude, expected", [("INCLUDE", "ab"), ("EXCLUDE", "ac")])
    def test_schedule(self, env, members, usernames, roles, include_exclude, expected):
        a = members[0]
        event = run(env.sched.schedule(event_name="Game Night", guild=env.guild, text_channel=env.text_channel,
                                       voice_channel=env.voice_channel, scheduler_id=a.id,
                                       include_exclude=include_exclude, usernames=usernames, roles=roles))
        assert "".join(participant.member.name for participant in event.participants) == expected


class TestCreateScheduler:
    @pytest.fixture
    def members(self, env):
        a, b = env.make_participant("a"), env.make_participant("b")
        for member in env.text_channel.members:
            member.bot = False
        return a.member, b.member

    def create(self, env, **kwargs):
        return run(env.sched.create(event_name="Game Night", guild=env.guild, text_channel=env.text_channel,
                                    voice_channel=env.voice_channel, start_time=at(1, 20), **kwargs))

    def test_unknown_scheduler_is_not_replaced_by_another_member(self, env, members):
        a, b = members
        event = self.create(env, scheduler_id=12345, usernames="b")
        assert [participant.member for participant in event.participants] == [b]
        assert event.scheduler is None

    def test_no_scheduler(self, env, members):
        a, b = members
        event = self.create(env, usernames=None)
        assert [participant.member for participant in event.participants] == [a, b]
        assert event.scheduler is None

    def test_bot_scheduler_is_shown_but_not_invited(self, env, members):
        a, b = members
        a.bot = True
        gamers = SimpleNamespace(id=next_id(), name="Gamers")
        env.guild.roles = [gamers]
        c = env.make_participant("c").member
        c.bot = False
        for member in (a, b, c):
            member.roles = []
        c.roles = [gamers]
        event = self.create(env, scheduler_id=a.id, usernames=str(b.id), roles="Gamers")
        assert [participant.member for participant in event.participants] == [b, c]
        assert event.scheduler.member is a and event.scheduler not in event.participants

    def test_no_participants_is_an_error(self, env, members):
        with pytest.raises(Exception, match="No one was found to invite"):
            self.create(env, usernames="nobody")

    @pytest.mark.parametrize("command", ["create", "schedule"])
    def test_only_bots_is_an_error(self, env, members, command):
        a, b = members
        a.bot = b.bot = True
        kwargs = dict(event_name="Game Night", guild=env.guild, text_channel=env.text_channel,
                      voice_channel=env.voice_channel, scheduler_id=a.id, usernames=[b.id])
        if command == "create":
            kwargs["start_time"] = at(1, 20)
        with pytest.raises(Exception, match="at least one person, not just bots"):
            run(getattr(env.sched, command)(**kwargs))
        assert env.sched.client.events == []


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
