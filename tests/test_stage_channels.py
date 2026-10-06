from types import SimpleNamespace

from discord import EntityType

from libs.participant import TimeBlock
from fakes import FakeScheduledEvent, FakeStageChannel, FakeStageInstance, at, run
from test_lifecycle import FakeInteraction, fake_event_buttons


def add_stage_channel(env, **kwargs):
    stage_channel = FakeStageChannel(**kwargs)
    env.guild.channels[stage_channel.id] = stage_channel
    env.guild.stage_channels.append(stage_channel)
    return stage_channel


def make_stage_event(env, stage_channel, started=False):
    scheduled_event = FakeScheduledEvent(at(1, 20))
    event = env.make_event([env.make_participant("a")], voice_channel=stage_channel, created=True, started=started,
                           start_times=[at(1, 20)], scheduled_events=[scheduled_event])
    event.event_buttons = fake_event_buttons()
    return event, scheduled_event


class TestLocation:
    def test_resolves_stage_channel_by_name_and_id(self, env):
        stage_channel = add_stage_channel(env)
        assert env.sched.resolve_location(env.guild, "stage") == (stage_channel, None)
        assert env.sched.resolve_location(env.guild, str(stage_channel.id)) == (stage_channel, None)

    def test_autocomplete_lists_stage_channels_after_voice_channels(self, env):
        add_stage_channel(env)
        interaction = SimpleNamespace(guild=env.guild)
        choices = run(env.sched.location_autocomplete(interaction, ""))
        assert [choice.name for choice in choices] == ["🔊 General", "🎙️ Stage"]

    def test_voice_channel_is_the_default_over_stage(self, env):
        add_stage_channel(env)
        event = env.sched.Event(name="default", voice_channel=None, guild=env.guild, text_channel=env.text_channel)
        assert event.voice_channel is env.voice_channel

    def test_stage_is_the_default_without_voice_channels(self, env):
        env.guild.voice_channels.clear()
        stage_channel = add_stage_channel(env)
        event = env.sched.Event(name="default", voice_channel=None, guild=env.guild, text_channel=env.text_channel)
        assert event.voice_channel is stage_channel


class TestCreation:
    def test_guild_events_are_stage_instance_events(self, env):
        stage_channel = add_stage_channel(env)
        a = env.make_participant("a", [TimeBlock(at(1, 18), at(1, 23))])
        event = env.make_event([a], voice_channel=stage_channel)
        assert event.is_stage
        assert event.entity_type == EntityType.stage_instance
        run(event.create_if_possible())
        (scheduled_event,) = env.guild.created
        assert scheduled_event.kwargs["entity_type"] == EntityType.stage_instance
        assert scheduled_event.kwargs["channel"] is stage_channel

    def test_voice_channel_events_are_unchanged(self, env):
        event = env.make_event([env.make_participant("a")])
        assert not event.is_stage
        assert event.entity_type == EntityType.voice


class TestStartAndEnd:
    def test_start_opens_stage_for_the_occurrence(self, env):
        stage_channel = add_stage_channel(env)
        event, scheduled_event = make_stage_event(env, stage_channel)
        run(event.start())
        assert event.started
        assert stage_channel.instance.scheduled_event_id == scheduled_event.id
        assert stage_channel.instance.topic == event.name

    def test_start_falls_back_when_stage_is_already_live(self, env):
        stage_channel = add_stage_channel(env)
        other_instance = FakeStageInstance(stage_channel, "Other", scheduled_event_id=None)
        stage_channel.instance = other_instance
        event, scheduled_event = make_stage_event(env, stage_channel)
        run(event.start())
        assert event.started
        assert stage_channel.instance is other_instance
        assert scheduled_event.status.name == "active"

    def test_start_falls_back_when_stage_cannot_be_opened(self, env):
        stage_channel = add_stage_channel(env, fail_create=True)
        event, scheduled_event = make_stage_event(env, stage_channel)
        run(event.start())
        assert event.started
        assert stage_channel.instance is None
        assert scheduled_event.status.name == "active"

    def test_end_closes_its_stage(self, env):
        stage_channel = add_stage_channel(env)
        event, scheduled_event = make_stage_event(env, stage_channel)
        run(event.start())
        instance = stage_channel.instance
        run(event.end())
        assert instance.deleted
        assert scheduled_event.deleted

    def test_end_leaves_other_stages_open(self, env):
        stage_channel = add_stage_channel(env)
        other_instance = FakeStageInstance(stage_channel, "Other", scheduled_event_id=None)
        stage_channel.instance = other_instance
        event, _ = make_stage_event(env, stage_channel, started=True)
        run(event.end())
        assert not other_instance.deleted

    def test_cancel_while_started_closes_its_stage(self, env):
        stage_channel = add_stage_channel(env)
        event, _ = make_stage_event(env, stage_channel)
        run(event.start())
        instance = stage_channel.instance
        run(event.cancel(reason="stop"))
        assert instance.deleted


class TestVoiceChannelPrompt:
    def test_stage_channel_can_be_selected(self, env):
        event = env.make_event([env.make_participant("a")])
        env.guild.channels.pop(env.voice_channel.id)
        env.guild.voice_channels.remove(env.voice_channel)
        run(env.sched.on_guild_channel_delete(SimpleNamespace(id=env.voice_channel.id, guild=env.guild)))
        stage_channel = add_stage_channel(env)
        prompt = event.voice_channel_prompt
        prompt.channel_select._values = [SimpleNamespace(id=stage_channel.id)]
        run(prompt.channel_select_callback(FakeInteraction(SimpleNamespace(name="a", nick=None))))
        assert event.voice_channel is stage_channel
        assert event.entity_type == EntityType.stage_instance
