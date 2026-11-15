"""Le son du dock sur le PC : l'accès au flux, le format, les bornes, et le vrai son de bout en bout (PipeWire sans carte son)."""

from __future__ import annotations

import asyncio
import itertools
import json
import math
import struct

import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus

from mydock_agent import audio_in
from mydock_agent.audio_in import (
    HELLO,
    MAX_CHUNK_BYTES,
    PACAT_ARGS,
    AudioIn,
    AudioUnavailable,
    DemoSink,
    PacatSink,
)
from mydock_agent.testing import (
    AudioStack,
    audio_stack,
    eventually,
    have_audio_tools,
    recv_json,
    running_agent,
    wait_closed,
)

CHUNK = b"\x01\x02\x03\x04" * 960  # 20 ms de son brut (48 kHz, stéréo, 16 bits)


class FakeSink:
    """Un lecteur qu'on commande : il note ce qu'on lui écrit."""

    instances: list[FakeSink] = []

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.started = False
        self.stopped = False
        self.is_alive = True
        self.pending = 0
        self.last_failure = ""
        self.fail_to_start: str | None = None
        FakeSink.instances.append(self)

    async def start(self) -> None:
        if self.fail_to_start:
            raise AudioUnavailable(self.fail_to_start)
        self.started = True

    def backlog(self) -> int:
        return self.pending

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    @property
    def alive(self) -> bool:
        return self.is_alive

    async def stop(self) -> None:
        self.stopped = True

    def failure(self) -> str:
        return self.last_failure


@pytest.fixture(autouse=True)
def _fresh_sinks():
    FakeSink.instances = []


def hello(**changes) -> str:
    return json.dumps({**HELLO, **changes})


async def open_stream(agent, **options):
    ws = await agent.connect_audio().__aenter__()
    await ws.send(hello())
    assert (await recv_json(ws))["t"] == "ready"
    return ws


# --- l'accès --------------------------------------------------------------------------------------------------------------


async def test_the_agent_says_in_its_hello_that_it_can_play_the_docks_sound():
    async with running_agent(audio_in=AudioIn(FakeSink)) as agent, agent.connect() as ws:
        assert (await recv_json(ws))["agent"]["audio_in"] is True
    async with running_agent() as agent, agent.connect() as ws:
        assert (await recv_json(ws))["agent"]["audio_in"] is False


async def test_the_audio_door_needs_the_token():
    async with running_agent(audio_in=AudioIn(FakeSink)) as agent:
        with pytest.raises(
            InvalidStatus
        ):  # refusé avant même l'ouverture (le jeton n'est pas présenté)
            async with agent.connect_audio(headers={}):
                pass
        with pytest.raises(InvalidStatus):
            async with agent.connect_audio(token="faux" * 12):
                pass
    assert FakeSink.instances == []  # aucun lecteur n'a été lancé pour un refusé


async def test_wrong_tokens_count_and_block_the_address_like_on_the_main_door():
    async with running_agent(audio_in=AudioIn(FakeSink), max_failures=3) as agent:
        for _ in range(3):
            with pytest.raises(InvalidStatus):
                async with agent.connect_audio(token="faux" * 12):
                    pass
        with pytest.raises(
            InvalidStatus
        ):  # même le bon jeton ne passe plus : l'adresse est bloquée
            async with agent.connect_audio():
                pass


async def test_without_an_audio_player_configured_the_door_is_closed_even_with_the_token():
    async with running_agent() as agent:
        with pytest.raises(InvalidStatus):
            async with agent.connect_audio():
                pass


# --- le flux --------------------------------------------------------------------------------------------------------------


async def test_sound_frames_reach_the_player_in_order_and_the_player_is_stopped_at_the_end():
    sound = AudioIn(FakeSink)
    async with running_agent(audio_in=sound) as agent:
        ws = await open_stream(agent)
        frames = [bytes([n]) * 3840 for n in range(1, 6)]
        for frame in frames:
            await ws.send(frame)
        sink = FakeSink.instances[0]
        await eventually(lambda: len(sink.writes) == 5)
        assert sink.writes == frames
        assert sound.active
        await ws.close()
        await eventually(lambda: sink.stopped)
        await eventually(lambda: not sound.active)


