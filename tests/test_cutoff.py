from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from libs.participant import DEFAULT_HOURS_PAST_MIDNIGHT_CUTOFF, TimeBlock
from fakes import FakeGuild, at, make_member, run
from test_lifecycle import FakeInteraction


@pytest.fixture
def guilds(sched, monkeypatch):
    """The guilds the client is in, as a list tests can change."""
    guild_list = []
    monkeypatch.setattr(type(sched.client), "guilds", property(lambda self: guild_list))
    return guild_list


def add_member(guild, member_id, name="user"):
    member = make_member(member_id, name)
    guild.members[member_id] = member
    return member


class TestFullAvailability:
    def test_uses_the_users_cutoff(self, env):
        a = env.make_participant("a")
        b = env.make_participant("b")
        env.sched.participant_lib.user_cutoffs[a.member.id] = 4
        a.set_full_availability(date=at(1, 0))
        b.set_full_availability(date=at(1, 0))
        assert (a.availability[0].start_time, a.availability[0].end_time) == (at(1, 0), at(2, 4))
        assert (b.availability[0].start_time, b.availability[0].end_time) == (at(1, 0), at(2, DEFAULT_HOURS_PAST_MIDNIGHT_CUTOFF))

    def test_full_keyword_uses_the_users_cutoff(self, env):
        a = env.make_participant("a")
        env.sched.participant_lib.user_cutoffs[a.member.id] = 3
        a.set_specific_availability("full", at(1, 0).strftime("%Y-%m-%d"))
        assert (a.availability[0].start_time, a.availability[0].end_time) == (at(1, 0), at(2, 3))


class TestCutoffCommand:
    def use(self, env, user_id, hours):
        interaction = FakeInteraction(SimpleNamespace(id=user_id, name="user", nick=None))
        run(env.sched.cutoff_command.callback(interaction, hours))
        (kind, kwargs), = interaction.responses
        assert kind == "send_message" and kwargs["ephemeral"]
        return kwargs["content"]

    def test_saves_the_users_cutoff(self, env):
        self.use(env, 7, 3)
        assert env.sched.participant_lib.user_cutoffs == {7: 3}

    def test_blank_resets_to_the_default(self, env):
        env.sched.participant_lib.user_cutoffs[7] = 3
        content = self.use(env, 7, None)
        assert env.sched.participant_lib.user_cutoffs == {}
        assert content == "Your Full Availability now extends to midnight."

    def test_default_value_is_not_saved(self, env):
        env.sched.participant_lib.user_cutoffs[7] = 3
        self.use(env, 7, DEFAULT_HOURS_PAST_MIDNIGHT_CUTOFF)
        assert env.sched.participant_lib.user_cutoffs == {}

    def test_discord_limits_hours_to_negative_6_through_23(self, sched):
        (hours,) = sched.cutoff_command.parameters
        assert (hours.min_value, hours.max_value, hours.required) == (-6, 23, False)

    @pytest.mark.parametrize("hours", [-7, 24])
    def test_rejects_out_of_range(self, env, hours):
        content = self.use(env, 7, hours)
        assert "between -6 and 23" in content
        assert env.sched.participant_lib.user_cutoffs == {}

    @pytest.mark.parametrize("hours, time", [(-6, "6 PM"), (-2, "10 PM"), (1, "1 AM"), (12, "noon"), (23, "11 PM")])
    def test_reply_shows_the_time_of_day(self, env, hours, time):
        content = self.use(env, 7, hours)
        assert env.sched.participant_lib.user_cutoffs == {7: hours}
        assert content == f"Your Full Availability now extends to {time}."

    def test_only_changes_the_users_own_cutoff(self, env):
        env.sched.participant_lib.user_cutoffs[8] = 5
        self.use(env, 7, 3)
        assert env.sched.participant_lib.user_cutoffs == {7: 3, 8: 5}


class TestPersistence:
    def test_saved_with_string_keys(self, sched):
        sched.participant_lib.user_cutoffs[7] = 3
        assert sched.client.events_dict["user_cutoffs"] == {"7": 3}

    def test_loaded_on_startup(self, sched, guilds, monkeypatch):
        guild = FakeGuild()
        add_member(guild, 7)
        guilds.append(guild)
        monkeypatch.setattr(sched.client, "loaded_json", False)
        monkeypatch.setattr(sched.persist, "read", lambda: {"events": [], "user_cutoffs": {"7": 3}})
        run(sched.client.retrieve_events())
        assert sched.participant_lib.user_cutoffs == {7: 3}

    def test_users_who_left_while_offline_are_deleted_on_startup(self, sched, guilds, monkeypatch):
        guild = FakeGuild()
        add_member(guild, 7)
        guilds.append(guild)
        monkeypatch.setattr(sched.client, "loaded_json", False)
        monkeypatch.setattr(sched.persist, "read", lambda: {"events": [], "user_cutoffs": {"7": 3, "8": 5}})
        run(sched.client.retrieve_events())
        assert sched.participant_lib.user_cutoffs == {7: 3}

    def test_data_without_cutoffs_loads(self, sched, guilds, monkeypatch):
        monkeypatch.setattr(sched.client, "loaded_json", False)
        monkeypatch.setattr(sched.persist, "read", lambda: {"events": []})
        run(sched.client.retrieve_events())
        assert sched.participant_lib.user_cutoffs == {}


