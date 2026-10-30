"""L'agent assemblé : liste blanche des actions, et la démonstration de bout en bout par le réseau."""

import asyncio
import base64
import json
import os

import pytest

from mydock_agent.collectors.music import art_id
from mydock_agent.collectors.system import SystemProvider
from mydock_agent.demo import music as demo_music
from mydock_agent.demo.music import DemoMusic
from mydock_agent.demo.system import DemoSensors
from mydock_agent.runtime import build_runtime
from mydock_agent.testing import eventually, recv_until, running_agent, send_json
from mydock_common.actions import ActionError

REAL_ACTIONS = [
    "diag.ping",
    "music.art",
    "music.next",
    "music.play_pause",
    "music.position",
    "music.previous",
    "music.seek",
    "music.select",
    "system.kill",
]


def test_the_whitelist_of_actions_is_exactly_this_and_nothing_else():
    """Toute nouvelle action doit être ajoutée ici consciemment : c'est la surface de commande."""
    _, registry = build_runtime(demo=False)
    assert registry.names == REAL_ACTIONS


def test_the_only_sensitive_action_of_this_stage_is_stopping_a_process():
    _, registry = build_runtime(demo=False)
    assert [n for n in registry.names if registry.is_sensitive(n)] == ["system.kill"]


def test_the_demo_adds_only_a_harmless_confirmation_test():
    _, demo = build_runtime(demo=True)
    assert sorted(set(demo.names) - set(REAL_ACTIONS)) == ["diag.confirm_demo"]


def test_both_modes_publish_the_same_topics():
    for demo in (True, False):
        providers, _ = build_runtime(demo=demo)
        assert [p.topic for p in providers] == ["host", "system", "procs", "music"]
        assert [p.lazy for p in providers] == [False, True, True, True]


# --- DemoMusic ---------------------------------------------------------------------------------------


def test_the_demo_starts_with_spotify_playing_and_a_paused_browser():
    music = DemoMusic()
    snapshot = music.snapshot()
    assert snapshot["active"] == "spotify"
    assert [(p["id"], p["status"]) for p in snapshot["players"]] == [
        ("spotify", "playing"),
        ("brave.instance4242", "paused"),
    ]
    json.dumps(snapshot)


async def test_demo_controls_behave_like_a_real_player():
    music = DemoMusic()
    first = music.spotify.title
    await music.next(None)
    assert music.spotify.title != first
    await music.previous(None)
    assert music.spotify.title == first
    await music.previous(None)  # on repart de la fin de la liste
    assert music.spotify.title == demo_music.TRACKS[-1][0]
    await music.seek(None, 10**9)
    assert music.spotify.position_ms == music.spotify.length_ms  # borné à la durée
    await music.play_pause(None)
    assert music.spotify.status == "paused"
    frozen = music.snapshot()["players"][0]["position_ms"]
    await asyncio.sleep(0.05)
    assert music.snapshot()["players"][0]["position_ms"] == frozen  # en pause, rien n'avance


async def test_the_demo_browser_has_no_next_track():
    music = DemoMusic()
    music.select("brave")
    assert music.snapshot()["active"] == "brave.instance4242"
    with pytest.raises(ActionError) as info:
        await music.next(None)
    assert info.value.code == "unsupported"
    with pytest.raises(ActionError):
        music.select("absent")


async def test_the_demo_art_is_a_generated_svg_for_each_track_and_nothing_else():
    music = DemoMusic()
    for index in range(len(demo_music.TRACKS)):
        reply = await music.art(art_id(f"demo://art/{index}"))
        assert reply["mime"] == "image/svg+xml"
        assert base64.b64decode(reply["data"]).startswith(b"<svg")
    with pytest.raises(ActionError):
        await music.art("0123456789abcdef")