@pytest.mark.parametrize(
    "announce",
    [
        hello(rate=44100),
        hello(ch=1),
        hello(fmt="f32le"),
        hello(v=2),
        json.dumps({**HELLO, "extra": 1}),
        json.dumps({"t": "audio"}),
        json.dumps([1, 2, 3]),
        "pas du json",
        "x" * 600,
    ],
)
async def test_any_format_but_the_one_the_agent_plays_is_refused(announce):
    async with running_agent(audio_in=AudioIn(FakeSink)) as agent, agent.connect_audio() as ws:
        await ws.send(announce)
        assert (await recv_json(ws))["t"] == "err"
        closed = await wait_closed(ws)
        assert closed.rcvd is not None
        assert closed.rcvd.code == 1008
    assert FakeSink.instances == []  # le lecteur n'a jamais été lancé


async def test_a_binary_message_instead_of_the_announcement_is_refused():
    async with running_agent(audio_in=AudioIn(FakeSink)) as agent, agent.connect_audio() as ws:
        await ws.send(CHUNK)
        assert (await wait_closed(ws)).rcvd.code == 1008  # type: ignore[union-attr]
    assert FakeSink.instances == []


async def test_a_dock_that_never_announces_is_dropped():
    sound = AudioIn(FakeSink, hello_timeout=0.2)
    async with running_agent(audio_in=sound) as agent, agent.connect_audio() as ws:
        assert (await wait_closed(ws)).rcvd.code == 1008  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "frame",
    [b"\x00" * 3, b"\x00" * 3841, b"\x00" * (MAX_CHUNK_BYTES + 4)],
    ids=["3", "3841", "trop gros"],
)
async def test_a_frame_that_is_not_whole_samples_or_is_too_big_ends_the_stream(frame):
    async with running_agent(audio_in=AudioIn(FakeSink)) as agent:
        ws = await open_stream(agent)
        await ws.send(frame)
        await wait_closed(ws)
        sink = FakeSink.instances[0]
        assert sink.writes == []
        assert sink.stopped


async def test_text_after_the_announcement_changes_nothing():
    async with running_agent(audio_in=AudioIn(FakeSink)) as agent:
        ws = await open_stream(agent)
        await ws.send(
            json.dumps({"t": "cmd", "run": "rm -rf /"})
        )  # jamais interprété : ce n'est pas un canal de commandes
        await ws.send(CHUNK)
        sink = FakeSink.instances[0]
        await eventually(lambda: sink.writes == [CHUNK])
        await ws.close()


async def test_frames_are_dropped_not_queued_when_the_pc_falls_behind():
    async with running_agent(audio_in=AudioIn(FakeSink, max_backlog=10_000)) as agent:
        ws = await open_stream(agent)
        sink = FakeSink.instances[0]
        await ws.send(CHUNK)
        await eventually(lambda: len(sink.writes) == 1)
        sink.pending = 50_000  # le lecteur n'a pas fini de jouer
        for _ in range(5):
            await ws.send(CHUNK)
        await asyncio.sleep(0.2)
        assert len(sink.writes) == 1  # jetées : le son ne prend pas de retard
        sink.pending = 0
        await ws.send(CHUNK)
        await eventually(lambda: len(sink.writes) == 2)
        await ws.close()


async def test_a_player_that_dies_ends_the_stream():
    async with running_agent(audio_in=AudioIn(FakeSink)) as agent:
        ws = await open_stream(agent)
        sink = FakeSink.instances[0]
        sink.is_alive = False
        sink.last_failure = "Connection refused"
        await ws.send(CHUNK)
        await wait_closed(ws)
        assert sink.writes == []
        assert sink.stopped


async def test_no_sound_server_is_told_to_the_dock_with_the_reason():
    class Unavailable(FakeSink):
        def __init__(self) -> None:
            super().__init__()
            self.fail_to_start = "Le serveur de son du PC ne répond pas."

    async with running_agent(audio_in=AudioIn(Unavailable)) as agent, agent.connect_audio() as ws:
        await ws.send(hello())
        answer = await recv_json(ws)
        assert answer["t"] == "err"
        assert answer["code"] == "unavailable"
        assert "serveur de son" in answer["msg"]
        assert (await wait_closed(ws)).rcvd.code == 1011  # type: ignore[union-attr]


