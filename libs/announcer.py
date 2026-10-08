'''Voice announcements of events starting and ending, read out with a text-to-speech engine.'''

import os
import sys
import json
import shutil
import importlib.util
import asyncio
import logging
import tempfile
from typing import Optional, Union

from discord import Guild, VoiceChannel, StageChannel, FFmpegPCMAudio, opus

logger = logging.getLogger("Event Scheduler")

ESPEAK_NG = "espeak-ng"
PIPER_EN = "piper-en"
PIPER_JP = "piper-jp"
NONE = "none"
ENGINES = (ESPEAK_NG, PIPER_EN, PIPER_JP, NONE)

# Scripts that an announcement is split into, so each part can be read out by a voice for its script
JAPANESE = "japanese"
LATIN = "latin"

# Environment variable holding each piper slot's voice model (.onnx) path
PIPER_MODEL_VARIABLES = {PIPER_EN: "PIPER_MODEL_EN", PIPER_JP: "PIPER_MODEL_JP"}

# What's read out in each language, by the language family in the voice model's config.
# Voices in other languages read out the English announcements.
ANNOUNCEMENTS = {
    "en": {"starting": "The event {name} is starting now.",
           "ending": "The event {name} is ending now."},
    "ja": {"starting": "イベント「{name}」が今から始まります。",
           "ending": "イベント「{name}」が終わります。"},
}

CONNECT_TIMEOUT_SECONDS: int = 15
SYNTHESIZE_TIMEOUT_SECONDS: int = 30
PLAY_TIMEOUT_SECONDS: int = 30

# Engine each guild chose, guilds without one don't get announcements
guild_engines: dict[int, str] = {}


def get_engine(guild_id: int) -> str:
    return guild_engines.get(guild_id, NONE)


def set_engine(guild_id: int, engine: str) -> None:
    # Only guilds with announcements turned on are saved
    if engine == NONE:
        guild_engines.pop(guild_id, None)
    else:
        guild_engines[guild_id] = engine


def get_piper_model(engine: str) -> Optional[str]:
    """The path to the piper slot's voice model (.onnx) from its environment variable."""
    return os.getenv(PIPER_MODEL_VARIABLES[engine])


def get_piper_command() -> Optional[list[str]]:
    """
    Gets the command that runs piper: the piper-tts module of the Python running the bot, or else a piper program on PATH.
    None if neither is installed.
    """
    # pip can put the piper program in a folder that isn't on the bot's PATH, such as ~/.local/bin
    if importlib.util.find_spec("piper") is not None:
        return [sys.executable, "-m", "piper"]
    if shutil.which("piper") is not None:
        return ["piper"]
    return None


def get_language(engine: str) -> Optional[dict]:
    """
    Gets the language of a piper slot's voice from the config (.onnx.json) next to its model.

    Returns
    -------
    language: :class:`Optional[dict]`
        The config's language, with keys such as "family" ("en") and "name_english" ("English").
        None if the engine isn't piper, or the config can't be read.
    """
    if engine not in PIPER_MODEL_VARIABLES:
        return None
    model = get_piper_model(engine)
    if not model:
        return None
    try:
        with open(f"{model}.json", encoding="utf-8") as file:
            return json.load(file).get("language")
    except Exception:
        return None


def get_language_family(engine: str) -> str:
    """The language family of the engine's voice, e.g. "ja". espeak-ng and voices without a config read English."""
    language = get_language(engine) or {}
    return language.get("family") or "en"


def get_engine_label(engine: str) -> str:
    """The engine's name with its voice's language, e.g. "piper-en (English)"."""
    language = get_language(engine)
    if language and language.get("name_english"):
        return f"{engine} ({language['name_english']})"
    return engine


def get_announcement(engine: str, name: str, action: str) -> str:
    """Gets what the engine reads out for the event starting or ending, in its voice's language."""
    announcements = ANNOUNCEMENTS.get(get_language_family(engine), ANNOUNCEMENTS["en"])
    return announcements[action].format(name=name)


def get_script(char: str) -> Optional[str]:
    """Gets the script of the character, or None for digits, spaces, and punctuation that go with the text around them."""
    code = ord(char)
    # Japanese punctuation such as 「」。, kana, kanji, and full and half width forms
    if (0x3000 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF or 0xFF01 <= code <= 0xFF9F):
        return JAPANESE
    if char.isalpha():
        return LATIN
    return None


def split_scripts(text: str) -> list[tuple[str, str]]:
    """
    Splits the text into parts that are each in one script.
    Digits, spaces, and punctuation join the part before them, or the first part if they start the text.

    Returns
    -------
    parts: :class:`list[tuple[str, str]]`
        The (script, text) parts in order.
    """
    parts: list[list[str]] = []
    leading = ""
    for char in text:
        script = get_script(char)
        if script is None or (parts and parts[-1][0] == script):
            if parts:
                parts[-1][1] += char
            else:
                leading += char
        else:
            parts.append([script, leading + char])
            leading = ""
    if not parts:
        return [(LATIN, leading)] if leading else []
    return [(script, part) for script, part in parts]