class TestPruning:
    def leave(self, sched, guild, user_id):
        del guild.members[user_id]
        run(sched.on_raw_member_remove(SimpleNamespace(user=SimpleNamespace(id=user_id), guild_id=guild.id)))

    def test_deleted_when_user_leaves_their_last_shared_guild(self, sched, guilds):
        guild = FakeGuild()
        add_member(guild, 7)
        guilds.append(guild)
        sched.participant_lib.user_cutoffs[7] = 3
        self.leave(sched, guild, 7)
        assert sched.participant_lib.user_cutoffs == {}

    def test_kept_while_user_shares_another_guild(self, sched, guilds):
        first, second = FakeGuild(), FakeGuild()
        add_member(first, 7)
        add_member(second, 7)
        guilds.extend([first, second])
        sched.participant_lib.user_cutoffs[7] = 3
        self.leave(sched, first, 7)
        assert sched.participant_lib.user_cutoffs == {7: 3}

    def test_other_users_are_untouched(self, sched, guilds):
        guild = FakeGuild()
        add_member(guild, 7)
        guilds.append(guild)
        sched.participant_lib.user_cutoffs.update({7: 3, 8: 5})
        self.leave(sched, guild, 7)
        assert sched.participant_lib.user_cutoffs == {8: 5}

    def test_kept_while_member_cache_is_incomplete(self, sched, guilds):
        guild = FakeGuild()
        add_member(guild, 7)
        other = FakeGuild()
        other.chunked = False
        guilds.extend([guild, other])
        sched.participant_lib.user_cutoffs[7] = 3
        self.leave(sched, guild, 7)
        assert sched.participant_lib.user_cutoffs == {7: 3}

    def test_deleted_when_bot_leaves_the_only_shared_guild(self, sched, guilds):
        guild, other = FakeGuild(), FakeGuild()
        add_member(guild, 7)
        add_member(other, 8)
        guilds.extend([guild, other])
        sched.participant_lib.user_cutoffs.update({7: 3, 8: 5})
        guilds.remove(guild)
        run(sched.on_guild_remove(guild))
        assert sched.participant_lib.user_cutoffs == {8: 5}


class FixedNow:
    """Pins datetime.now() in libs.participant to a time of day today."""

    @staticmethod
    def install(monkeypatch, sched, hour, minute=0):
        fixed = at(0, hour, minute)

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed

        monkeypatch.setattr(sched.participant_lib, "datetime", FixedDatetime)
        return fixed


class TestNegativeCutoff:
    def test_ends_before_midnight(self, env):
        a = env.make_participant("a")
        env.sched.participant_lib.user_cutoffs[a.member.id] = -2
        a.set_full_availability(date=at(1, 0))
        assert (a.availability[0].start_time, a.availability[0].end_time) == (at(1, 0), at(1, 22))

    def test_today_before_cutoff_runs_until_cutoff(self, env, monkeypatch):
        fixed = FixedNow.install(monkeypatch, env.sched, 20)
        a = env.make_participant("a")
        env.sched.participant_lib.user_cutoffs[a.member.id] = -2
        a.set_full_availability()
        assert (a.availability[0].start_time, a.availability[0].end_time) == (fixed, at(0, 22))

    def test_today_after_cutoff_raises_without_changing_availability(self, env, monkeypatch):
        FixedNow.install(monkeypatch, env.sched, 23)
        a = env.make_participant("a")
        env.sched.participant_lib.user_cutoffs[a.member.id] = -2
        with pytest.raises(Exception, match="ends at 10 PM .* already passed"):
            a.set_full_availability()
        assert a.availability == []
        assert not a.full_availability_flag and not a.answered

    def test_full_availability_button_reports_passed_cutoff(self, env, monkeypatch):
        FixedNow.install(monkeypatch, env.sched, 23)
        a = env.make_participant("a")
        env.sched.participant_lib.user_cutoffs[a.member.id] = -2
        event = env.make_event([a])

        async def press():
            buttons = env.sched.AvailabilityButtons(event)
            interaction = FakeInteraction(a.member)
            await buttons.full_button.callback(interaction)
            return interaction

        interaction = run(press())
        assert "already passed" in interaction.followups[0]["content"]
        assert a.availability == []


