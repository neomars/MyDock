"""Le son du Pi vers le PC : le relais du dock, ses pannes, ses bornes, et le circuit complet avec un vrai agent."""

from __future__ import annotations

import asyncio
import contextlib
import json
from types import SimpleNamespace

import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus

from mydock_agent.audio_in import AudioIn
from mydock_agent.testing import TOKEN, running_agent
from mydock_common.audio import HELLO, MAX_CHUNK_BYTES, RATE, SAMPLE_BYTES
from mydock_dock import audio_relay
from mydock_dock.agent_link import LinkSettings
from mydock_dock.audio_relay import MAX_QUEUE_BYTES, AudioRelay, RelayState
from mydock_dock.config import ConfigError, DockConfig
from mydock_dock.player import HelperConn, PlayerHub
from mydock_dock.testing import call, eventually, running_dock, snapshot_of
from mydock_dock.validation import ValidationFailure

FRAME = b"\x01\x02\x03\x04" * 960  # 20 ms de son brut


# --- un faux PC et un faux lien, pour provoquer chaque panne -------------------------------------------------------------


class FakeSocket:
    """La connexion du relais vers l'agent, commandée par le test."""

    def __init__(self, answer: str | None = None, *, hold_sends: bool = False) -> None:
        self.sent: list[bytes | str] = []
        self.closed = False
        self.answer = answer if answer is not None else json.dumps({"t": "ready", "latency_ms": 80})
        self.gate = asyncio.Event()
        if not hold_sends:
            self.gate.set()
        self.lost = asyncio.Event()

    async def send(self, data: bytes | str) -> None:
        if self.lost.is_set():
            raise ConnectionClosed(None, None)
        if isinstance(data, bytes):  # seul le son attend le feu vert : l'annonce passe toujours
            await self.gate.wait()
        self.sent.append(data)

    async def recv(self) -> str:
        return self.answer

    async def wait_closed(self) -> None:
        await self.lost.wait()

    async def close(self) -> None:
        self.closed = True

    @property
    def frames(self) -> list[bytes]:
        return [m for m in self.sent if isinstance(m, bytes)]


class FakeLink:
    def __init__(self) -> None:
        self.connected = True
        self.status = SimpleNamespace(agent={"audio_in": True}, resolved="10.0.0.2")
        self.settings = LinkSettings("pc.lan", 8765, TOKEN)


class Calls:
    """Ce que le relais demande à `connect` : l'adresse, les en-têtes, et la prochaine connexion à donner."""

    def __init__(self) -> None:
        self.opened: list[tuple[str, dict]] = []
        self.sockets: list[FakeSocket] = []
        self.next: list[object] = []

    async def __call__(self, url: str, **options):
        self.opened.append((url, options))
        item = self.next.pop(0) if self.next else FakeSocket()
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, FakeSocket):
            self.sockets.append(item)
        return item


@pytest.fixture
def parts(monkeypatch):
    monkeypatch.setattr(audio_relay, "RETRY_SECONDS", (0.05,))
    monkeypatch.setattr(audio_relay, "POLL_SECONDS", 0.05)
    link, calls, changes = FakeLink(), Calls(), []
    relay = AudioRelay(link, on_change=lambda: changes.append(relay.state), open_connection=calls)  # type: ignore[arg-type]
    return SimpleNamespace(link=link, calls=calls, relay=relay, changes=changes)


# --- le chemin heureux ----------------------------------------------------------------------------------------------------


