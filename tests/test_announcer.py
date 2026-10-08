import json
from types import SimpleNamespace

import pytest

import libs.announcer as announcer_lib
from libs.announcer import Announcer, ESPEAK_NG, PIPER_EN, PIPER_JP, NONE, JAPANESE, LATIN
from fakes import FakeGuild, make_member, run
from test_lifecycle import FakeInteraction, make_created_event


class FakeVoiceClient:
    """Stands in for discord.VoiceClient, finishing playback right away."""

    def __init__(self, guild, channel, log):
        self.guild = guild
        self.channel = channel
        self.log = log

    def play(self, source, after=None):
        self.log.append(("play", self.channel.id, source))
        after(None)

    def stop(self):
        self.log.append(("stop", self.channel.id))

    async def move_to(self, channel):
        self.log.append(("move", channel.id))
        self.channel = channel

    async def disconnect(self):
        self.log.append(("disconnect", self.channel.id))
        self.guild.voice_client = None


def make_voice_channel(guild, channel_id, log, members=None):
    channel = SimpleNamespace(id=channel_id, name=f"voice-{channel_id}", members=members if members is not None else [make_member(channel_id * 10, "listener")])

    async def connect(timeout=None, self_deaf=False):
        log.append(("connect", channel_id))
        guild.voice_client = FakeVoiceClient(guild, channel, log)
        return guild.voice_client

    channel.connect = connect
    return channel


@pytest.fixture
def voice(monkeypatch):
    """A guild with announcements on, and synthesis and playback faked."""
    log = []
    guild = FakeGuild()
    guild.name = "guild"
    guild.voice_client = None
    announcer_lib.set_engine(guild.id, ESPEAK_NG)
    monkeypatch.setattr(announcer_lib, "missing_requirements", lambda engine: [])

    async def fake_synthesize(engine, text, path):
        log.append(("synthesize", engine, text))

    monkeypatch.setattr(announcer_lib, "synthesize", fake_synthesize)
    monkeypatch.setattr(announcer_lib, "FFmpegPCMAudio", lambda path: "audio")
    yield SimpleNamespace(guild=guild, log=log)
    announcer_lib.guild_engines.clear()


def announce_all(announcer, guild, *announcements):
    async def go():
        for channel, name, action in announcements:
            announcer.announce(guild, channel, name, action)
        await announcer.workers[guild.id]
    run(go())