class TestFullAvailabilityTooShort:
    def press(self, env, event, member):
        async def click():
            buttons = env.sched.AvailabilityButtons(event)
            interaction = FakeInteraction(member)
            await buttons.full_button.callback(interaction)
            return interaction
        return run(click())

    def ends_in(self, env, monkeypatch, participant, minutes):
        end_time = env.sched.now() + timedelta(minutes=minutes)
        monkeypatch.setattr(participant, "get_full_availability_end", lambda date=None: end_time)

    def test_refuses_window_too_short_for_the_event(self, env, monkeypatch):
        a = env.make_participant("a")
        # Pressed at 11:48 PM with a midnight cutoff
        self.ends_in(env, monkeypatch, a, 12)
        event = env.make_event([a], duration=timedelta(minutes=30))
        interaction = self.press(env, event, a.member)
        content = interaction.followups[0]["content"]
        assert "ends at midnight, 12 minutes from now" in content and "/cutoff" in content
        assert a.availability == []
        assert not a.full_availability_flag and not a.answered

    def test_counts_the_start_delay(self, env, monkeypatch):
        a = env.make_participant("a")
        # Long enough for the 30 minute event now, but not one starting START_TIME_DELAY from now
        self.ends_in(env, monkeypatch, a, env.sched.START_TIME_DELAY + 29)
        event = env.make_event([a], duration=timedelta(minutes=30))
        interaction = self.press(env, event, a.member)
        assert "too soon" in interaction.followups[0]["content"]

    def test_accepts_window_long_enough(self, env, monkeypatch):
        a = env.make_participant("a")
        self.ends_in(env, monkeypatch, a, env.sched.START_TIME_DELAY + 30)
        event = env.make_event([a, env.make_participant("b")], duration=timedelta(minutes=30))
        self.press(env, event, a.member)
        assert a.full_availability_flag and a.answered


def test_every_command_and_option_description_fits_discord(sched):
    # Discord rejects the whole command sync if any description is over 100 characters
    for command in sched.client.tree.get_commands():
        assert len(command.description) <= 100, command.name
        for parameter in command.parameters:
            assert len(parameter.description) <= 100, f"{command.name} {parameter.name}"


class TestRespondTooSoon:
    """The Respond form reports times that end too soon to fit the event, instead of dropping them silently."""

    def submit(self, env, monkeypatch, event, participant, blocks):
        def fake_set_specific_availability(avail_string, date_string):
            participant.availability.extend(blocks)
            participant.answered = True

        monkeypatch.setattr(participant, "set_specific_availability", fake_set_specific_availability)

        async def go():
            modal = env.sched.AvailabilityModal(event, participant, title="Respond")
            interaction = FakeInteraction(participant.member)
            await modal.on_submit(interaction)
            return interaction
        return run(go())

    def test_reports_and_drops_times_ending_too_soon(self, env, monkeypatch):
        now = env.sched.now()
        a = env.make_participant("a")
        event = env.make_event([a, env.make_participant("b")], duration=timedelta(minutes=30))
        too_soon = TimeBlock(now, now + timedelta(minutes=45))
        later = TimeBlock(at(1, 20), at(1, 22))
        interaction = self.submit(env, monkeypatch, event, a, [too_soon, later])
        content = interaction.followups[0]["content"]
        assert "weren't saved because they end too soon" in content
        assert f"<t:{int(too_soon.end_time.timestamp())}:t>" in content
        assert "still need to respond" not in content
        assert [(tb.start_time, tb.end_time) for tb in a.availability] == [(at(1, 20), at(1, 22))]
        assert a.answered

    def test_says_when_nothing_usable_is_left(self, env, monkeypatch):
        now = env.sched.now()
        a = env.make_participant("a")
        event = env.make_event([a, env.make_participant("b")], duration=timedelta(minutes=30))
        interaction = self.submit(env, monkeypatch, event, a, [TimeBlock(now, now + timedelta(minutes=45))])
        assert "still need to respond" in interaction.followups[0]["content"]
        assert a.availability == []
        assert not a.answered

    def test_times_that_fit_are_saved_without_a_message(self, env, monkeypatch):
        now = env.sched.now()
        a = env.make_participant("a")
        event = env.make_event([a, env.make_participant("b")], duration=timedelta(minutes=30))
        fits = TimeBlock(now, now + timedelta(minutes=env.sched.START_TIME_DELAY + 30))
        interaction = self.submit(env, monkeypatch, event, a, [fits])
        assert interaction.followups == []
        assert a.answered
