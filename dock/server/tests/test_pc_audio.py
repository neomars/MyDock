"""Le son du lecteur capturé sur le Pi pour sortir sur le PC : le puits virtuel, la capture, le routage de VLC et de MAME."""

from __future__ import annotations

import asyncio
import itertools
import math
import os
import shutil
import stat
import struct
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from mydock_agent.testing import AudioStack, audio_stack, eventually, have_audio_tools
from mydock_dock import player_session as ps
from mydock_dock.pc_audio import SINK, DemoSound, PcSound, usable_environment
from mydock_dock.player_session import Session

# --- de faux pactl et parec ------------------------------------------------------------------------------------------------


class Rig:
    """Un faux serveur de son : `pactl` lit et écrit un dossier d'état, `parec` rend le son qu'on lui a préparé."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.state = tmp / "state"
        self.state.mkdir()
        (self.state / "sinks").write_text("0\tauto_null\tPipeWire\n")
        (self.state / "default").write_text("auto_null\n")
        self.calls = tmp / "calls.log"
        self.parec_args = tmp / "parec.args"
        self.install_pactl()
        self.install_parec(b"")

    def script(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def install_pactl(self, *, fail: str | None = None) -> None:
        failure = f'echo "{fail}" >&2; exit 1\n' if fail else ""
        self.script(
            "pactl",
            f'''
            echo "pactl $*" >> "{self.calls}"
            {failure}
            case "$1" in
              list) cat "{self.state}/sinks" ;;
              load-module) echo "7\\t$3\\tPipeWire" | sed 's/sink_name=//' >> "{self.state}/sinks"; echo "$3" | sed 's/sink_name=//' > "{self.state}/default"; echo 536870913 ;;  # comme PipeWire : le nouveau puits devient la sortie par défaut
              get-default-sink) cat "{self.state}/default" ;;
              set-default-sink) echo "$2" > "{self.state}/default" ;;
              info) echo "Server Name: PulseAudio (on PipeWire 1.0.5)" ;;
            esac
            ''',
        )

    def install_parec(self, data: bytes, *, then_exit: bool = False, chunk: int = 0) -> None:
        """`parec` écrit `data` (par morceaux de `chunk` octets) puis, selon le cas, s'arrête ou attend d'être arrêté."""
        (self.tmp / "sound.raw").write_bytes(data)
        pace = (
            f"split -b {chunk} --filter='cat; sleep 0.02' \"{self.tmp}/sound.raw\""
            if chunk
            else f'cat "{self.tmp}/sound.raw"'
        )
        tail = "" if then_exit else "exec sleep 30\n"
        self.script(
            "parec",
            f'''
            echo "$@" > "{self.parec_args}"
            {pace}
            {tail}''',
        )

    def which(self, name: str) -> str | None:
        path = self.bin / name
        return str(path) if path.exists() else None

    def env(self) -> dict[str, str]:
        return {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "XDG_RUNTIME_DIR": str(self.tmp),
        }

    def default(self) -> str:
        return (self.state / "default").read_text().strip()

    def pactl_calls(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


class Wire:
    """Ce que PcSound envoie au dock : des messages JSON et des trames de son."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.frames: list[bytes] = []
        self.events: list[str] = []

    async def emit(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    async def frame(self, data: bytes) -> None:
        self.frames.append(data)

    def states(self) -> list[str]:
        return [m["state"] for m in self.messages if m.get("t") == "audio"]


def make_sound(rig: Rig, wire: Wire, **options: Any) -> PcSound:
    sound = PcSound(
        rig.env(),
        emit=wire.emit,
        send_frame=wire.frame,
        event=wire.events.append,
        which=rig.which,
        **options,
    )
    return sound


@pytest.fixture
def wire():
    return Wire()


# --- le cycle d'une lecture ----------------------------------------------------------------------------------------------------


async def test_a_playback_gets_a_virtual_sink_a_capture_and_the_dock_is_told(rig, wire):
    sound = make_sound(rig, wire)
    assert await sound.begin() is True
    assert wire.states() == ["capturing"]
    assert any(f"load-module module-null-sink sink_name={SINK}" in c for c in rig.pactl_calls())
    assert (
        rig.default() == SINK
    )  # les programmes qui passent par ALSA y vont aussi, le temps de la lecture
    await eventually(rig.parec_args.exists)  # le faux parec écrit ses arguments en démarrant
    args = rig.parec_args.read_text()
    assert f"--device={SINK}.monitor" in args
    assert "--format=s16le --rate=48000 --channels=2" in args
    assert "--raw" in args
    await sound.end()
    assert wire.states() == ["capturing", "stopped"]
    assert rig.default() == "auto_null"  # la sortie d'avant est rétablie


async def test_the_sink_is_created_once_and_reused(rig, wire):
    sound = make_sound(rig, wire)
    for _ in range(3):
        await sound.begin()
        await sound.end()
    loads = [c for c in rig.pactl_calls() if "load-module" in c]
    assert len(loads) == 1


async def test_the_captured_sound_reaches_the_dock_in_whole_samples(rig, wire):
    data = (
        bytes(range(256)) * 40
    )  # 10 240 octets, lus par morceaux de 4 001 : jamais un échantillon coupé
    rig.install_parec(data, chunk=4001)
    sound = make_sound(rig, wire)
    await sound.begin()
    await eventually(lambda: sum(map(len, wire.frames)) >= 10_236)
    await sound.end()
    assert all(len(frame) % 4 == 0 for frame in wire.frames)
    assert (
        b"".join(wire.frames) == data[: len(b"".join(wire.frames))]
    )  # dans l'ordre, sans trou ni doublon


async def test_nothing_happens_when_the_setting_is_off(rig, wire):
    sound = make_sound(rig, wire)
    sound.enabled = False
    assert await sound.begin() is False
    assert wire.messages == []
    assert rig.pactl_calls() == []  # pas un seul appel au serveur de son


async def test_a_new_playback_replaces_the_capture_of_the_old_one(rig, wire):
    sound = make_sound(rig, wire)
    await sound.begin()
    await sound.begin()
    assert wire.states() == ["capturing", "stopped", "capturing"]
    await sound.end()


async def test_ending_without_a_capture_says_nothing(rig, wire):
    sound = make_sound(rig, wire)
    await sound.end()
    assert wire.messages == []


async def test_the_capture_that_stops_by_itself_is_reported(rig, wire):
    rig.install_parec(b"\x00" * 3840, then_exit=True)
    sound = make_sound(rig, wire)
    await sound.begin()
    await eventually(lambda: "unavailable" in wire.states())
    reason = next(m["reason"] for m in wire.messages if m.get("state") == "unavailable")
    assert "s'est arrêtée" in reason
    await sound.end()


# --- ce qui manque sur le Pi ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["pactl", "parec"])
async def test_a_pi_without_the_sound_tools_says_what_to_install(rig, wire, missing):
    (rig.bin / missing).unlink()
    sound = make_sound(rig, wire)
    assert await sound.begin() is False
    state = [m for m in wire.messages if m.get("state") == "unavailable"]
    assert len(state) == 1
    assert missing in state[0]["reason"]
    assert "sudo apt install pipewire-pulse wireplumber pulseaudio-utils" in state[0]["reason"]
    assert not any(s == "capturing" for s in wire.states())
    assert any(
        "impossible" in e for e in wire.events
    )  # et le journal le garde (le diagnostic le relit)


async def test_a_pi_without_a_running_sound_server_says_so_with_the_reason(rig, wire):
    rig.install_pactl(fail="Connection failure: Connection refused")
    sound = make_sound(rig, wire)
    assert await sound.begin() is False
    reason = wire.messages[-1]["reason"]
    assert "Pas de serveur de son (PipeWire)" in reason
    assert "Connection refused" in reason
    assert "redémarre le Pi" in reason


async def test_a_failed_start_leaves_the_default_sink_as_it_was(rig, wire):
    (rig.bin / "parec").unlink()
    sound = make_sound(rig, wire)
    await sound.begin()
    assert rig.default() == "auto_null"


async def test_the_probe_names_the_sound_server_or_says_there_is_none(rig, wire):
    sound = make_sound(rig, wire)
    assert await sound.probe() == "PulseAudio (on PipeWire 1.0.5)"
    rig.install_pactl(fail="Connection refused")
    assert "aucun serveur de son joignable" in await sound.probe()


# --- le routage -----------------------------------------------------------------------------------------------------------------


def test_vlc_and_mame_are_pointed_at_the_virtual_sink(rig, wire):
    sound = make_sound(rig, wire)
    assert sound.route_env() == {"PULSE_SINK": SINK, "PIPEWIRE_NODE": SINK}


@pytest.mark.parametrize(
    ("advance", "args"), [(150, ["--audio-desync=-150"]), (0, []), (1000, ["--audio-desync=-1000"])]
)
def test_videos_advance_their_sound_by_the_travel_time(rig, wire, advance, args):
    sound = make_sound(rig, wire)
    sound.advance_ms = advance
    assert sound.vlc_args() == args


def test_the_sound_commands_do_not_inherit_a_stream_target():
    """pactl et parec ne doivent pas hériter d'une cible de flux (ils parleraient du mauvais puits)."""
    env = usable_environment(
        {"PULSE_SINK": "x", "PIPEWIRE_NODE": "y", "XDG_RUNTIME_DIR": "/run/user/1000"}
    )
    assert env == {
        "XDG_RUNTIME_DIR": "/run/user/1000",
        **({"HOME": os.environ["HOME"]} if "HOME" in os.environ else {}),
    }


# --- dans la session de l'écran -------------------------------------------------------------------------------------------------


class Mailbox:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    def states(self) -> list[str]:
        return [m["state"] for m in self.messages if m["t"] == "audio"]


class RecordingPlayback:
    """Un lecteur de remplacement : il note comment la session l'a lancé."""

    instances: list[RecordingPlayback] = []
    volume = 70
    finished = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args, self.kwargs = args, kwargs
        self.stopped = False
        RecordingPlayback.instances.append(self)

    async def start(self) -> None: ...

    async def stop(self) -> None:
        self.stopped = True

    async def status(self) -> dict[str, Any]:
        return {"state": "playing", "position": 1.0, "length": None}

    async def command(self, name: str, **params: Any) -> None: ...

    def error(self) -> str | None:
        return None


@pytest.fixture
def recorded(monkeypatch):
    RecordingPlayback.instances = []
    monkeypatch.setattr(ps, "VlcPlayback", RecordingPlayback)
    monkeypatch.setattr(ps, "MamePlayback", RecordingPlayback)
    monkeypatch.setattr(ps, "POLL_SECONDS", 0.05)


def make_session(tmp_path: Path, *, demo: bool = False, **kw: Any) -> Session:
    s = Session(
        "ws://unused", demo=demo, vlc="/usr/bin/vlc", smbclient="", mame="/usr/games/mame",
        runtime_dir=tmp_path, extra_vlc_args=["--option-de-l-utilisateur"], env={"HOME": str(tmp_path), "XDG_RUNTIME_DIR": str(tmp_path)}, **kw,
    )  # fmt: skip
    s.windows = type(
        "W",
        (),
        {"focus": staticmethod(lambda *_: asyncio.sleep(0, result=True)), "available": True},
    )()
    s.touch = type(
        "T",
        (),
        {
            "start": staticmethod(lambda: asyncio.sleep(0)),
            "stop": staticmethod(lambda: asyncio.sleep(0)),
            "available": True,
        },
    )()
    s.inputs = type(
        "I",
        (),
        {
            "start": lambda *_: None,
            "arm": lambda *_: None,
            "stop": lambda *_: None,
            "touch_seen": True,
            "summary": lambda *_: "",
        },
    )()
    s._loop = asyncio.get_running_loop()
    return s


def play(job: int = 1, **kw: Any) -> dict[str, Any]:
    return {
        "t": "cmd",
        "name": "play",
        "job": job,
        "url": "smb://nas/partage/film.mkv",
        "volume": 70,
        **kw,
    }


def game(job: int = 1) -> dict[str, Any]:
    return play(job, engine="mame", game="pong", title="Pong", rompath="/var/lib/mydock/roms")


class QuietSound(DemoSound):
    """Le vrai cycle de PcSound (messages, routage), sans serveur de son."""


async def test_vlc_plays_into_the_virtual_sink_and_advances_its_sound(tmp_path, recorded):
    s, box = make_session(tmp_path, sound=None), Mailbox()
    s.sound = QuietSound({}, emit=s._emit, send_frame=s._emit_sound)
    await s.handle(play(), box)
    (vlc,) = RecordingPlayback.instances
    assert vlc.kwargs["env"]["PULSE_SINK"] == SINK
    assert vlc.kwargs["env"]["PIPEWIRE_NODE"] == SINK
    assert vlc.kwargs["extra"] == [
        "--audio-desync=-150",
        "--option-de-l-utilisateur",
    ]  # les options de l'utilisateur gardent le dernier mot
    assert box.states() == ["capturing"]
    await s.handle({"t": "cmd", "name": "stop", "job": 1}, box)
    assert box.states() == ["capturing", "stopped"]


async def test_mame_plays_into_the_virtual_sink_too(tmp_path, recorded):
    s, box = make_session(tmp_path), Mailbox()
    s.sound = QuietSound({}, emit=s._emit, send_frame=s._emit_sound)
    await s.handle(game(), box)
    (mame,) = RecordingPlayback.instances
    assert mame.kwargs["env"]["PULSE_SINK"] == SINK
    assert box.states() == ["capturing"]
    await s.handle({"t": "cmd", "name": "stop", "job": 1}, box)
    assert box.states() == ["capturing", "stopped"]


async def test_with_the_sound_on_the_pi_nothing_is_rerouted(tmp_path, recorded):
    s, box = make_session(tmp_path), Mailbox()
    s.sound = QuietSound({}, emit=s._emit, send_frame=s._emit_sound)
    await s.handle({"t": "cmd", "name": "configure", "pc_audio": False}, box)
    await s.handle(play(), box)
    (vlc,) = RecordingPlayback.instances
    assert "PULSE_SINK" not in vlc.kwargs["env"]
    assert vlc.kwargs["extra"] == ["--option-de-l-utilisateur"]
    assert box.states() == []


async def test_the_dock_configures_the_sound(tmp_path, recorded):
    s, box = make_session(tmp_path), Mailbox()
    await s.handle(
        {"t": "cmd", "name": "configure", "pc_audio": False, "audio_advance_ms": 400}, box
    )
    assert (s.sound.enabled, s.sound.advance_ms) == (False, 400)
    for bad in (
        {"pc_audio": "oui"},
        {"audio_advance_ms": -1},
        {"audio_advance_ms": 5000},
        {"audio_advance_ms": True},
        {"audio_advance_ms": "x"},
    ):
        await s.handle({"t": "cmd", "name": "configure", **bad}, box)
    assert (s.sound.enabled, s.sound.advance_ms) == (False, 400)  # rien de douteux n'est pris


async def test_a_playback_that_fails_to_start_stops_the_capture(tmp_path, monkeypatch, recorded):
    class Failing(RecordingPlayback):
        async def start(self) -> None:
            raise RuntimeError("VLC ne démarre pas")

    monkeypatch.setattr(ps, "VlcPlayback", Failing)
    s, box = make_session(tmp_path), Mailbox()
    s.sound = QuietSound({}, emit=s._emit, send_frame=s._emit_sound)
    await s.handle(play(), box)
    assert box.states() == ["capturing", "stopped"]
    assert [m["t"] for m in box.messages if m["t"] == "ended"] == ["ended"]


async def test_the_capture_ends_when_the_playback_ends_by_itself(tmp_path, recorded):
    s, box = make_session(tmp_path), Mailbox()
    s.sound = QuietSound({}, emit=s._emit, send_frame=s._emit_sound)
    await s.handle(play(), box)
    (vlc,) = RecordingPlayback.instances
    vlc.finished = True
    await eventually(lambda: box.states() == ["capturing", "stopped"])


async def test_a_new_playback_starts_a_new_capture_after_stopping_the_old_one(tmp_path, recorded):
    s, box = make_session(tmp_path), Mailbox()
    s.sound = QuietSound({}, emit=s._emit, send_frame=s._emit_sound)
    await s.handle(play(1), box)
    await s.handle(play(2), box)
    assert box.states() == ["capturing", "stopped", "capturing"]
    await s.handle({"t": "cmd", "name": "stop", "job": 2}, box)


async def test_in_demonstration_the_cycle_is_the_same_without_any_capture(tmp_path):
    s, box = make_session(tmp_path, demo=True), Mailbox()
    assert isinstance(s.sound, DemoSound)
    await s.handle(play(), box)
    assert box.states() == ["capturing"]
    await s.handle({"t": "cmd", "name": "stop", "job": 1}, box)
    assert box.states() == ["capturing", "stopped"]


async def test_frames_are_dropped_when_the_dock_is_not_there(tmp_path):
    s = make_session(tmp_path)
    await s._emit_sound(b"\x00" * 3840)  # pas de liaison : sans effet, sans erreur
    sent: list[bytes] = []

    async def send_bytes(frame: bytes) -> None:
        sent.append(frame)

    s._send_bytes = send_bytes
    await s._emit_sound(b"\x01" * 3840)
    assert sent == [b"\x01" * 3840]


# --- le vrai serveur de son -------------------------------------------------------------------------------------------------------


def tone(seconds: float, hertz: float = 440.0) -> bytes:
    count = int(48_000 * seconds)
    return b"".join(
        struct.pack("<hh", s, s)
        for s in (int(12_000 * math.sin(2 * math.pi * hertz * n / 48_000)) for n in range(count))
    )


def measure(raw: bytes) -> tuple[float, float]:
    """(valeur efficace, fréquence en Hz) du canal gauche, une fois le son arrivé."""
    values = struct.unpack(f"<{len(raw) // 2}h", raw[: len(raw) // 2 * 2])[0::2]
    loud = [i for i, v in enumerate(values) if abs(v) > 500]
    if len(loud) < 4800:
        return 0.0, 0.0
    window = values[loud[0] : loud[0] + 24_000]
    rms = math.sqrt(sum(v * v for v in window) / len(window))
    crossings = sum(1 for a, b in itertools.pairwise(window) if a < 0 <= b)
    return rms, crossings * 48_000 / len(window)


@pytest.fixture(scope="module")
def stack():
    if not have_audio_tools():
        pytest.skip("PipeWire, WirePlumber et pactl requis")
    with audio_stack() as running:
        yield running


async def test_a_real_tone_played_into_the_sink_is_captured_and_handed_to_the_dock(
    stack: AudioStack, wire
):
    stack.new_sink(
        "sortie_hdmi"
    )  # la sortie du Pi d'avant (sans carte son ici, un puits virtuel en tient lieu)
    stack.pactl("set-default-sink", "sortie_hdmi")
    sound = PcSound(stack.env, emit=wire.emit, send_frame=wire.frame)
    assert await sound.begin() is True
    assert stack.pactl("get-default-sink").strip() == SINK
    player = subprocess.Popen(  # noqa: ASYNC220 — un essai, le processus est surveillé plus bas
        ["pacat", "--playback", "--raw", "--format=s16le", "--rate=48000", "--channels=2", f"--device={SINK}"],
        stdin=subprocess.PIPE, env=stack.env, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    assert player.stdin is not None
    player.stdin.write(tone(2.0))
    player.stdin.close()
    await asyncio.sleep(2.5)
    await sound.end()
    player.wait(timeout=5)
    rms, hertz = measure(b"".join(wire.frames))
    assert rms > 3000, (
        f"le son n'est pas arrivé (valeur efficace {rms:.0f}, {sum(map(len, wire.frames))} octets)"
    )
    assert 420 < hertz < 460, f"la note n'est pas la bonne ({hertz:.0f} Hz)"
    assert all(len(frame) % 4 == 0 for frame in wire.frames)
    assert stack.pactl("get-default-sink").strip() == "sortie_hdmi"  # la sortie d'avant est revenue
    assert "MyDock - son du lecteur" not in stack.pactl("list", "source-outputs")  # parec est parti


async def test_the_sink_survives_between_two_playbacks_and_is_not_created_twice(
    stack: AudioStack, wire
):
    sound = PcSound(stack.env, emit=wire.emit, send_frame=wire.frame)
    for _ in range(2):
        assert await sound.begin() is True
        await sound.end()
    assert stack.pactl("list", "short", "sinks").count(SINK) == 1


@pytest.mark.skipif(
    os.geteuid() == 0 or not shutil.which("cvlc") or not shutil.which("ffmpeg"),
    reason="VLC et ffmpeg requis, et un utilisateur ordinaire (VLC refuse de tourner en root)",
)
async def test_real_vlc_with_the_routing_and_the_advance_plays_into_the_sink(
    stack: AudioStack, wire, tmp_path
):
    """VLC réel : l'environnement et l'option de la session le font jouer dans le puits, et il accepte l'avance du son."""
    wav = tmp_path / "la.wav"
    subprocess.run(  # noqa: ASYNC221
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(wav),
        ],
        check=True,
    )
    sound = PcSound(stack.env, emit=wire.emit, send_frame=wire.frame)
    assert await sound.begin() is True
    vlc = subprocess.Popen(  # noqa: ASYNC220 — un essai, le processus est surveillé plus bas
        ["cvlc", "--intf", "dummy", "--play-and-exit", *sound.vlc_args(), str(wav)],
        env={**stack.env, **sound.route_env()}, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )  # fmt: skip
    await asyncio.sleep(5.5)
    await sound.end()
    _, errors = vlc.communicate(timeout=10)
    assert b"audio-desync" not in errors.lower() or b"unknown" not in errors.lower()
    rms, hertz = measure(b"".join(wire.frames))
    assert rms > 1500, f"VLC n'a pas joué dans le puits (valeur efficace {rms:.0f})"
    assert 420 < hertz < 460
