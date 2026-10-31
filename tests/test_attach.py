from types import SimpleNamespace

import pytest
from discord import EntityType

from fakes import FakeScheduledEvent, at, make_member, run
from test_lifecycle import FakeInteraction


@pytest.fixture
def attach(env, monkeypatch):
    """Adds guild events to the env's guild, and runs /attach and its autocomplete as a channel member."""
    monkeypatch.setattr(env.sched.Event, "save_image_to_file", lambda self: env.sched.asyncio.sleep(0))
    user = make_member(99, "host")
    env.guild.members[user.id] = user
    env.text_channel.members.append(user)

    def guild_event(name="Movie", start=None, **kwargs):
        kwargs.setdefault("channel", env.voice_channel)
        scheduled_event = FakeScheduledEvent(start or at(1, 20), name=name, **kwargs)
        env.guild.created.append(scheduled_event)
        return scheduled_event

    def interaction():
        result = FakeInteraction(user)
        result.guild = env.guild
        result.channel = env.text_channel
        result.deleted_original = False

        async def delete_original_response():
            result.deleted_original = True

        result.delete_original_response = delete_original_response
        return result

    def use(value):
        result = interaction()
        run(env.sched.attach_command.callback(result, value))
        return result

    def suggest(current=""):
        return run(env.sched.attach_autocomplete(interaction(), current))

    return SimpleNamespace(guild_event=guild_event, use=use, suggest=suggest, user=user)


def bot_event(env, name):
    (event,) = [event for event in env.sched.client.events if event.name == name]
    return event


class TestAutocomplete:
    def test_suggests_unmanaged_events_soonest_first(self, env, attach, eastern):
        later = attach.guild_event("Later", at(2, 20))
        sooner = attach.guild_event("Sooner", at(1, 20))
        assert [choice.value for choice in attach.suggest()] == [str(sooner.id), str(later.id)]

    def test_labels_show_the_start_time(self, env, attach, eastern):
        guild_event = attach.guild_event("Movie", at(1, 20))
        (choice,) = attach.suggest()
        assert choice.name == f"Movie ({at(1, 20):%a %m/%d} 20:00 ET)"

    def test_long_names_keep_the_start_time(self, env, attach, eastern):
        attach.guild_event("x" * 100)
        (choice,) = attach.suggest()
        assert len(choice.name) == 100 and choice.name.endswith("20:00 ET)")

    def test_filters_by_what_was_typed(self, env, attach):
        attach.guild_event("Board Games")
        movie = attach.guild_event("Movie Night")
        assert [choice.value for choice in attach.suggest("  movie ")] == [str(movie.id)]

    def test_leaves_out_managed_events(self, env, attach):
        managed = attach.guild_event("Managed")
        env.make_event([env.make_participant("a")], created=True, start_times=[managed.start_time], scheduled_events=[managed])
        unmanaged = attach.guild_event("Unmanaged")
        assert [choice.value for choice in attach.suggest()] == [str(unmanaged.id)]

    def test_at_most_25(self, env, attach):
        for day in range(30):
            attach.guild_event(f"Event {day}", at(day + 1, 20))
        assert len(attach.suggest()) == 25