class TestAnnounce:
    def test_reads_out_and_leaves(self, voice):
        channel = make_voice_channel(voice.guild, 1, voice.log)
        announce_all(Announcer(), voice.guild, (channel, "x", "starting"))
        assert voice.log == [("synthesize", ESPEAK_NG, "The event x is starting now."),
                             ("connect", 1), ("play", 1, "audio"), ("disconnect", 1)]

    def test_queues_announcements_in_one_guild(self, voice):
        first = make_voice_channel(voice.guild, 1, voice.log)
        second = make_voice_channel(voice.guild, 2, voice.log)
        announce_all(Announcer(), voice.guild, (first, "one", "starting"), (second, "two", "ending"))
        assert voice.log == [("synthesize", ESPEAK_NG, "The event one is starting now."), ("connect", 1), ("play", 1, "audio"),
                             ("synthesize", ESPEAK_NG, "The event two is ending now."), ("move", 2), ("play", 2, "audio"),
                             ("disconnect", 2)]

    def test_announcements_off_does_nothing(self, voice):
        announcer_lib.set_engine(voice.guild.id, NONE)
        channel = make_voice_channel(voice.guild, 1, voice.log)
        announcer = Announcer()
        announcer.announce(voice.guild, channel, "x", "starting")
        assert announcer.workers == {}
        assert voice.guild.id not in announcer_lib.guild_engines

    def test_skips_channels_with_only_bots(self, voice):
        bot = make_member(5, "bot")
        bot.bot = True
        channel = make_voice_channel(voice.guild, 1, voice.log, members=[bot])
        announcer = Announcer()
        announcer.announce(voice.guild, channel, "x", "starting")
        assert announcer.workers == {}

    def test_skips_channel_emptied_while_queued(self, voice):
        first = make_voice_channel(voice.guild, 1, voice.log)
        second = make_voice_channel(voice.guild, 2, voice.log)

        async def go():
            announcer = Announcer()
            announcer.announce(voice.guild, first, "one", "starting")
            announcer.announce(voice.guild, second, "two", "ending")
            second.members.clear()
            await announcer.workers[voice.guild.id]
        run(go())
        assert ("synthesize", ESPEAK_NG, "The event two is ending now.") not in voice.log

    def test_failure_moves_on_to_next_announcement(self, voice, monkeypatch):
        first = make_voice_channel(voice.guild, 1, voice.log)
        second = make_voice_channel(voice.guild, 2, voice.log)

        async def flaky_synthesize(engine, text, path):
            if "one" in text:
                raise RuntimeError("espeak-ng exited with 1")
            voice.log.append(("synthesize", engine, text))

        monkeypatch.setattr(announcer_lib, "synthesize", flaky_synthesize)
        announce_all(Announcer(), voice.guild, (first, "one", "starting"), (second, "two", "ending"))
        assert voice.log == [("synthesize", ESPEAK_NG, "The event two is ending now."), ("connect", 2), ("play", 2, "audio"), ("disconnect", 2)]

    def test_missing_requirements_skips_without_joining(self, voice, monkeypatch):
        monkeypatch.setattr(announcer_lib, "missing_requirements", lambda engine: ["ffmpeg"])
        channel = make_voice_channel(voice.guild, 1, voice.log)
        announce_all(Announcer(), voice.guild, (channel, "one", "starting"))
        assert voice.log == []

    def test_playback_timeout_stops_and_leaves(self, voice, monkeypatch):
        monkeypatch.setattr(announcer_lib, "PLAY_TIMEOUT_SECONDS", 0)
        monkeypatch.setattr(FakeVoiceClient, "play", lambda self, source, after=None: None)
        channel = make_voice_channel(voice.guild, 1, voice.log)
        announce_all(Announcer(), voice.guild, (channel, "one", "starting"))
        assert voice.log[-2:] == [("stop", 1), ("disconnect", 1)]

    def test_announcement_while_leaving_is_still_read_out(self, voice, monkeypatch):
        first = make_voice_channel(voice.guild, 1, voice.log)
        second = make_voice_channel(voice.guild, 2, voice.log)
        announcer = Announcer()
        original_disconnect = FakeVoiceClient.disconnect

        async def disconnect_and_announce(self):
            await original_disconnect(self)
            if self.channel is first:
                announcer.announce(voice.guild, second, "two", "ending")

        monkeypatch.setattr(FakeVoiceClient, "disconnect", disconnect_and_announce)
        announce_all(announcer, voice.guild, (first, "one", "starting"))
        assert ("play", 2, "audio") in voice.log