async def test_a_new_stream_replaces_the_old_one():
    """Le dock qui revient (réseau coupé, redémarrage) ne reste pas dehors parce que son ancienne connexion semble vivante."""
    sound = AudioIn(FakeSink)
    async with running_agent(audio_in=sound) as agent:
        first = await open_stream(agent)
        old = FakeSink.instances[0]
        second = await open_stream(agent)
        new = FakeSink.instances[1]
        await wait_closed(first)
        await eventually(lambda: old.stopped)
        await second.send(CHUNK)
        await eventually(lambda: new.writes == [CHUNK])
        assert not new.stopped
        assert sum(not s.stopped for s in FakeSink.instances) == 1  # un seul lecteur à la fois
        await second.close()


async def test_a_stream_without_frames_is_closed_and_its_player_stopped():
    async with running_agent(audio_in=AudioIn(FakeSink, idle=0.3)) as agent:
        ws = await open_stream(agent)
        await wait_closed(ws)
        assert FakeSink.instances[0].stopped


async def test_a_keepalive_from_the_dock_keeps_a_silent_stream_open():
    """Une vidéo en pause : pas de trame, mais le dock dit qu'il est là ; le flux n'est pas fermé pour inactivité."""
    async with running_agent(audio_in=AudioIn(FakeSink, idle=0.4)) as agent:
        ws = await open_stream(agent)
        for _ in range(8):  # 1,2 s sans une trame de son, plus de trois fois le délai d'inactivité
            await ws.send(json.dumps({"t": "keepalive"}))
            await asyncio.sleep(0.15)
        assert not FakeSink.instances[0].stopped
        assert FakeSink.instances[0].writes == []
        await ws.send(CHUNK)
        await eventually(lambda: FakeSink.instances[0].writes == [CHUNK])
        await ws.close()


async def test_the_demo_agent_counts_the_sound_and_plays_nothing():
    sinks: list[DemoSink] = []

    def make() -> DemoSink:
        sinks.append(DemoSink())
        return sinks[-1]

    async with running_agent(audio_in=AudioIn(make)) as agent:
        ws = await open_stream(agent)
        for _ in range(3):
            await ws.send(CHUNK)
        await eventually(lambda: sinks[0].bytes == 3 * len(CHUNK))
        await ws.close()


# --- pacat ----------------------------------------------------------------------------------------------------------------