class TestAttach:
    def test_makes_an_event_with_everyone_in_the_channel(self, env, attach):
        member = env.make_participant("a").member
        guild_event = attach.guild_event("Movie")
        interaction = attach.use(str(guild_event.id))
        event = bot_event(env, "Movie")
        assert event.created and event.scheduled_events == [guild_event] and event.start_times == [guild_event.start_time]
        assert event.voice_channel is env.voice_channel and event.text_channel is env.text_channel
        assert {participant.member.id for participant in event.participants} == {attach.user.id, member.id}
        assert all(participant.answered for participant in event.participants)
        assert event.scheduler.member is attach.user
        assert env.update_messages_calls == [event]

    def test_sends_no_confirmation(self, env, attach):
        interaction = attach.use(str(attach.guild_event().id))
        assert interaction.deleted_original and interaction.followups == []

    def test_typed_location(self, env, attach):
        guild_event = attach.guild_event("Range", entity_type=EntityType.external, location="123 Main St", channel=None)
        attach.use(str(guild_event.id))
        event = bot_event(env, "Range")
        assert (event.voice_channel, event.location) == (None, "123 Main St")

    def test_uses_the_cover_image(self, env, attach):
        guild_event = attach.guild_event(cover_image=SimpleNamespace(url="https://example.com/cover.png"))
        attach.use(str(guild_event.id))
        assert bot_event(env, "Movie").image_url == "https://example.com/cover.png"

    def test_adds_matching_events_as_occurrences(self, env, attach):
        third = attach.guild_event("Movie", at(3, 20))
        first = attach.guild_event("Movie", at(1, 20))
        attach.guild_event("Movie", at(2, 20), entity_type=EntityType.external, location="Elsewhere", channel=None)
        attach.guild_event("Other", at(2, 20))
        attach.use(str(third.id))
        event = bot_event(env, "Movie")
        assert event.scheduled_events == [first, third]
        assert event.start_times == [at(1, 20), at(3, 20)]

    def test_matching_occurrences_get_the_events_image(self, env, attach, monkeypatch):
        monkeypatch.setattr(env.sched.Event, "has_image_saved", property(lambda self: True))
        monkeypatch.setattr(env.sched.Event, "get_image", lambda self: b"image")
        with_cover = attach.guild_event(cover_image=SimpleNamespace(url="https://example.com/cover.png"))
        without_cover = attach.guild_event(start=at(2, 20))
        attach.use(str(with_cover.id))
        assert with_cover.edits == [] and without_cover.edits == [{"image": b"image"}]

    def test_joins_a_bot_event_with_the_same_name_and_location(self, env, attach):
        existing = env.make_event([env.make_participant("a")])
        existing_name = existing.name
        guild_event = attach.guild_event(existing_name)
        attach.use(str(guild_event.id))
        assert bot_event(env, existing_name) is existing
        assert existing.created and existing.scheduled_events == [guild_event]

    def test_doesnt_join_a_bot_event_at_another_location(self, env, attach):
        existing = env.make_event([env.make_participant("a")], voice_channel=None, location="Elsewhere")
        guild_event = attach.guild_event(existing.name)
        attach.use(str(guild_event.id))
        assert len([event for event in env.sched.client.events if event.name == existing.name]) == 2
        assert existing.scheduled_events == []

    def test_keeps_existing_occurrences_aligned(self, env, attach):
        linked = FakeScheduledEvent(at(1, 20), name="Movie", channel=env.voice_channel)
        env.guild.created.append(linked)
        # A later occurrence that hasn't been created yet, and one whose guild event exists but isn't linked
        existing = env.make_event([env.make_participant("a")], created=True, multi_event=True,
                                  start_times=[at(1, 20), at(2, 20), at(4, 20)], scheduled_events=[linked])
        existing.name = "Movie"
        unlinked = attach.guild_event("Movie", at(2, 20))
        attach.use(str(unlinked.id))
        assert existing.scheduled_events == [linked, unlinked]
        assert existing.start_times == [at(1, 20), at(2, 20), at(4, 20)]


class TestRejected:
    def test_typed_text_that_isnt_a_suggestion(self, env, attach):
        attach.guild_event()
        interaction = attach.use("Movie")
        ((kind, kwargs),) = interaction.responses
        assert kind == "send_message" and kwargs["ephemeral"] and "suggestions" in kwargs["content"]
        assert not any(event.name == "Movie" for event in env.sched.client.events)

    def test_deleted_guild_event(self, env, attach):
        guild_event = attach.guild_event()
        guild_event.deleted = True
        interaction = attach.use(str(guild_event.id))
        assert interaction.responses[0][1]["ephemeral"]

    def test_already_managed(self, env, attach):
        guild_event = attach.guild_event()
        env.make_event([env.make_participant("a")], created=True, start_times=[guild_event.start_time], scheduled_events=[guild_event])
        interaction = attach.use(str(guild_event.id))
        ((kind, kwargs),) = interaction.responses
        assert kwargs["ephemeral"] and "already managing" in kwargs["content"]

    def test_option_is_required_and_autocompleted(self, sched):
        (event,) = sched.attach_command.parameters
        assert event.required and event.autocomplete