def reads_script(engine: str, script: str) -> bool:
    """Indicates whether the engine's voice reads the script. Every voice other than Japanese ones reads Latin letters."""
    is_japanese = get_language_family(engine) == "ja"
    return is_japanese if script == JAPANESE else not is_japanese


def get_script_engine(engine: str, script: str) -> str:
    """
    Gets the engine to read text in the script with: the selected engine if its voice reads the script,
    otherwise another piper slot whose voice does, otherwise the selected engine anyway.
    """
    if reads_script(engine, script):
        return engine
    for other_engine in PIPER_MODEL_VARIABLES:
        if other_engine != engine and reads_script(other_engine, script) and not missing_requirements(other_engine):
            return other_engine
    return engine


def get_voice_parts(engine: str, text: str) -> list[tuple[str, str]]:
    """
    Splits the text into the parts each voice reads out, e.g. a Japanese sentence and the English event name in it.

    Returns
    -------
    parts: :class:`list[tuple[str, str]]`
        The (engine, text) parts in order. Neighboring parts read by the same engine are joined.
    """
    parts: list[list[str]] = []
    for script, part in split_scripts(text):
        part_engine = get_script_engine(engine, script)
        if parts and parts[-1][0] == part_engine:
            parts[-1][1] += part
        else:
            parts.append([part_engine, part])
    return [(part_engine, part.strip()) for part_engine, part in parts if part.strip()]


async def synthesize_announcement(engine: str, text: str, directory: str) -> str:
    """
    Writes the text read out by the engine, and other voices for parts in other scripts, to a WAV file in the directory.

    Returns
    -------
    path: :class:`str`
        The WAV file's path.
    """
    paths = []
    for index, (part_engine, part) in enumerate(get_voice_parts(engine, text)):
        path = os.path.join(directory, f"part-{index}.wav")
        await synthesize(part_engine, part, path)
        paths.append(path)
    if len(paths) == 1:
        return paths[0]
    path = os.path.join(directory, "announcement.wav")
    await concatenate(paths, path)
    return path


async def concatenate(paths: list[str], path: str) -> None:
    """Joins the WAV files into one at the path. Voices can have different sample rates, so each is resampled first."""
    args = ["ffmpeg", "-y", "-loglevel", "error"]
    for part_path in paths:
        args += ["-i", part_path]
    filters = "".join(f"[{index}:a]aresample=48000,aformat=sample_fmts=s16:channel_layouts=mono[a{index}];"
                      for index in range(len(paths)))
    filters += "".join(f"[a{index}]" for index in range(len(paths))) + f"concat=n={len(paths)}:v=0:a=1[out]"
    args += ["-filter_complex", filters, "-map", "[out]", path]
    await run_process("ffmpeg", args)


def missing_requirements(engine: str) -> list[str]:
    """
    Gets what the bot's host is missing to announce with the engine.

    Returns
    -------
    missing: :class:`list[str]`
        Descriptions of the missing requirements, empty if there are none.
    """
    if engine == NONE:
        return []
    missing = []
    try:
        import nacl  # noqa: F401
        import davey  # noqa: F401
    except ImportError:
        missing.append("voice support (install discord.py[voice])")
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg")
    # discord.py loads opus itself on first playback, this checks that it will be able to
    if not opus.is_loaded() and not opus._load_default():
        missing.append("libopus")
    if engine == ESPEAK_NG and shutil.which("espeak-ng") is None:
        missing.append("espeak-ng")
    if engine in PIPER_MODEL_VARIABLES:
        if get_piper_command() is None:
            missing.append(f"piper (install piper-tts with {sys.executable} -m pip install piper-tts)")
        model = get_piper_model(engine)
        if not model or not os.path.isfile(model):
            missing.append(f"a piper voice model (set {PIPER_MODEL_VARIABLES[engine]} to its .onnx file)")
    return missing


async def synthesize(engine: str, text: str, path: str) -> None:
    """Writes the text read out by the engine to a WAV file at the path."""
    # Text goes through stdin so an event name can't be read as a command line option
    if engine == ESPEAK_NG:
        args = ["espeak-ng", "--stdin", "-w", path]
    elif engine in PIPER_MODEL_VARIABLES:
        args = get_piper_command() + ["--model", get_piper_model(engine), "--output_file", path]
    else:
        raise ValueError(f"Unknown text-to-speech engine: {engine}")
    await run_process(engine, args, text)