async def test_the_command_line_of_the_player_is_fixed(monkeypatch):
    """Rien de ce que dit le dock n'entre dans la ligne de commande de pacat."""
    seen: dict[str, object] = {}

    class FakeProcess:
        returncode = None
        stdin = None
        stderr = None

        async def wait(self):
            return 0

    async def spawn(*argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(audio_in.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(audio_in.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(PacatSink, "_read_stderr", lambda self: asyncio.sleep(0))
    sink = PacatSink()
    await sink.start()
    assert seen["argv"] == ("/usr/bin/pacat", *PACAT_ARGS)
    assert seen["env"]["LC_ALL"] == "C"  # type: ignore[index]
    assert "--playback" in PACAT_ARGS
    assert not any(
        arg.startswith("--device") for arg in PACAT_ARGS
    )  # la sortie par défaut du PC, choisie par l'utilisateur


async def test_a_pc_without_pacat_says_what_to_install(monkeypatch):
    monkeypatch.setattr(audio_in.shutil, "which", lambda name: None)
    with pytest.raises(AudioUnavailable) as raised:
        await PacatSink().start()
    assert "pulseaudio-utils" in str(raised.value)


# --- le vrai son ----------------------------------------------------------------------------------------------------------


def tone(seconds: float, hertz: float = 440.0) -> bytes:
    """Un la de 440 Hz (brut, s16le, 48 kHz, stéréo), assez fort pour être mesuré."""
    count = int(48_000 * seconds)
    samples = (int(12_000 * math.sin(2 * math.pi * hertz * n / 48_000)) for n in range(count))
    return b"".join(struct.pack("<hh", s, s) for s in samples)


def measure(raw: bytes) -> tuple[float, float]:
    """(valeur efficace, fréquence estimée en Hz) du canal gauche d'un enregistrement brut."""
    values = struct.unpack(f"<{len(raw) // 2}h", raw[: len(raw) // 2 * 2])[0::2]
    # La partie qui compte : on saute le début (le son arrive après le démarrage du flux)
    loud = [v for v in values if abs(v) > 500]
    if len(loud) < 4800:
        return 0.0, 0.0
    start = values.index(loud[0])
    window = values[start : start + 24_000]
    rms = math.sqrt(sum(v * v for v in window) / len(window))
    crossings = sum(1 for a, b in itertools.pairwise(window) if a < 0 <= b)
    return rms, crossings * 48_000 / len(window)


@pytest.fixture(scope="module")
def stack():
    if not have_audio_tools():
        pytest.skip("PipeWire, WirePlumber et pactl requis")
    with audio_stack() as running:
        yield running


async def test_a_real_tone_goes_from_the_dock_through_the_agent_to_the_pc_speakers(
    stack: AudioStack,
):
    """Le circuit réel : trames du dock -> agent -> pacat -> serveur de son -> puits du PC (ici un puits virtuel)."""
    stack.new_sink("pc_speakers")
    env = {**stack.env, "PULSE_SINK": "pc_speakers"}  # « la sortie par défaut du PC »
    sound = AudioIn(lambda: PacatSink(env=env))
    async with running_agent(audio_in=sound) as agent:
        recording = stack.capture("pc_speakers.monitor", 4)
        ws = await open_stream(agent)
        frame = tone(0.02)
        for _ in range(100):  # deux secondes, par trames de 20 ms, au rythme du temps réel
            await ws.send(frame)
            await asyncio.sleep(0.02)
        await ws.close()
        raw, _ = await asyncio.to_thread(recording.communicate)
    rms, hertz = measure(raw)
    assert rms > 3000, (
        f"le son n'est pas arrivé (valeur efficace {rms:.0f}, {len(raw)} octets enregistrés)"
    )
    assert 420 < hertz < 460, f"la note n'est pas la bonne ({hertz:.0f} Hz)"


async def test_the_stream_shows_up_on_the_pc_as_an_application_with_its_own_volume(
    stack: AudioStack,
):
    stack.new_sink("pc_speakers2")
    env = {**stack.env, "PULSE_SINK": "pc_speakers2"}
    async with running_agent(audio_in=AudioIn(lambda: PacatSink(env=env))) as agent:
        ws = await open_stream(agent)
        for _ in range(10):
            await ws.send(CHUNK)
            await asyncio.sleep(0.02)
        listing = await asyncio.to_thread(stack.pactl, "list", "sink-inputs")
        await ws.close()
    assert "MyDock - son du Raspberry Pi" in listing


async def test_closing_the_stream_stops_pacat(stack: AudioStack):
    stack.new_sink("pc_speakers3")
    env = {**stack.env, "PULSE_SINK": "pc_speakers3"}
    async with running_agent(audio_in=AudioIn(lambda: PacatSink(env=env))) as agent:
        ws = await open_stream(agent)
        await ws.send(CHUNK)
        assert "MyDock" in await asyncio.to_thread(stack.pactl, "list", "sink-inputs")
        await ws.close()
        await eventually(lambda: "MyDock" not in stack.pactl("list", "sink-inputs"), timeout=5)


async def test_a_pc_whose_sound_server_is_down_says_so(tmp_path):
    """pacat sans serveur de son (ici : un dossier d'exécution où rien ne tourne) : le dock en est informé, avec la raison."""
    if not have_audio_tools():
        pytest.skip("pacat requis")
    env = {
        "PATH": "/usr/bin:/bin",
        "XDG_RUNTIME_DIR": str(tmp_path),
        "HOME": str(tmp_path),
        "PULSE_SERVER": f"unix:{tmp_path}/absent",
    }
    async with (
        running_agent(audio_in=AudioIn(lambda: PacatSink(env=env))) as agent,
        agent.connect_audio() as ws,
    ):
        await ws.send(hello())
        answer = await recv_json(ws, timeout=5)
        assert answer["t"] == "err"
        assert answer["code"] == "unavailable"
        with pytest.raises(ConnectionClosed):
            await asyncio.wait_for(ws.recv(), 3)
