"""Son : l'agent face à un VRAI serveur de son (PipeWire et sa couche PulseAudio) et un vrai `pactl`.

Une pile PipeWire privée est lancée pour la durée du module, dans un dossier d'exécution jetable :
elle ne touche pas au son du poste. Des sorties, un micro et des applications y sont créés pour de
vrai (modules « null-sink », nœud virtuel, flux `pw-cat`) ; chaque réglage est vérifié en
interrogeant le serveur par un autre chemin que celui de l'agent. Sans PipeWire installé, ces tests
sont ignorés.
"""

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from mydock_agent.collectors import audio
from mydock_agent.collectors.audio import (
    AudioProvider,
    SubprocessPactl,
    percent_of,
    register_audio_actions,
)
from mydock_agent.testing import eventually, recv_until, running_agent, send_json

BINARIES = ("dbus-daemon", "pipewire", "wireplumber", "pipewire-pulse", "pactl", "pw-cli", "pw-cat")
pytestmark = pytest.mark.skipif(
    any(shutil.which(name) is None for name in BINARIES), reason="PipeWire absent"
)

VIRTUAL_MIC = (
    "{ factory.name=support.null-audio-sink node.name=micro_test "
    'node.description="Micro de test" media.class=Audio/Source/Virtual '
    "audio.position=[FL,FR] object.linger=true }"
)