class TestMissingRequirements:
    def test_none_needs_nothing(self):
        assert announcer_lib.missing_requirements(NONE) == []

    def test_piper_needs_a_model(self, monkeypatch, tmp_path):
        monkeypatch.setattr(announcer_lib.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(announcer_lib.opus, "is_loaded", lambda: True)
        monkeypatch.delenv("PIPER_MODEL_EN", raising=False)
        assert any("PIPER_MODEL_EN" in item for item in announcer_lib.missing_requirements(PIPER_EN))
        model = tmp_path / "voice.onnx"
        model.write_bytes(b"")
        monkeypatch.setenv("PIPER_MODEL_EN", str(model))
        assert not any("PIPER_MODEL_EN" in item for item in announcer_lib.missing_requirements(PIPER_EN))

    def test_piper_runs_with_the_bots_python_when_installed_there(self, monkeypatch):
        monkeypatch.setattr(announcer_lib.importlib.util, "find_spec", lambda name: object())
        monkeypatch.setattr(announcer_lib.shutil, "which", lambda name: None)
        assert announcer_lib.get_piper_command() == [announcer_lib.sys.executable, "-m", "piper"]

    def test_piper_falls_back_to_program_on_path(self, monkeypatch):
        monkeypatch.setattr(announcer_lib.importlib.util, "find_spec", lambda name: None)
        monkeypatch.setattr(announcer_lib.shutil, "which", lambda name: f"/usr/local/bin/{name}")
        assert announcer_lib.get_piper_command() == ["piper"]

    def test_missing_piper_names_the_bots_python(self, monkeypatch):
        monkeypatch.setattr(announcer_lib.importlib.util, "find_spec", lambda name: None)
        monkeypatch.setattr(announcer_lib.shutil, "which", lambda name: None)
        missing = announcer_lib.missing_requirements(PIPER_EN)
        assert f"piper (install piper-tts with {announcer_lib.sys.executable} -m pip install piper-tts)" in missing

    def test_reports_missing_programs(self, monkeypatch):
        monkeypatch.setattr(announcer_lib.shutil, "which", lambda name: None)
        missing = announcer_lib.missing_requirements(ESPEAK_NG)
        assert "ffmpeg" in missing and "espeak-ng" in missing


def write_voice(tmp_path, file_name, family, name_english):
    model = tmp_path / f"{file_name}.onnx"
    model.write_bytes(b"")
    (tmp_path / f"{file_name}.onnx.json").write_text(
        json.dumps({"language": {"family": family, "name_english": name_english}}), encoding="utf-8")
    return model


class TestPiperSlots:
    @pytest.fixture(autouse=True)
    def voices(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PIPER_MODEL_EN", str(write_voice(tmp_path, "english", "en", "English")))
        monkeypatch.setenv("PIPER_MODEL_JP", str(write_voice(tmp_path, "japanese", "ja", "Japanese")))

    def test_announcements_use_each_voices_language(self):
        assert announcer_lib.get_announcement(PIPER_EN, "Game Night", "starting") == "The event Game Night is starting now."
        assert announcer_lib.get_announcement(PIPER_JP, "ゲームナイト", "starting") == "イベント「ゲームナイト」が今から始まります。"
        assert announcer_lib.get_announcement(PIPER_JP, "ゲームナイト", "ending") == "イベント「ゲームナイト」が終わります。"

    def test_other_languages_and_espeak_use_english(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PIPER_MODEL_JP", str(write_voice(tmp_path, "german", "de", "German")))
        assert announcer_lib.get_announcement(PIPER_JP, "x", "ending") == "The event x is ending now."
        assert announcer_lib.get_announcement(ESPEAK_NG, "x", "ending") == "The event x is ending now."

    def test_labels_name_the_language(self, monkeypatch):
        assert announcer_lib.get_engine_label(PIPER_EN) == "piper-en (English)"
        assert announcer_lib.get_engine_label(PIPER_JP) == "piper-jp (Japanese)"
        assert announcer_lib.get_engine_label(ESPEAK_NG) == "espeak-ng"
        monkeypatch.delenv("PIPER_MODEL_JP")
        assert announcer_lib.get_engine_label(PIPER_JP) == "piper-jp"

    def test_each_slot_uses_its_own_model(self, monkeypatch):
        monkeypatch.setattr(announcer_lib.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(announcer_lib.opus, "is_loaded", lambda: True)
        monkeypatch.delenv("PIPER_MODEL_JP")
        assert not any("PIPER_MODEL_EN" in item for item in announcer_lib.missing_requirements(PIPER_EN))
        assert any("PIPER_MODEL_JP" in item for item in announcer_lib.missing_requirements(PIPER_JP))

    def test_engine_change_while_queued_uses_new_language(self, voice):
        channel = make_voice_channel(voice.guild, 1, voice.log)

        async def go():
            announcer = Announcer()
            announcer.announce(voice.guild, channel, "ゲームナイト", "starting")
            announcer_lib.set_engine(voice.guild.id, PIPER_JP)
            await announcer.workers[voice.guild.id]
        run(go())
        assert voice.log[0] == ("synthesize", PIPER_JP, "イベント「ゲームナイト」が今から始まります。")


class TestScripts:
    def test_splits_japanese_and_latin(self):
        assert announcer_lib.split_scripts("イベント「Game Night」が今から始まります。") == [
            (JAPANESE, "イベント「"), (LATIN, "Game Night"), (JAPANESE, "」が今から始まります。")]

    def test_digits_spaces_and_punctuation_join_the_part_before(self):
        assert announcer_lib.split_scripts("Game Night 2: 大会!") == [(LATIN, "Game Night 2: "), (JAPANESE, "大会!")]

    def test_leading_neutral_text_joins_the_first_part(self):
        assert announcer_lib.split_scripts("2 ゲーム") == [(JAPANESE, "2 ゲーム")]
        assert announcer_lib.split_scripts("123") == [(LATIN, "123")]

    def test_full_and_half_width_forms_are_japanese(self):
        assert announcer_lib.split_scripts("ＡＢＣｶﾀｶﾅ！") == [(JAPANESE, "ＡＢＣｶﾀｶﾅ！")]


class TestVoiceParts:
    @pytest.fixture(autouse=True)
    def voices(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PIPER_MODEL_EN", str(write_voice(tmp_path, "english", "en", "English")))
        monkeypatch.setenv("PIPER_MODEL_JP", str(write_voice(tmp_path, "japanese", "ja", "Japanese")))
        monkeypatch.setattr(announcer_lib, "missing_requirements", lambda engine: [])

    def test_english_name_in_japanese_sentence_uses_english_voice(self):
        assert announcer_lib.get_voice_parts(PIPER_JP, "イベント「Game Night」が今から始まります。") == [
            (PIPER_JP, "イベント「"), (PIPER_EN, "Game Night"), (PIPER_JP, "」が今から始まります。")]

    def test_japanese_name_in_english_sentence_uses_japanese_voice(self):
        assert announcer_lib.get_voice_parts(PIPER_EN, "The event ゲームナイト is starting now.") == [
            (PIPER_EN, "The event"), (PIPER_JP, "ゲームナイト"), (PIPER_EN, "is starting now.")]

    def test_espeak_borrows_the_japanese_voice(self):
        assert announcer_lib.get_voice_parts(ESPEAK_NG, "The event 麻雀 is ending now.") == [
            (ESPEAK_NG, "The event"), (PIPER_JP, "麻雀"), (ESPEAK_NG, "is ending now.")]

    def test_one_script_is_one_part(self):
        assert announcer_lib.get_voice_parts(PIPER_JP, "イベント「ゲームナイト」が終わります。") == [
            (PIPER_JP, "イベント「ゲームナイト」が終わります。")]

    def test_without_a_voice_for_the_script_the_selected_voice_reads_it(self, monkeypatch):
        # The English slot isn't usable, e.g. its model is missing
        monkeypatch.setattr(announcer_lib, "missing_requirements", lambda engine: ["model"] if engine == PIPER_EN else [])
        assert announcer_lib.get_voice_parts(PIPER_JP, "イベント「Game Night」が今から始まります。") == [
            (PIPER_JP, "イベント「Game Night」が今から始まります。")]

    def test_mixed_announcement_is_joined(self, monkeypatch, tmp_path):
        calls = []

        async def fake_synthesize(engine, text, path):
            calls.append((engine, text))

        async def fake_concatenate(paths, path):
            calls.append(("concatenate", [p.rsplit("/", 1)[-1] for p in paths]))

        monkeypatch.setattr(announcer_lib, "synthesize", fake_synthesize)
        monkeypatch.setattr(announcer_lib, "concatenate", fake_concatenate)
        path = run(announcer_lib.synthesize_announcement(PIPER_JP, "イベント「Game Night」が終わります。", str(tmp_path)))
        assert calls == [(PIPER_JP, "イベント「"), (PIPER_EN, "Game Night"), (PIPER_JP, "」が終わります。"),
                         ("concatenate", ["part-0.wav", "part-1.wav", "part-2.wav"])]
        assert path == str(tmp_path / "announcement.wav")

    def test_single_voice_announcement_is_not_joined(self, monkeypatch, tmp_path):
        async def fake_synthesize(engine, text, path):
            pass

        async def fail_concatenate(paths, path):
            raise AssertionError("joined a single part")

        monkeypatch.setattr(announcer_lib, "synthesize", fake_synthesize)
        monkeypatch.setattr(announcer_lib, "concatenate", fail_concatenate)
        path = run(announcer_lib.synthesize_announcement(PIPER_EN, "The event x is ending now.", str(tmp_path)))
        assert path == str(tmp_path / "part-0.wav")


class TestErrorLine:
    def test_prefers_missing_module(self):
        stderr = ("Traceback (most recent call last):\n    import pyopenjtalk\n"
                  "ModuleNotFoundError: No module named 'pyopenjtalk'\n\nDuring handling...\n"
                  "wave.Error: # channels not specified\n")
        assert announcer_lib.get_error_line(stderr) == "ModuleNotFoundError: No module named 'pyopenjtalk'"

    def test_falls_back_to_last_line(self):
        assert announcer_lib.get_error_line("warming up\nRuntimeError: bad model\n") == "RuntimeError: bad model"
        assert announcer_lib.get_error_line("") == "no error output"


class TestEventAnnouncements:
    def test_start_and_end_are_announced(self, env, monkeypatch):
        announced = []
        monkeypatch.setattr(env.sched.announcer, "announce", lambda guild, channel, name, action: announced.append((guild, channel, name, action)))
        event, _ = make_created_event(env)
        run(event.start())
        run(event.end())
        assert announced == [(env.guild, env.voice_channel, event.name, "starting"),
                             (env.guild, env.voice_channel, event.name, "ending")]

    def test_external_locations_are_not_announced(self, env, monkeypatch):
        announced = []
        monkeypatch.setattr(env.sched.announcer, "announce", lambda guild, channel, name, action: announced.append(name))
        event, _ = make_created_event(env)
        event.location = "Park"
        event.announce("starting")
        assert announced == []


class TestTtsCommand:
    def interaction(self, guild):
        interaction = FakeInteraction(make_member(7, "a"))
        interaction.user.display_name = "a"
        interaction.guild = guild
        return interaction

    def test_sets_engine_with_public_confirmation(self, sched, monkeypatch):
        monkeypatch.setattr(announcer_lib, "missing_requirements", lambda engine: [])
        guild = FakeGuild()
        guild.name = "guild"
        interaction = self.interaction(guild)
        run(sched.tts_command.callback(interaction, PIPER_EN))
        assert announcer_lib.get_engine(guild.id) == PIPER_EN
        (kind, kwargs), = interaction.responses
        assert kind == "send_message" and not kwargs.get("ephemeral")
        assert "**piper-en**" in kwargs["content"]

    def test_none_turns_announcements_off(self, sched):
        guild = FakeGuild()
        guild.name = "guild"
        announcer_lib.set_engine(guild.id, ESPEAK_NG)
        interaction = self.interaction(guild)
        run(sched.tts_command.callback(interaction, NONE))
        assert guild.id not in announcer_lib.guild_engines
        assert "turned off" in interaction.responses[0][1]["content"]

    def test_refuses_engine_the_host_is_missing(self, sched, monkeypatch):
        monkeypatch.setattr(announcer_lib, "missing_requirements", lambda engine: ["espeak-ng"])
        guild = FakeGuild()
        interaction = self.interaction(guild)
        run(sched.tts_command.callback(interaction, ESPEAK_NG))
        assert announcer_lib.get_engine(guild.id) == NONE
        (kind, kwargs), = interaction.responses
        assert kwargs["ephemeral"] and "missing espeak-ng" in kwargs["content"]

    def test_refuses_outside_a_server(self, sched):
        interaction = self.interaction(None)
        run(sched.tts_command.callback(interaction, ESPEAK_NG))
        assert interaction.responses[0][1]["ephemeral"]

    def test_offers_each_engine(self, sched):
        (engine,) = sched.tts_command.parameters
        assert [choice.value for choice in engine.choices] == list(announcer_lib.ENGINES)


class TestPersistence:
    def test_engines_are_saved(self, sched):
        announcer_lib.set_engine(5, PIPER_EN)
        assert sched.client.events_dict["tts_engines"] == {"5": PIPER_EN}

    def test_engines_are_loaded_on_startup(self, sched, monkeypatch):
        monkeypatch.setattr(sched.client, "loaded_json", False)
        monkeypatch.setattr(sched.persist, "read", lambda: {"events": [], "tts_engines": {"5": PIPER_EN, "6": "bogus"}})
        run(sched.client.retrieve_events())
        assert announcer_lib.guild_engines == {5: PIPER_EN}

    def test_removed_guild_forgets_engine(self, sched):
        guild = FakeGuild()
        announcer_lib.set_engine(guild.id, ESPEAK_NG)
        run(sched.on_guild_remove(guild))
        assert guild.id not in announcer_lib.guild_engines