async def run_process(name: str, args: list[str], text: str = "") -> None:
    """Runs the program with the text as its input, raising if it fails or takes longer than SYNTHESIZE_TIMEOUT_SECONDS."""
    process = await asyncio.create_subprocess_exec(*args,
                                                   stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.DEVNULL,
                                                   stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(text.encode()), SYNTHESIZE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise
    if process.returncode != 0:
        raise RuntimeError(f"{name} exited with {process.returncode}: {get_error_line(stderr.decode(errors='replace'))}")


def get_error_line(stderr: str) -> str:
    """
    Gets the most useful line of an engine's error output.
    A missing module, e.g. pyopenjtalk for Japanese piper voices, is often followed by less useful errors it caused.
    """
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("ModuleNotFoundError"):
            return line
    return lines[-1][:300] if lines else "no error output"


def has_listeners(channel: Union[VoiceChannel, StageChannel]) -> bool:
    """Indicates whether anyone other than bots is in the channel to hear an announcement."""
    return any(not getattr(member, "bot", False) for member in channel.members)


class Announcer:
    """
    Reads out announcements in voice channels.

    A bot can only be in one voice channel per guild, so each guild's announcements are queued and read out one at a time.

    Attributes
    ----------
    queues: :class:`dict[int, asyncio.Queue]`
        Each guild's queue of (channel, event name, action) announcements.
    workers: :class:`dict[int, asyncio.Task]`
        Each guild's task reading out its queue.
    """

    def __init__(self) -> None:
        self.queues: dict[int, asyncio.Queue] = {}
        self.workers: dict[int, asyncio.Task] = {}

    def announce(self, guild: Guild, channel: Union[VoiceChannel, StageChannel], name: str, action: str) -> None:
        """
        Queues reading out that the event is starting or ending in the guild's channel,
        unless announcements are off or nobody is there to hear it.

        Arguments
        ---------
        name: :class:`str`
            The event's name.
        action: :class:`str`
            "starting" or "ending".
        """
        if get_engine(guild.id) == NONE or not has_listeners(channel):
            return
        queue = self.queues.setdefault(guild.id, asyncio.Queue())
        queue.put_nowait((channel, name, action))
        worker = self.workers.get(guild.id)
        if worker is None or worker.done():
            self.workers[guild.id] = asyncio.create_task(self.work(guild))

    async def work(self, guild: Guild) -> None:
        """Reads out the guild's queued announcements, then leaves the voice channel."""
        queue = self.queues[guild.id]
        while True:
            while not queue.empty():
                channel, name, action = queue.get_nowait()
                try:
                    await self.read_out(guild, channel, name, action)
                except Exception as e:
                    logger.error(f'[{guild.name}] Could not announce {name} {action} in {channel.name}: {e!r}')
            await self.disconnect(guild)
            # Nothing awaits between this check and the task finishing, so announce() either
            # sees this task still running before the check or starts a new one after it
            if queue.empty():
                break

    async def read_out(self, guild: Guild, channel: Union[VoiceChannel, StageChannel], name: str, action: str) -> None:
        """Joins the guild's channel and reads out that the event is starting or ending."""
        # The engine may have changed while the announcement was queued
        engine = get_engine(guild.id)
        # Turned off, or everyone left, while the announcement was queued
        if engine == NONE or not has_listeners(channel):
            return
        text = get_announcement(engine, name, action)
        missing = missing_requirements(engine)
        if missing:
            raise RuntimeError(f"the host is missing {', '.join(missing)}")
        with tempfile.TemporaryDirectory() as directory:
            # Synthesized before joining so the bot doesn't sit silently in the channel
            path = await synthesize_announcement(engine, text, directory)
            voice_client = guild.voice_client
            if voice_client is None:
                voice_client = await channel.connect(timeout=CONNECT_TIMEOUT_SECONDS, self_deaf=True)
            elif voice_client.channel != channel:
                await voice_client.move_to(channel)
            if isinstance(channel, StageChannel):
                # Bots join stages in the audience
                await guild.me.edit(suppress=False)
            finished = asyncio.Event()
            loop = asyncio.get_running_loop()

            def after(error: Optional[Exception]) -> None:
                # Called from the audio player's thread
                if error is not None:
                    logger.error(f'[{guild.name}] Error playing announcement "{text}": {error!r}')
                loop.call_soon_threadsafe(finished.set)

            voice_client.play(FFmpegPCMAudio(path), after=after)
            try:
                await asyncio.wait_for(finished.wait(), PLAY_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                voice_client.stop()
                raise
        logger.info(f'[{guild.name}] Announced "{text}" in {channel.name} with {engine}')

    async def disconnect(self, guild: Guild) -> None:
        voice_client = guild.voice_client
        if voice_client is None:
            return
        try:
            await voice_client.disconnect()
        except Exception as e:
            logger.error(f"[{guild.name}] Error leaving voice channel after announcing: {e!r}")