async def test_the_demo_moves_on_to_the_next_track_when_one_ends(monkeypatch):
    monkeypatch.setattr(demo_music, "TRACKS", (("Court", "A", "X", 1), ("Suivant", "B", "Y", 60)))
    music = DemoMusic()
    published = []

    async def publish(data):
        published.append(data)
        return True

    task = asyncio.create_task(music.run(publish))
    try:
        await eventually(
            lambda: published and published[-1]["players"][0]["title"] == "Suivant", timeout=5
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# --- DemoSensors -------------------------------------------------------------------------------------


async def test_the_demo_machine_is_a_hybrid_laptop_with_a_sleeping_nvidia_card():
    data = await SystemProvider(DemoSensors(nvidia_awake=False)).sample(1.0)
    states = {g["vendor"]: g["state"] for g in data["gpus"]}
    assert states == {"intel": "active", "nvidia": "suspended"}
    assert data["battery"]["percent"] == 82
    assert [d["mount"] for d in data["disks"]] == ["/", "/home/mars/Jeux"]
    json.dumps(data)


async def test_the_demo_can_show_an_awake_nvidia_card(monkeypatch):
    monkeypatch.setenv("MYDOCK_DEMO_NVIDIA", "awake")
    data = await SystemProvider(DemoSensors()).sample(1.0)
    nvidia = next(g for g in data["gpus"] if g["vendor"] == "nvidia")
    assert nvidia["state"] == "active"
    assert nvidia["util"] is not None
    assert data["history"]["gpu"][-1] == nvidia["util"]


# --- tout l'agent de démonstration, par le réseau -----------------------------------------------------


@pytest.fixture
async def demo_agent():
    providers, registry = build_runtime(demo=True)
    async with running_agent(providers, registry=registry, demo=True) as agent:
        yield agent


async def hello_and_subscribe(ws, *topics):
    await recv_until(ws, lambda m: m["t"] == "hello")
    await send_json(ws, {"t": "sub", "topics": list(topics)})


async def test_the_demo_agent_stays_idle_until_someone_subscribes(demo_agent):
    assert demo_agent.hub.running_topics() == ["host"]
    async with demo_agent.connect() as ws:
        await hello_and_subscribe(ws, "system", "procs", "music")
        await recv_until(ws, lambda m: m.get("topic") == "music")
        assert demo_agent.hub.running_topics() == ["host", "music", "procs", "system"]
    await eventually(lambda: demo_agent.hub.running_topics() == ["host"], timeout=5)


async def test_system_and_processes_snapshots_arrive_over_the_wire(demo_agent):
    async with demo_agent.connect() as ws:
        await hello_and_subscribe(ws, "system", "procs")
        system = await recv_until(ws, lambda m: m.get("topic") == "system", timeout=5)
        procs = await recv_until(ws, lambda m: m.get("topic") == "procs", timeout=5)
        later = await recv_until(
            ws,
            lambda m: m.get("topic") == "system" and len(m["data"]["history"]["cpu"]) >= 3,
            timeout=8,
        )
    assert set(system["data"]) >= {"cpu", "mem", "net", "disks", "gpus", "history"}
    assert procs["data"]["procs"][0]["name"] == "brave"
    assert later["data"]["history"]["cpu"][-1] is not None


async def test_music_controls_work_over_the_wire(demo_agent):
    async with demo_agent.connect() as ws:
        await hello_and_subscribe(ws, "music")
        first = await recv_until(ws, lambda m: m.get("topic") == "music")
        title = first["data"]["players"][0]["title"]

        await send_json(ws, {"t": "act", "id": "n1", "a": "music.next", "p": {}})
        reply = await recv_until(ws, lambda m: m.get("t") == "ack" and m["id"] == "n1")
        assert reply["ok"] is True
        changed = await recv_until(
            ws, lambda m: m.get("topic") == "music" and m["data"]["players"][0]["title"] != title
        )
        assert changed["data"]["active"] == "spotify"

        await send_json(ws, {"t": "act", "id": "p1", "a": "music.play_pause", "p": {}})
        await recv_until(
            ws,
            lambda m: m.get("topic") == "music" and m["data"]["players"][0]["status"] == "paused",
        )

        art = changed["data"]["players"][0]["art"]
        await send_json(ws, {"t": "act", "id": "a1", "a": "music.art", "p": {"art": art}})
        picture = await recv_until(ws, lambda m: m.get("t") == "ack" and m["id"] == "a1")
        assert picture["r"]["mime"] == "image/svg+xml"


async def test_stopping_a_process_asks_for_confirmation_then_removes_it(demo_agent):
    async with demo_agent.connect() as ws:
        await hello_and_subscribe(ws, "procs")
        listing = await recv_until(ws, lambda m: m.get("topic") == "procs", timeout=5)
        victim = next(p for p in listing["data"]["procs"] if p["name"] == "zoom-vide")
        assert victim["killable"] is True
        params = {"pid": victim["pid"], "started": victim["started"]}

        await send_json(ws, {"t": "act", "id": "k1", "a": "system.kill", "p": params})
        asked = await recv_until(ws, lambda m: m.get("t") == "ack" and m["id"] == "k1")
        assert asked["e"]["code"] == "confirm_required"
        assert "zoom-vide" in asked["confirm"]["label"]
        assert "démo" in asked["confirm"]["label"]

        await send_json(
            ws,
            {
                "t": "act",
                "id": "k2",
                "a": "system.kill",
                "p": params,
                "confirm": asked["confirm"]["token"],
            },
        )
        done = await recv_until(ws, lambda m: m.get("t") == "ack" and m["id"] == "k2")
        assert done["ok"] is True
        gone = await recv_until(
            ws,
            lambda m: (
                m.get("topic") == "procs"
                and all(p["name"] != "zoom-vide" for p in m["data"]["procs"])
            ),
            timeout=8,
        )
        assert gone["data"]["count"] < listing["data"]["count"]


async def test_system_processes_and_foreign_users_are_not_offered_a_kill_button(demo_agent):
    async with demo_agent.connect() as ws:
        await hello_and_subscribe(ws, "procs")
        listing = await recv_until(ws, lambda m: m.get("topic") == "procs", timeout=5)
    flags = {p["name"]: p["killable"] for p in listing["data"]["procs"]}
    assert flags["gnome-shell"] is False
    assert flags["pipewire"] is False
    assert flags["Xwayland"] is False
    assert flags["brave"] is True
    if os.getuid() != 0:
        assert flags.get("cupsd", False) is False