class Stack:
    """Une session de son complète et jetable : bus D-Bus, PipeWire, WirePlumber, pipewire-pulse."""

    def __init__(self) -> None:
        # Le chemin du socket est limité à 107 octets : un dossier court sous /tmp.
        self.runtime = Path(tempfile.mkdtemp(prefix="mdkpw"))
        self.runtime.chmod(0o700)
        bus = self.runtime / "bus"
        self.env = {
            **os.environ,
            "XDG_RUNTIME_DIR": str(self.runtime),
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus}",
            "LC_ALL": "C",
        }
        for leftover in ("PULSE_SERVER", "PULSE_RUNTIME_PATH", "PIPEWIRE_REMOTE"):
            self.env.pop(leftover, None)
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._extras: list[subprocess.Popen[bytes]] = []
        self._start("dbus", ["dbus-daemon", "--session", "--nofork", f"--address=unix:path={bus}"])
        self._wait(lambda: bus.exists(), "le bus D-Bus")
        self._start("pipewire", ["pipewire"])
        time.sleep(0.8)
        self._start("wireplumber", ["wireplumber"])
        time.sleep(1.2)
        self.start_pulse()

    def _start(self, name: str, command: list[str]) -> None:
        log = (self.runtime / f"{name}.log").open("wb")
        self._processes[name] = subprocess.Popen(
            command, env=self.env, stdout=log, stderr=log, start_new_session=True
        )

    def _wait(self, ready, what: str, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ready():
                return
            time.sleep(0.1)
        raise RuntimeError(f"{what} n'a pas démarré")

    def start_pulse(self) -> None:
        self._start("pulse", ["pipewire-pulse"])
        self._wait(lambda: self.run("info", check=False).returncode == 0, "pipewire-pulse")

    def stop_pulse(self) -> None:
        process = self._processes.pop("pulse")
        process.terminate()
        process.wait(timeout=10)

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["pactl", *args], env=self.env, capture_output=True, text=True, timeout=10, check=check
        )

    def json(self, *args: str) -> Any:
        return json.loads(self.run("-f", "json", *args).stdout)

    def load_sink(self, name: str, description: str) -> str:
        """Crée une sortie virtuelle ; retourne le numéro de son module (pour la retirer ensuite)."""
        done = subprocess.run(
            [
                "pactl",
                "load-module",
                "module-null-sink",
                f"sink_name={name}",
                f"sink_properties=device.description={description}",
            ],
            env={**self.env, "LC_ALL": "C.UTF-8"},  # pour transmettre l'accent au serveur
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return done.stdout.strip()

    def sink(self, name: str) -> dict[str, Any]:
        return next(s for s in self.json("list", "sinks") if s["name"] == name)

    def source(self, name: str) -> dict[str, Any]:
        return next(s for s in self.json("list", "sources") if s["name"] == name)

    def spawn(self, command: list[str], *, stdin: int | None = None) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            command,
            env=self.env,
            stdin=stdin,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._extras.append(process)
        return process

    def populate(self) -> None:
        """Les périphériques de la maison fictive (recréés si pipewire-pulse a redémarré)."""
        names = {s["name"] for s in self.json("list", "sinks")}
        if "casque" not in names:
            self.run(
                "load-module",
                "module-null-sink",
                "sink_name=casque",
                "sink_properties=device.description=Casque device.form_factor=headphone",
            )
        if "hdmi_ecran" not in names:
            self.run(
                "load-module",
                "module-null-sink",
                "sink_name=hdmi_ecran",
                "sink_properties=device.description=HDMI-ecran",
            )
        if "micro_test" not in {s["name"] for s in self.json("list", "sources")}:
            subprocess.run(
                ["pw-cli", "create-node", "adapter", VIRTUAL_MIC],
                env=self.env,
                capture_output=True,
                timeout=10,
                check=True,
            )
        self._wait(
            lambda: "micro_test" in {s["name"] for s in self.json("list", "sources")}, "le micro"
        )
        self.run("set-default-sink", "casque")
        self.run("set-default-source", "micro_test")
        for sink in ("casque", "hdmi_ecran"):
            self.run("set-sink-volume", sink, "100%")
            self.run("set-sink-mute", sink, "0")
        self.run("set-source-volume", "micro_test", "100%")
        self.run("set-source-mute", "micro_test", "0")

    def close(self) -> None:
        for process in [*self._extras, *reversed(self._processes.values())]:
            if process.poll() is None:
                process.terminate()
        for process in [*self._extras, *self._processes.values()]:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
            if process.poll() is None:
                process.kill()
        shutil.rmtree(self.runtime, ignore_errors=True)


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    stack = Stack()
    try:
        yield stack
    finally:
        stack.close()


@pytest.fixture(autouse=True)
def world(stack: Stack) -> Stack:
    stack.populate()
    return stack


@contextlib.asynccontextmanager
async def running(stack: Stack):
    provider = AudioProvider(SubprocessPactl(env=stack.env))
    published: list[dict[str, Any]] = []

    async def publish(data):
        published.append(data)
        return True

    task = asyncio.create_task(provider.run(publish))
    try:
        await eventually(lambda: published and "unavailable" not in published[-1], timeout=10)
        yield provider, published
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def near(percent: int, wanted: int) -> bool:
    return abs(percent - wanted) <= 1  # PipeWire garde le volume en nombre à virgule


def app(snapshot: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((a for a in snapshot["apps"] if a["name"] == name), None)


async def test_the_agent_reads_a_real_sound_server(stack):
    async with running(stack) as (_provider, published):
        state = published[-1]
        ids = {s["id"]: s for s in state["sinks"]}
        assert {"casque", "hdmi_ecran"} <= set(ids)
        assert ids["casque"]["kind"] == "headphones"
        assert ids["casque"]["label"] == "Casque"
        assert ids["hdmi_ecran"]["kind"] == "hdmi"
        assert [s["id"] for s in state["sources"]] == ["micro_test"]  # les moniteurs sont écartés
        assert state["default_sink"] == "casque"
        assert state["default_source"] == "micro_test"


async def test_volume_mute_and_default_really_change_on_the_server(stack):
    async with running(stack) as (provider, published):
        await provider.set_volume("sink", "casque", 37)
        assert near(
            percent_of(max(c["value"] for c in stack.sink("casque")["volume"].values())), 37
        )
        await eventually(lambda: near(published[-1]["sinks"][0]["volume"], 37), timeout=10)

        await provider.set_mute("sink", "casque", True)
        assert stack.sink("casque")["mute"] is True
        await provider.set_mute("sink", "casque", False)
        assert stack.sink("casque")["mute"] is False

        await provider.set_volume("source", "micro_test", 55)
        assert near(
            percent_of(max(c["value"] for c in stack.source("micro_test")["volume"].values())), 55
        )

        await provider.set_default("sink", "hdmi_ecran")
        assert stack.json("info")["default_sink_name"] == "hdmi_ecran"
        await eventually(lambda: published[-1]["default_sink"] == "hdmi_ecran", timeout=10)


async def test_the_left_right_balance_survives_on_a_real_server(stack):
    stack.run("set-sink-volume", "casque", "65536", "32768")  # gauche 100 %, droite 50 %
    async with running(stack) as (provider, _published):
        await provider.set_volume("sink", "casque", 60)
        left, right = (c["value"] for c in stack.sink("casque")["volume"].values())
        assert abs(left - 39322) <= 40
        assert abs(right - 19661) <= 40


async def test_changes_made_by_other_programs_reach_the_dock(stack):
    async with running(stack) as (_provider, published):
        stack.run("set-sink-volume", "casque", "20%")  # le clavier, GNOME, pavucontrol...
        await eventually(lambda: near(published[-1]["sinks"][0]["volume"], 20), timeout=10)
        stack.run("set-sink-mute", "casque", "1")
        await eventually(lambda: published[-1]["sinks"][0]["muted"] is True, timeout=10)
        stack.run("set-default-sink", "hdmi_ecran")
        await eventually(lambda: published[-1]["default_sink"] == "hdmi_ecran", timeout=10)


async def test_applications_come_and_go_and_can_be_adjusted(stack):
    async with running(stack) as (provider, published):
        player = stack.spawn(
            [
                "pw-cat",
                "-p",
                "--rate",
                "48000",
                "--channels",
                "2",
                "--format",
                "s16",
                "--properties",
                '{ application.name = spotify media.name = "Nuit blanche" }',
                "-",
            ],
            stdin=os.open("/dev/zero", os.O_RDONLY),
        )
        await eventually(lambda: app(published[-1], "Spotify") is not None, timeout=5)
        # Un flux naît « en pause » et se met à jouer un instant plus tard : l'agent publie les deux.
        await eventually(lambda: app(published[-1], "Spotify")["playing"] is True, timeout=5)
        spotify = app(published[-1], "Spotify")
        assert spotify["sink"] == "casque"  # « spotify » est écrit comme il faut

        await provider.set_volume("app", spotify["id"], 25)
        server = next(
            s for s in stack.json("list", "sink-inputs") if str(s["index"]) == spotify["id"]
        )
        assert near(percent_of(max(c["value"] for c in server["volume"].values())), 25)
        await provider.set_mute("app", spotify["id"], True)
        await eventually(lambda: app(published[-1], "Spotify")["muted"] is True, timeout=10)

        player.terminate()
        player.wait(timeout=5)
        await eventually(lambda: app(published[-1], "Spotify") is None, timeout=5)


async def test_a_microphone_in_use_is_reported_and_forgotten(stack):
    async with running(stack) as (_provider, published):
        recorder = stack.spawn(
            [
                "pw-cat",
                "-r",
                "--rate",
                "48000",
                "--channels",
                "2",
                "--format",
                "s16",
                "--properties",
                "{ application.name = Discord }",
                "-",
            ]
        )
        await eventually(lambda: published[-1]["recorders"], timeout=5)
        assert [(r["name"], r["source"]) for r in published[-1]["recorders"]] == [
            ("Discord", "micro_test")
        ]
        recorder.terminate()
        recorder.wait(timeout=5)
        await eventually(lambda: not published[-1]["recorders"], timeout=5)


async def test_accented_names_that_pactl_cannot_print_get_a_readable_replacement(stack):
    """Le JSON de pactl remplace tout texte accentué par « (null) » : l'agent ne doit jamais l'afficher."""
    module = stack.load_sink("ecouteurs", "Écouteurs-de-Marie")
    try:
        # Le serveur connaît bien le nom accentué ; c'est pactl qui ne sait pas l'écrire.
        assert stack.sink("ecouteurs")["description"] == "(null)"
        stack.spawn(
            [
                "pw-cat",
                "-p",
                "--rate",
                "48000",
                "--channels",
                "2",
                "--format",
                "s16",
                "--properties",
                "{ application.name = Écouteur-ünï application.process.binary = lecteur }",
                "-",
            ],
            stdin=os.open("/dev/zero", os.O_RDONLY),
        )
        async with running(stack) as (_provider, published):
            await eventually(lambda: app(published[-1], "lecteur") is not None, timeout=5)
            sink = next(s for s in published[-1]["sinks"] if s["id"] == "ecouteurs")
            assert sink["label"] == "Sortie audio"
            assert "(null)" not in json.dumps(published[-1], ensure_ascii=False)
    finally:
        stack.run("unload-module", module, check=False)


async def test_the_provider_recovers_when_the_sound_server_restarts(stack, monkeypatch):
    monkeypatch.setattr(audio, "RETRY_SECONDS", 0.3)
    async with running(stack) as (_provider, published):
        stack.stop_pulse()
        await eventually(lambda: "unavailable" in published[-1], timeout=8)
        assert "Nouvelle tentative" in published[-1]["unavailable"]
        stack.start_pulse()
        await eventually(lambda: "unavailable" not in published[-1], timeout=10)


async def test_the_whole_agent_drives_the_real_sound_server_over_the_wire(stack):
    from mydock_agent.builtin_actions import register_builtin
    from mydock_common.actions import ActionRegistry

    provider = AudioProvider(SubprocessPactl(env=stack.env))
    registry = ActionRegistry()
    register_builtin(registry, demo=False)
    register_audio_actions(registry, provider)
    async with (
        running_agent([provider], registry=registry, demo=False) as agent,
        agent.connect() as ws,
    ):
        await recv_until(ws, lambda m: m["t"] == "hello")
        await send_json(ws, {"t": "sub", "topics": ["audio"]})
        first = await recv_until(ws, lambda m: m.get("topic") == "audio", timeout=10)
        assert "casque" in [s["id"] for s in first["data"]["sinks"]]

        order = {"t": "act", "id": "v1", "a": "audio.set_volume"}
        await send_json(ws, {**order, "p": {"kind": "sink", "id": "casque", "percent": 42}})
        ack = await recv_until(ws, lambda m: m.get("t") == "ack" and m["id"] == "v1", timeout=5)
        assert ack["ok"] is True
        changed = await recv_until(
            ws,
            lambda m: (
                m.get("topic") == "audio"
                and near(next(s for s in m["data"]["sinks"] if s["id"] == "casque")["volume"], 42)
            ),
            timeout=5,
        )
        assert changed["data"]["default_sink"] == "casque"
        assert near(
            percent_of(max(c["value"] for c in stack.sink("casque")["volume"].values())), 42
        )

        await send_json(
            ws, {**order, "id": "v2", "p": {"kind": "sink", "id": "inconnu", "percent": 5}}
        )
        refused = await recv_until(ws, lambda m: m.get("t") == "ack" and m["id"] == "v2", timeout=5)
        assert refused["e"]["code"] == "not_found"
