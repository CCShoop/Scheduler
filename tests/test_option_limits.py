from types import SimpleNamespace

import pytest

from fakes import run
from test_lifecycle import FakeInteraction


def option_limits(command):
    return {parameter.name: (parameter.min_value, parameter.max_value) for parameter in command.parameters
            if parameter.min_value is not None or parameter.max_value is not None}


class TestSlashCommandLimits:
    def test_create(self, sched):
        assert option_limits(sched.create_command) == {
            "event_name": (1, 100), "location": (1, 100), "duration": (0, 1440)}

    def test_schedule(self, sched):
        assert option_limits(sched.schedule_command) == {
            "event_name": (1, 100), "location": (1, 100), "duration": (0, 1440), "timeout": (1, 30)}

    def test_edit(self, sched):
        assert option_limits(sched.edit_command) == {
            "name": (1, 100), "location": (1, 100), "duration": (0, 1440), "timeout": (1, 30)}


class TestValidateEventOptions:
    @pytest.mark.parametrize("kwargs", [
        {},
        {"event_name": "a"},
        {"event_name": "a" * 100},
        {"duration": 0},
        {"duration": 1440},
        {"timeout_days": 1},
        {"timeout_days": 30},
    ])
    def test_accepts_limits(self, sched, kwargs):
        sched.validate_event_options(**kwargs)

    @pytest.mark.parametrize("kwargs, match", [
        ({"event_name": ""}, "Event name"),
        ({"event_name": "a" * 101}, "Event name"),
        ({"duration": -1}, "Duration"),
        ({"duration": 1441}, "Duration"),
        ({"timeout_days": 0}, "Timeout"),
        ({"timeout_days": 31}, "Timeout"),
    ])
    def test_rejects_out_of_range(self, sched, kwargs, match):
        with pytest.raises(Exception, match=match):
            sched.validate_event_options(**kwargs)

    def test_schedule_rejects_bad_timeout(self, env):
        # Covers the json packet path, which skips slash command limits
        with pytest.raises(Exception, match="Timeout"):
            run(env.sched.schedule(event_name="packet", guild=env.guild, text_channel=env.text_channel,
                                   voice_channel=env.voice_channel, timeout_days=0))
        assert env.sched.client.events == []

    def test_create_rejects_bad_duration(self, env):
        with pytest.raises(Exception, match="Duration"):
            run(env.sched.create(event_name="packet", guild=env.guild, text_channel=env.text_channel,
                                 voice_channel=env.voice_channel, start_time="2200", duration=-5))
        assert env.sched.client.events == []


class TestScheduleAgainModal:
    def submit(self, env, monkeypatch, duration):
        event = env.make_event([env.make_participant("a")])
        calls = []

        async def fake_schedule(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(env.sched, "schedule", fake_schedule)

        async def go():
            modal = env.sched.ScheduleAgainModal(event=event, title="Schedule Again")
            modal.event_duration._value = duration
            modal.event_start_time._value = ""
            interaction = FakeInteraction(SimpleNamespace(id=1, name="a", nick=None))
            await modal.on_submit(interaction)
            return modal, interaction

        modal, interaction = run(go())
        return modal, interaction, calls

    def test_name_and_duration_lengths_are_limited(self, env, monkeypatch):
        modal, _, _ = self.submit(env, monkeypatch, "30")
        assert modal.event_name.max_length == 100
        assert modal.event_duration.max_length == 4

    def test_out_of_range_duration_is_reported(self, env, monkeypatch):
        _, interaction, calls = self.submit(env, monkeypatch, "9999")
        assert calls == []
        assert "Duration must be 0 to 1440 minutes" in interaction.followups[0]["content"]

    def test_blank_duration_means_automatic(self, env, monkeypatch):
        _, _, calls = self.submit(env, monkeypatch, "")
        assert calls[0]["duration"] == 0