async def test_sound_goes_to_the_agent_with_the_token_at_the_address_the_link_found(parts):
    relay = parts.relay
    relay.begin()
    await eventually(lambda: relay.state == RelayState.STREAMING)
    url, options = parts.calls.opened[0]
    assert (
        url == "ws://10.0.0.2:8765/audio"
    )  # l'adresse que le lien principal a trouvée, pas un nom à résoudre
    assert options["additional_headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert options["proxy"] is None
    assert (
        json.loads(parts.calls.sockets[0].sent[0]) == HELLO
    )  # la première trame : l'annonce du format, exactement
    for _ in range(5):
        relay.feed(FRAME)
    await eventually(lambda: len(parts.calls.sockets[0].frames) == 5)
    assert parts.calls.sockets[0].frames == [FRAME] * 5
    relay.end()
    await eventually(lambda: relay.state == RelayState.IDLE)
    assert parts.calls.sockets[0].closed
    await relay.aclose()


async def test_frames_before_the_stream_is_open_or_when_nothing_plays_are_ignored(parts):
    relay = parts.relay
    relay.feed(FRAME)  # rien ne joue
    assert relay.snapshot() == {"state": "idle", "detail": ""}
    relay.begin()
    relay.feed(FRAME)  # le flux n'est pas encore ouvert : le son ne s'accumule pas en attendant
    await eventually(lambda: relay.state == RelayState.STREAMING)
    await asyncio.sleep(0.1)
    assert parts.calls.sockets[0].frames == []
    await relay.aclose()


@pytest.mark.parametrize(
    "frame",
    [b"", b"\x00" * 3, b"\x00" * (MAX_CHUNK_BYTES + SAMPLE_BYTES)],
    ids=["vide", "3 octets", "trop gros"],
)
async def test_frames_that_are_not_whole_samples_or_are_too_big_are_dropped(parts, frame):
    relay = parts.relay
    relay.begin()
    await eventually(lambda: relay.state == RelayState.STREAMING)
    relay.feed(frame)
    relay.feed(FRAME)
    await eventually(lambda: len(parts.calls.sockets[0].frames) == 1)
    assert parts.calls.sockets[0].frames == [FRAME]
    await relay.aclose()


async def test_sound_never_falls_behind_the_oldest_frames_are_dropped(parts):
    """Le PC ou le réseau ne suit pas : on jette les trames les plus anciennes (un craquement), on ne les accumule pas."""
    parts.calls.next = [FakeSocket(hold_sends=True)]
    relay = parts.relay
    relay.begin()
    await eventually(lambda: relay.state == RelayState.STREAMING)
    frames = [n.to_bytes(4, "little") * 960 for n in range(1, 101)]  # 2 secondes de son
    for frame in frames:
        relay.feed(frame)
        await asyncio.sleep(0)
    assert relay._queued <= MAX_QUEUE_BYTES
    assert relay.dropped > 0
    assert relay._queue[-1] == frames[-1]  # on garde le plus récent
    assert relay._queue[0] != frames[0]
    parts.calls.sockets[0].gate.set()
    await relay.aclose()


async def test_the_bound_is_a_third_of_a_second():
    assert MAX_QUEUE_BYTES == RATE * SAMPLE_BYTES * 300 // 1000


async def test_a_pause_keeps_the_stream_open_with_a_keepalive_not_with_sound(parts, monkeypatch):
    """Une vidéo en pause ne fait aucun son : sans maintien, l'agent fermerait le flux au bout de dix secondes."""
    monkeypatch.setattr(audio_relay, "KEEPALIVE_SECONDS", 0.05)
    relay = parts.relay
    relay.begin()
    await eventually(lambda: relay.state == RelayState.STREAMING)
    socket = parts.calls.sockets[0]
    await eventually(lambda: socket.sent.count(audio_relay.KEEPALIVE) >= 3)
    assert socket.frames == []  # pas un octet de son inventé
    assert relay.state == RelayState.STREAMING
    await relay.aclose()


async def test_what_is_left_is_sent_before_the_stream_closes(parts):
    parts.calls.next = [FakeSocket(hold_sends=True)]
    relay = parts.relay
    relay.begin()
    await eventually(lambda: relay.state == RelayState.STREAMING)
    for _ in range(3):
        relay.feed(FRAME)
    relay.end()
    parts.calls.sockets[0].gate.set()
    await eventually(lambda: relay.state == RelayState.IDLE)
    assert len(parts.calls.sockets[0].frames) == 3
    await relay.aclose()


# --- les pannes, dites en clair -------------------------------------------------------------------------------------------


async def test_a_pc_that_is_offline_is_said_and_the_sound_resumes_when_it_comes_back(parts):
    parts.link.connected = False
    relay = parts.relay
    relay.begin()
    await eventually(lambda: relay.state == RelayState.OFFLINE)
    assert "hors ligne" in relay.detail
    relay.feed(FRAME)  # rien n'est gardé pour plus tard
    assert relay._queued == 0
    parts.link.connected = True
    await eventually(lambda: relay.state == RelayState.STREAMING)
    assert parts.calls.sockets[0].frames == []
    await relay.aclose()


async def test_an_agent_too_old_to_play_sound_is_told_to_be_updated(parts):
    parts.link.status.agent = {"version": "1.7.3"}
    relay = parts.relay
    relay.begin()
    await eventually(lambda: relay.state == RelayState.OLD)
    assert "mets-le à jour" in relay.detail
    assert (
        parts.calls.opened == []
    )  # on ne frappe pas à une porte qui n'existe pas (ce serait un échec d'authentification de plus)
    await relay.aclose()


async def test_a_pc_that_cannot_play_says_why_and_is_tried_again(parts):
    refusal = json.dumps(
        {
            "t": "err",
            "code": "unavailable",
            "msg": "pacat est introuvable sur le PC (sudo apt install pulseaudio-utils).",
        }
    )
    parts.calls.next = [FakeSocket(refusal)]
    relay = parts.relay
    relay.begin()
    await eventually(lambda: relay.state == RelayState.ERROR)
    assert "pulseaudio-utils" in relay.detail
    await eventually(
        lambda: relay.state == RelayState.STREAMING
    )  # le PC a été réparé : le son reprend tout seul
    await relay.aclose()


async def test_a_refused_connection_says_so(parts):
    response = SimpleNamespace(status_code=403)
    parts.calls.next = [InvalidStatus(response)]  # type: ignore[arg-type]
    relay = parts.relay
    relay.begin()
    await eventually(lambda: relay.state == RelayState.ERROR)
    assert "HTTP 403" in relay.detail
    await relay.aclose()


async def test_an_unreachable_pc_is_offline_not_an_error(parts):
    parts.calls.next = [ConnectionRefusedError()]
    relay = parts.relay
    relay.begin()
    await eventually(lambda: relay.state == RelayState.OFFLINE)
    await eventually(lambda: relay.state == RelayState.STREAMING)
    await relay.aclose()


async def test_a_connection_cut_in_the_middle_is_reopened_while_something_plays(parts):
    relay = parts.relay
    relay.begin()
    await eventually(lambda: relay.state == RelayState.STREAMING)
    relay.feed(FRAME)
    await eventually(lambda: len(parts.calls.sockets[0].frames) == 1)
    parts.calls.sockets[0].lost.set()  # le réseau a coupé
    await eventually(lambda: len(parts.calls.sockets) == 2)
    await eventually(lambda: relay.state == RelayState.STREAMING)
    relay.feed(FRAME)
    await eventually(lambda: len(parts.calls.sockets[1].frames) == 1)
    await relay.aclose()


async def test_nothing_is_reopened_once_the_sound_has_stopped(parts):
    relay = parts.relay
    relay.begin()
    await eventually(lambda: relay.state == RelayState.STREAMING)
    relay.end()
    await eventually(lambda: relay.state == RelayState.IDLE)
    await asyncio.sleep(0.3)
    assert len(parts.calls.opened) == 1
    await relay.aclose()


async def test_each_sound_counts_its_own_bytes(parts):
    relay = parts.relay
    relay.begin()
    await eventually(lambda: relay.state == RelayState.STREAMING)
    relay.feed(FRAME)
    await eventually(lambda: relay.sent == len(FRAME))
    relay.end()
    await eventually(lambda: relay.state == RelayState.IDLE)
    relay.begin()
    assert relay.sent == 0
    await relay.aclose()


# --- le lecteur : où sort le son, et pourquoi on n'entend rien ------------------------------------------------------------


def make_hub(*, pc_audio: bool = True):
    changes: list[int] = []
    hub = PlayerHub(lambda: changes.append(1))
    link = FakeLink()
    relay = AudioRelay(link, open_connection=Calls())  # type: ignore[arg-type]
    hub.audio = relay
    hub.settings = {"pc_audio": pc_audio}
    conn = HelperConn()
    hub.attach(conn)
    conn.info = {"vlc": True, "touch": True, "mame": True}
    return hub, conn, relay


async def test_the_snapshot_says_where_the_sound_goes():
    hub, _conn, _relay = make_hub()
    assert hub.snapshot()["audio"] == {"mode": "pc", "state": "idle", "detail": ""}
    hub.settings = {"pc_audio": False}
    assert hub.snapshot()["audio"] == {"mode": "local", "state": "local", "detail": ""}


async def test_the_helper_says_it_captures_and_the_relay_opens_then_it_says_it_stopped():
    hub, conn, relay = make_hub()
    hub.play({"title": "x"}, "rtsp://h/x")
    hub.on_message(conn, {"t": "audio", "state": "capturing"})
    assert relay.capturing
    assert hub.snapshot()["audio"]["mode"] == "pc"
    hub.on_message(conn, {"t": "audio", "state": "stopped"})
    assert not relay.capturing
    await relay.aclose()


async def test_the_sound_stops_when_the_playback_ends_is_stopped_or_the_helper_leaves():
    for stop in (
        lambda hub, conn: hub.on_message(conn, {"t": "ended", "job": 1}),
        lambda hub, conn: hub.command("stop"),
        lambda hub, conn: hub.detach(conn),
    ):
        hub, conn, relay = make_hub()
        hub.play({"title": "x"}, "rtsp://h/x")
        hub.on_message(conn, {"t": "audio", "state": "capturing"})
        assert relay.capturing
        stop(hub, conn)
        assert not relay.capturing
        await relay.aclose()


async def test_a_helper_that_cannot_capture_says_why_on_the_screen():
    hub, conn, relay = make_hub()
    hub.play({"title": "x"}, "rtsp://h/x")
    hub.on_message(
        conn,
        {
            "t": "audio",
            "state": "unavailable",
            "reason": "Pas de serveur de son (PipeWire) dans la session de l'écran.",
        },
    )
    shown = hub.snapshot()["audio"]
    assert shown["state"] == "error"
    assert "PipeWire" in shown["detail"]
    hub.on_message(
        conn, {"t": "audio", "state": "capturing"}
    )  # il y est arrivé depuis : le problème disparaît
    assert hub.snapshot()["audio"]["state"] != "error"
    await relay.aclose()


@pytest.mark.parametrize(
    "message",
    [
        {"t": "audio"},
        {"t": "audio", "state": 5},
        {"t": "audio", "state": "autre"},
        {"t": "audio", "state": "unavailable", "reason": {"x": 1}},
    ],
)
async def test_odd_audio_messages_from_the_helper_change_nothing_dangerous(message):
    hub, conn, relay = make_hub()
    hub.on_message(conn, message)
    assert not relay.capturing
    await relay.aclose()


async def test_the_state_shown_is_the_relays_while_something_plays():
    hub, _conn, relay = make_hub()
    hub.play({"title": "x"}, "rtsp://h/x")
    relay.state, relay.detail = RelayState.OFFLINE, "Le PC est hors ligne : pas de son."
    assert hub.snapshot()["audio"] == {
        "mode": "pc",
        "state": "offline",
        "detail": "Le PC est hors ligne : pas de son.",
    }
    hub.command("stop")
    assert (
        hub.snapshot()["audio"]["state"] == "idle"
    )  # rien ne joue : pas d'avertissement qui traîne


# --- les réglages ---------------------------------------------------------------------------------------------------------


def test_the_defaults_send_the_sound_to_the_pc_with_a_small_advance(tmp_path):
    config = DockConfig.from_dict({}, tmp_path / "d.toml")
    assert config.pc_audio is True
    assert config.audio_advance_ms == 150


@pytest.mark.parametrize(
    "media",
    [
        {"pc_audio": "oui"},
        {"pc_audio": 1},
        {"audio_advance_ms": -1},
        {"audio_advance_ms": 1001},
        {"audio_advance_ms": "150"},
        {"audio_advance_ms": True},
        {"audio_advance_ms": 1.5},
    ],
)
def test_a_wrong_sound_setting_in_the_file_is_refused_with_its_section(tmp_path, media):
    with pytest.raises(ConfigError) as refused:
        DockConfig.from_dict({"media": media}, tmp_path / "d.toml")
    assert "[media]" in str(refused.value)


def test_the_sound_settings_are_kept_in_the_private_file(tmp_path):
    config = DockConfig.from_dict({}, tmp_path / "d.toml")
    config.set_sound(pc_audio=False, audio_advance_ms=300)
    again = DockConfig.from_dict(
        __import__("tomllib").loads((tmp_path / "d.toml").read_text()), tmp_path / "d.toml"
    )
    assert (again.pc_audio, again.audio_advance_ms) == (False, 300)
    config.set_sound(pc_audio=True)  # None ne change rien
    assert config.audio_advance_ms == 300


@pytest.mark.parametrize(
    "patch",
    [
        {"pc_audio": "oui"},
        {"audio_advance_ms": 5000},
        {"audio_advance_ms": -3},
        {"audio_advance_ms": "x"},
    ],
)
def test_a_refused_sound_setting_changes_nothing(tmp_path, patch):
    config = DockConfig.from_dict({}, tmp_path / "d.toml")
    with pytest.raises(ValidationFailure):
        config.set_sound(**patch)
    assert (config.pc_audio, config.audio_advance_ms) == (True, 150)


# --- le circuit complet : assistant simulé -> dock -> vrai agent ---------------------------------------------------------


class Recorder:
    """Le « PC » : un vrai agent dont le lecteur note le son qu'il reçoit."""

    instances: list[Recorder] = []

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.stopped = False
        Recorder.instances.append(self)

    async def start(self) -> None: ...

    def backlog(self) -> int:
        return 0

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    @property
    def alive(self) -> bool:
        return not self.stopped

    async def stop(self) -> None:
        self.stopped = True

    def failure(self) -> str:
        return ""


@contextlib.asynccontextmanager
async def pair(tmp_path):
    Recorder.instances = []
    async with (
        running_agent(audio_in=AudioIn(Recorder)) as agent,
        running_dock(tmp_path, agent_port=agent.port) as dock,
    ):
        await eventually(lambda: dock.server.link.connected, timeout=5)
        yield agent, dock


async def audio_shown(browser, state: str) -> dict:
    """Ce que l'écran lit de l'état du son, dès qu'il en est à `state` (les mises à jour arrivent d'elles-mêmes)."""
    async with asyncio.timeout(5):
        while True:
            message = json.loads(await browser.recv())
            data = message.get("data") if message.get("topic") == "player" else None
            if message.get("t") == "snap" and data and data["audio"]["state"] == state:
                return data["audio"]


async def player_hello(ws) -> None:
    await ws.send(json.dumps({"t": "hello", "v": 1, "vlc": True, "touch": True, "mame": True}))


async def test_sound_captured_by_the_helper_reaches_the_pc_through_the_dock(tmp_path):
    async with pair(tmp_path) as (_agent, dock), dock.connect_player() as helper:
        await player_hello(helper)
        await eventually(
            lambda: dock.server.player.helper is not None and bool(dock.server.player.helper.info)
        )
        dock.server.player.play({"title": "Un film"}, "rtsp://h/x")
        await helper.send(json.dumps({"t": "audio", "state": "capturing"}))
        await eventually(lambda: dock.server.audio.state == RelayState.STREAMING, timeout=5)
        frames = [bytes([n]) * 3840 for n in range(1, 21)]
        for frame in frames:
            await helper.send(frame)
            await asyncio.sleep(
                0.01
            )  # le vrai son arrive au rythme du temps réel (une trame toutes les 20 ms)
        await eventually(
            lambda: Recorder.instances and len(Recorder.instances[0].writes) == 20, timeout=5
        )
        assert Recorder.instances[0].writes == frames  # dans l'ordre, sans rien de plus
        await helper.send(json.dumps({"t": "audio", "state": "stopped"}))
        await eventually(lambda: Recorder.instances[0].stopped, timeout=5)


async def test_the_screen_is_told_where_the_sound_goes(tmp_path):
    async with (
        pair(tmp_path) as (_agent, dock),
        dock.connect() as browser,
        dock.connect_player() as helper,
    ):
        await player_hello(helper)
        await eventually(
            lambda: dock.server.player.helper is not None and bool(dock.server.player.helper.info)
        )
        dock.server.player.play({"title": "Un film"}, "rtsp://h/x")
        await helper.send(json.dumps({"t": "audio", "state": "capturing"}))
        await eventually(lambda: dock.server.audio.state == RelayState.STREAMING, timeout=5)
        assert await audio_shown(browser, "streaming") == {
            "mode": "pc",
            "state": "streaming",
            "detail": "",
        }


async def test_binary_data_is_never_taken_for_anything_but_sound(tmp_path):
    async with pair(tmp_path) as (_agent, dock), dock.connect_player() as helper:
        await player_hello(helper)
        await helper.send(b"\x00" * 7)  # n'importe quoi en binaire quand rien ne capture : ignoré
        await helper.send(b"x" * (200 * 1024))
        await asyncio.sleep(0.2)
        assert Recorder.instances == []
        assert (
            dock.server.player.helper is not None
        )  # et la connexion de l'assistant n'est pas cassée


async def test_changing_the_sound_setting_reaches_the_helper_and_the_screen(tmp_path):
    async with (
        running_dock(tmp_path) as dock,
        dock.connect() as browser,
        dock.connect_player() as helper,
    ):
        await player_hello(helper)
        first = json.loads(await asyncio.wait_for(helper.recv(), 3))
        assert (first["pc_audio"], first["audio_advance_ms"]) == (True, 150)
        ack = await call(
            browser, "s1", "dock.sound.set", {"pc_audio": False, "audio_advance_ms": 400}
        )
        assert ack["ok"], ack
        assert ack["r"]["pc_audio"] is False
        again = json.loads(await asyncio.wait_for(helper.recv(), 3))
        assert (again["pc_audio"], again["audio_advance_ms"]) == (False, 400)
        assert (await snapshot_of(browser, "player"))["data"]["audio"]["mode"] == "local"
        assert dock.config.pc_audio is False


@pytest.mark.parametrize(
    "params",
    [
        {"pc_audio": "oui"},
        {"audio_advance_ms": 1001},
        {"audio_advance_ms": -1},
        {"audio_advance_ms": 1.5},
        {"commande": "rm -rf /"},
    ],
)
async def test_a_bad_sound_setting_is_refused_and_changes_nothing(tmp_path, params):
    async with running_dock(tmp_path) as dock, dock.connect() as browser:
        ack = await call(browser, "s2", "dock.sound.set", params)
        assert ack["ok"] is False
        assert (dock.config.pc_audio, dock.config.audio_advance_ms) == (True, 150)
