"""Navigateur simulé -> vrai serveur dock -> vrai agent : le trajet complet, sans doublure."""

import asyncio
import json
from dataclasses import dataclass

import httpx
import pytest
from websockets.exceptions import InvalidStatus

from mydock_agent.testing import ScriptedProvider, running_agent
from mydock_common.config import load_toml
from mydock_dock.agent_link import LinkState
from mydock_dock.app import (
    AGENT_ACT_TIMEOUT,
    agent_act_timeout,
    host_allowed,
    origin_allowed,
    pick_wol_interface,
)
from mydock_dock.config import DockConfig
from mydock_dock.testing import (
    RunningDock,
    call,
    eventually,
    free_port,
    recv_json,
    recv_until,
    running_dock,
    send_json,
    snapshot_of,
    start_udp_sink,
)
from mydock_dock.wol import TEST_TARGETS_ENV, magic_packet

# Un portable : Ethernet débranché, Wi-Fi actif (valeurs fictives, adresses « locales »).
LAPTOP = {
    "hostname": "portable",
    "interfaces": [
        {
            "name": "enp3s0",
            "mac": "02:00:5E:10:00:01",
            "kind": "ethernet",
            "up": False,
            "ipv4": [],
            "default": False,
        },
        {
            "name": "wlp2s0",
            "mac": "02:00:5e:10:00:02",
            "kind": "wifi",
            "up": True,
            "ipv4": ["192.0.2.20"],
            "default": True,
        },
    ],
}


@dataclass
class Stack:
    agent: object
    dock: RunningDock
    host: ScriptedProvider
    music: ScriptedProvider


@pytest.fixture
async def stack(tmp_path):
    host = ScriptedProvider("host", lazy=False, initial=LAPTOP)
    music = ScriptedProvider("music", initial={"title": "Morceau"})
    async with (
        running_agent([host, music]) as agent,
        running_dock(tmp_path, agent_port=agent.port) as dock,
    ):
        await eventually(lambda: dock.server.link.connected)
        await eventually(lambda: "host" in dock.server.link.snapshots)
        yield Stack(agent, dock, host, music)


async def hello_and_topics(ws):
    hello = await recv_json(ws)
    return (
        hello,
        await snapshot_of(ws, "link"),
        await snapshot_of(ws, "dock"),
        await snapshot_of(ws, "ui"),
    )


# --- ce que reçoit le navigateur ------------------------------------------------------------------


async def test_browser_receives_hello_and_the_local_topics(stack):
    async with stack.dock.connect() as ws:
        hello, link, dock, ui = await hello_and_topics(ws)
    assert hello["t"] == "hello"
    assert hello["agent"]["role"] == "dock"
    assert link["data"]["state"] == "connected"
    assert link["data"]["agent"]["demo"] is True
    assert dock["data"]["pc"]["host"] == "127.0.0.1"
    assert dock["data"]["pc"]["token_set"] is True
    assert ui["data"]["last_page"] == "system"


async def test_agent_snapshots_are_relayed_unchanged(stack):
    async with stack.dock.connect() as ws:
        await send_json(ws, {"t": "sub", "topics": ["host", "music"]})
        host = await snapshot_of(ws, "host")
        music = await snapshot_of(ws, "music")
        assert host["data"] == LAPTOP
        assert music["data"] == {"title": "Morceau"}
        await stack.music.push({"title": "Autre"})
        assert (await snapshot_of(ws, "music"))["data"] == {"title": "Autre"}


class Tap:
    """Enrobe une connexion et garde tout ce qui a été reçu, même ce que les aides consomment."""

    def __init__(self, ws):
        self.ws = ws
        self.seen: list[str] = []

    async def recv(self, *args, **kwargs):
        message = await self.ws.recv(*args, **kwargs)
        self.seen.append(message)
        return message

    async def send(self, message):
        await self.ws.send(message)


async def test_the_agent_token_never_reaches_the_browser(stack):
    """Le navigateur ne doit jamais voir le jeton, quoi qu'il demande."""
    token = stack.agent.token
    async with stack.dock.connect() as raw:
        ws = Tap(raw)
        await hello_and_topics(ws)
        await send_json(ws, {"t": "sub", "topics": ["host", "music", "link", "dock", "ui"]})
        await call(ws, "a", "dock.pc.test")
        await call(ws, "b", "dock.pc.set", {"host": "127.0.0.1", "port": stack.agent.port})
        await call(ws, "c", "dock.ui.set", {"last_page": "power"})
        await call(ws, "d", "dock.token.set", {"token": "n" * 40})
        await call(ws, "e", "dock.bogus")
        await call(ws, "f", "dock.pc.test", {"token": "z" * 40})
        await asyncio.sleep(0.5)  # laisse passer les mises à jour de la liaison
        try:
            async with asyncio.timeout(1):
                while True:
                    await ws.recv()
        except TimeoutError:
            pass
    everything = "\n".join(ws.seen)
    assert len(ws.seen) > 10  # on a bien regardé toute la conversation
    assert token not in everything
    assert "n" * 40 not in everything  # ni le jeton qu'on vient de saisir
    assert "z" * 40 not in everything  # ni celui d'un simple test de connexion
    assert '"token_set":true' in everything


async def test_a_second_browser_sees_changes_made_by_the_first(stack):
    async with stack.dock.connect() as first, stack.dock.connect() as second:
        await hello_and_topics(first)
        await hello_and_topics(second)
        ack = await call(first, "u1", "dock.ui.set", {"last_page": "markets", "hidden": ["steam"]})
        assert ack["ok"] is True
        ui = await snapshot_of(second, "ui")
        assert ui["data"]["last_page"] == "markets"
        assert ui["data"]["hidden"] == ["steam"]


async def test_the_launcher_applications_are_shared_between_browsers_and_validated(stack):
    chosen = ["org.mozilla.firefox.desktop", "spotify.desktop"]
    async with stack.dock.connect() as first, stack.dock.connect() as second:
        await hello_and_topics(first)
        await hello_and_topics(second)
        assert (await call(first, "a1", "dock.ui.set", {"pinned_apps": chosen}))["ok"] is True
        assert (await snapshot_of(second, "ui"))["data"]["pinned_apps"] == chosen
        for bad in (
            ["firefox"],
            ["a.desktop", "a.desktop"],
            "firefox.desktop",
            ["../../x.desktop"],
        ):
            refused = await call(first, "a2", "dock.ui.set", {"pinned_apps": bad})
            assert refused["ok"] is False
        again = await call(first, "a3", "dock.ui.set", {"last_page": "apps"})
        assert again["r"]["pinned_apps"] == chosen  # un réglage refusé n'a rien changé


async def test_the_screen_sleep_delay_is_shared_validated_and_readable_by_the_pi_script(stack):
    async with httpx.AsyncClient(trust_env=False, base_url=stack.dock.origin) as client:
        assert (await client.get("/api/screen")).json() == {"sleep_after": 0}
        async with stack.dock.connect() as first, stack.dock.connect() as second:
            await hello_and_topics(first)
            await hello_and_topics(second)
            assert (await call(first, "s1", "dock.ui.set", {"screen_sleep": 300}))["ok"] is True
            assert (await snapshot_of(second, "ui"))["data"]["screen_sleep"] == 300
            reply = await client.get("/api/screen")
            assert reply.json() == {"sleep_after": 300}
            assert reply.headers["cache-control"] == "no-store"
            for bad in (45, "300", True, -1):
                refused = await call(first, "s2", "dock.ui.set", {"screen_sleep": bad})
                assert refused["ok"] is False
            assert (await client.get("/api/screen")).json() == {"sleep_after": 300}


async def test_the_screen_endpoint_refuses_a_rebinding_host(stack):
    async with httpx.AsyncClient(trust_env=False, base_url=stack.dock.origin) as client:
        rebound = await client.get("/api/screen", headers={"Host": "evil.example"})
        assert rebound.status_code == 421


# --- actions -------------------------------------------------------------------------------


async def test_local_ping(stack):
    async with stack.dock.connect() as ws:
        ack = await call(ws, "p1", "dock.ping")
    assert ack["ok"] is True
    assert ack["r"]["dock_ms"] > 1_700_000_000_000


async def test_agent_actions_are_forwarded_with_the_browsers_id_and_hop_timing(stack):
    async with stack.dock.connect() as ws:
        ack = await call(ws, "mine-42", "diag.ping")
    assert ack["id"] == "mine-42"
    assert ack["ok"] is True
    assert ack["hop"]["link_ms"] >= 0
    assert ack["ms"] >= 0  # temps d'exécution mesuré par l'agent


def test_slow_spotify_actions_get_more_time_than_the_others():
    """Lancer Spotify puis jouer peut prendre une quarantaine de secondes : le dock ne doit pas abandonner à 6 s."""
    assert agent_act_timeout("music.next") == AGENT_ACT_TIMEOUT == 6.0
    assert agent_act_timeout("apps.launch") == 6.0
    for name in (
        "spotify.play",
        "spotify.launch",
        "spotify.search",
        "spotify.view",
        "spotify.image",
    ):
        assert agent_act_timeout(name) > 60.0
    assert (
        agent_act_timeout("spotifyx.play") == AGENT_ACT_TIMEOUT
    )  # un préfixe exact, pas une simple ressemblance


async def test_the_agent_stays_the_judge_of_unknown_actions_and_bad_params(stack):
    async with stack.dock.connect() as ws:
        assert (await call(ws, "a", "rm.rf"))["e"]["code"] == "unknown_action"
        assert (await call(ws, "b", "diag.ping", {"x": 1}))["e"]["code"] == "bad_params"


async def test_dock_namespace_is_never_forwarded_to_the_agent(stack):
    called = []

    async def evil(_ctx, _params):
        called.append(True)

    stack.agent.registry.register("dock.evil", evil)
    async with stack.dock.connect() as ws:
        ack = await call(ws, "e1", "dock.evil")
    assert ack["e"]["code"] == "unknown_action"
    assert called == []


async def test_sensitive_confirmation_passes_through_the_dock(stack):
    async with stack.dock.connect() as ws:
        needs = await call(ws, "s1", "diag.confirm_demo")
        assert needs["e"]["code"] == "confirm_required"
        assert needs["id"] == "s1"
        token = needs["confirm"]["token"]
        done = await call(ws, "s2", "diag.confirm_demo", confirm=token)
        assert done["ok"] is True
        replay = await call(ws, "s3", "diag.confirm_demo", confirm=token)
        assert replay["e"]["code"] == "confirm_invalid"


async def test_actions_fail_cleanly_when_the_pc_is_offline(tmp_path):
    async with running_dock(tmp_path) as dock, dock.connect() as ws:  # PC non configuré
        await hello_and_topics(ws)
        ack = await call(ws, "o1", "diag.ping")
    assert ack["ok"] is False
    assert ack["e"]["code"] == "pc_offline"


# --- réglages -------------------------------------------------------------------------------


async def test_invalid_settings_are_explained_in_french(stack):
    async with stack.dock.connect() as ws:
        bad_host = await call(ws, "a", "dock.pc.set", {"host": "http://pc", "port": 8765})
        assert bad_host["e"]["code"] == "bad_params"
        assert "sans http://" in bad_host["e"]["msg"]
        bad_port = await call(ws, "b", "dock.pc.set", {"host": "pc.local", "port": 70000})
        assert "entre 1 et 65535" in bad_port["e"]["msg"]
        bad_token = await call(ws, "c", "dock.token.set", {"token": "court"})
        assert "Jeton invalide" in bad_token["e"]["msg"]
        bad_mac = await call(ws, "d", "dock.wol.set_mac", {"mac": "nimportequoi"})
        assert "MAC invalide" in bad_mac["e"]["msg"]
        wrong_type = await call(ws, "e", "dock.pc.set", {"host": "pc.local", "port": "8765"})
        assert wrong_type["e"]["code"] == "bad_params"
        unknown = await call(ws, "f", "dock.pc.set", {"host": "pc", "port": 1, "extra": True})
        assert unknown["e"]["code"] == "bad_params"
    assert stack.dock.config.agent_host == "127.0.0.1"  # rien n'a bougé
    assert stack.dock.server.link.connected


async def test_changing_the_pc_address_reconnects_and_persists(stack):
    other = free_port()
    async with stack.dock.connect() as ws:
        await hello_and_topics(ws)
        ack = await call(ws, "s1", "dock.pc.set", {"host": "127.0.0.1", "port": other})
        assert ack["ok"] is True
        await eventually(
            lambda: stack.dock.server.link.status.state == LinkState.HOST_UP_AGENT_DOWN
        )
        link = await recv_until(
            ws, lambda m: m.get("topic") == "link" and m["data"]["state"] == "host_up_agent_down"
        )
        assert link["data"]["port"] == other
        await call(ws, "s2", "dock.pc.set", {"host": "127.0.0.1", "port": stack.agent.port})
        await eventually(lambda: stack.dock.server.link.connected)
    saved = load_toml(stack.dock.config.path)
    assert saved["pc"]["port"] == stack.agent.port


async def test_fixing_a_wrong_token_from_the_settings_screen_works(tmp_path):
    async with (
        running_agent() as agent,
        running_dock(tmp_path, agent_port=agent.port, token="x" * 40) as dock,
    ):
        await eventually(lambda: dock.server.link.status.state == LinkState.AUTH_FAILED)
        async with dock.connect() as ws:
            await hello_and_topics(ws)
            ack = await call(ws, "t1", "dock.token.set", {"token": agent.token})
            assert ack["ok"] is True
            await eventually(lambda: dock.server.link.connected, timeout=3)
    assert DockConfig.load(dock.config.path).token == agent.token


async def test_pc_test_gives_a_verdict_without_changing_anything(stack):
    async with stack.dock.connect() as ws:
        ok = await call(ws, "t1", "dock.pc.test")
        assert ok["r"]["state"] == "connected"
        bad = await call(ws, "t2", "dock.pc.test", {"token": "x" * 40})
        assert bad["r"]["state"] == "auth_failed"
        gone = await call(ws, "t3", "dock.pc.test", {"port": free_port()})
        assert gone["r"]["state"] == "host_up_agent_down"
        invalid = await call(ws, "t4", "dock.pc.test", {"host": "http://x"})
        assert invalid["e"]["code"] == "bad_params"
    assert stack.dock.server.link.connected  # la liaison réelle n'a pas bougé


# --- Wake-on-LAN ------------------------------------------------------------------------------


def test_wol_prefers_ethernet_even_when_unplugged_and_falls_back_to_wifi():
    assert pick_wol_interface(LAPTOP["interfaces"]) == {
        "mac": "02:00:5e:10:00:01",
        "iface": "enp3s0",
        "kind": "ethernet",
    }
    wifi_only = [i for i in LAPTOP["interfaces"] if i["kind"] == "wifi"]
    assert pick_wol_interface(wifi_only)["kind"] == "wifi"
    assert pick_wol_interface([]) is None
    assert pick_wol_interface([{"name": "x", "mac": "pas une mac", "kind": "ethernet"}]) is None


async def test_the_pcs_mac_address_is_learned_while_it_is_on(stack):
    async with stack.dock.connect() as ws:
        _, _, dock, _ = await hello_and_topics(ws)
    wol = dock["data"]["wol"]
    assert (wol["mac"], wol["source"], wol["kind"]) == ("02:00:5e:10:00:01", "auto", "ethernet")
    assert [i["kind"] for i in wol["interfaces"]] == ["ethernet", "wifi"]
    assert dock["data"]["pc"]["name"] == "portable"


async def test_wake_on_lan_sends_a_real_magic_packet(stack, monkeypatch):
    sink = await start_udp_sink()
    monkeypatch.setenv(TEST_TARGETS_ENV, f"127.0.0.1:{sink.port}")
    async with stack.dock.connect() as ws:
        ack = await call(ws, "w1", "dock.wol.send")
    assert ack["ok"] is True
    assert ack["r"]["mac"] == "02:00:5e:10:00:01"
    assert ack["r"]["sent"] == 1
    await eventually(lambda: sink.packets)
    assert sink.packets[0] == magic_packet("02:00:5e:10:00:01")


async def test_wake_on_lan_works_while_the_pc_is_off(tmp_path, monkeypatch):
    """Scénario réel : le dock a vu le PC allumé, puis le PC s'éteint. Le réveil doit marcher."""
    sink = await start_udp_sink()
    monkeypatch.setenv(TEST_TARGETS_ENV, f"127.0.0.1:{sink.port}")
    port = free_port()
    async with running_dock(tmp_path, agent_port=port) as dock:
        async with running_agent([ScriptedProvider("host", lazy=False, initial=LAPTOP)], port=port):
            await eventually(lambda: "02:00:5e:10:00:01" in json.dumps(dock.server.payload("dock")))
        await eventually(lambda: not dock.server.link.connected)
        async with dock.connect() as ws:
            ack = await call(ws, "w1", "dock.wol.send")
    assert ack["ok"] is True
    await eventually(lambda: sink.packets)


async def test_without_a_known_mac_the_user_is_told_what_to_do(tmp_path):
    async with running_dock(tmp_path) as dock, dock.connect() as ws:
        ack = await call(ws, "w1", "dock.wol.send")
    assert ack["e"]["code"] == "no_mac"
    assert "dès qu'il s'est connecté" in ack["e"]["msg"]


async def test_a_manual_mac_overrides_the_learned_one_and_can_be_cleared(stack):
    async with stack.dock.connect() as ws:
        await hello_and_topics(ws)
        await call(ws, "m1", "dock.wol.set_mac", {"mac": "02-00-5E-AA-BB-CC"})
        manual = await recv_until(
            ws, lambda m: m.get("topic") == "dock" and m["data"]["wol"]["source"] == "manual"
        )
        assert manual["data"]["wol"]["mac"] == "02:00:5e:aa:bb:cc"
        await call(ws, "m2", "dock.wol.set_mac", {"mac": None})
        auto = await recv_until(
            ws, lambda m: m.get("topic") == "dock" and m["data"]["wol"]["source"] == "auto"
        )
        assert auto["data"]["wol"]["mac"] == "02:00:5e:10:00:01"


# --- panne de l'agent ------------------------------------------------------------------------


async def test_cached_pc_state_is_still_served_when_the_pc_goes_off(tmp_path):
    port = free_port()
    async with running_dock(tmp_path, agent_port=port) as dock:
        async with running_agent([ScriptedProvider("host", lazy=False, initial=LAPTOP)], port=port):
            await eventually(lambda: dock.server.link.connected)
            await eventually(lambda: "host" in dock.server.link.snapshots)
        async with dock.connect() as ws:
            link = await snapshot_of(ws, "link")
            await send_json(ws, {"t": "sub", "topics": ["host"]})
            host = await snapshot_of(ws, "host")
    assert link["data"]["state"] in {"host_up_agent_down", "unreachable", "connecting"}
    assert host["data"]["hostname"] == "portable"


async def test_link_changes_are_pushed_to_the_browser(tmp_path):
    port = free_port()
    async with running_dock(tmp_path, agent_port=port) as dock, dock.connect() as ws:
        await hello_and_topics(ws)
        async with running_agent(port=port):
            link = await recv_until(
                ws, lambda m: m.get("topic") == "link" and m["data"]["state"] == "connected"
            )
            assert link["data"]["since"] > 0
        down = await recv_until(
            ws, lambda m: m.get("topic") == "link" and m["data"]["state"] == "host_up_agent_down"
        )
        assert down["data"]["next_retry_at"] is not None
        assert "agent MyDock" in down["data"]["detail"]


async def test_upstream_subscriptions_follow_the_browsers(stack):
    hub = stack.agent.hub
    async with stack.dock.connect() as first:
        await send_json(first, {"t": "sub", "topics": ["music"]})
        await eventually(lambda: hub.subscriber_count("music") == 1)
        async with stack.dock.connect() as second:
            await send_json(second, {"t": "sub", "topics": ["music"]})
            await snapshot_of(second, "music")
            assert hub.subscriber_count("music") == 1  # une seule demande vers l'agent
        await asyncio.sleep(0.1)
        assert hub.subscriber_count("music") == 1  # le premier navigateur est toujours là
    await eventually(lambda: hub.subscriber_count("music") == 0)
    assert hub.subscriber_count("host") == 1  # « host » reste toujours demandé


async def test_the_dock_always_follows_system_so_the_history_is_ready_when_the_page_opens(stack):
    hub = stack.agent.hub
    # Aucun navigateur connecté : le dock suit quand même « system » (l'agent garde 60 s d'historique).
    await eventually(lambda: hub.subscriber_count("system") == 1)
    assert hub.subscriber_count("procs") == 0  # les processus, eux, seulement page Système ouverte
    async with stack.dock.connect() as browser:
        await send_json(browser, {"t": "sub", "topics": ["procs"]})
        await eventually(lambda: hub.subscriber_count("procs") == 1)
        assert hub.subscriber_count("system") == 1  # toujours une seule demande
    await eventually(lambda: hub.subscriber_count("procs") == 0)
    assert hub.subscriber_count("system") == 1


# --- sécurité du serveur local --------------------------------------------------------------


async def test_foreign_origins_cannot_drive_the_dock(stack):
    with pytest.raises(InvalidStatus) as info:
        async with stack.dock.connect(origin="http://evil.example"):
            pass
    assert info.value.response.status_code == 403
    with pytest.raises(InvalidStatus):
        async with stack.dock.connect(origin="http://localhost:1"):  # autre application locale
            pass
    async with stack.dock.connect(origin=None) as ws:  # client sans Origin (outil local)
        assert (await recv_json(ws))["t"] == "hello"
    async with stack.dock.connect() as ws:
        assert (await recv_json(ws))["t"] == "hello"


async def test_dns_rebinding_is_refused(stack):
    async with httpx.AsyncClient(trust_env=False, base_url=stack.dock.origin) as client:
        good = await client.get("/api/health")
        assert good.status_code == 200
        rebound = await client.get("/api/health", headers={"Host": "evil.example"})
        assert rebound.status_code == 421


def test_host_and_origin_rules():
    local = DockConfig.from_dict({}, path=None)  # type: ignore[arg-type]
    assert host_allowed("127.0.0.1:8080", local)
    assert host_allowed("localhost:8080", local)
    assert host_allowed("localhost", local)
    assert not host_allowed("evil.example", local)
    assert not host_allowed("127.0.0.1.evil.example:8080", local)
    assert not host_allowed(None, local)
    assert origin_allowed(None, "127.0.0.1:8080")
    assert origin_allowed("http://127.0.0.1:8080", "127.0.0.1:8080")
    assert not origin_allowed("http://127.0.0.1:9", "127.0.0.1:8080")
    assert not origin_allowed("https://127.0.0.1:8080", "127.0.0.1:8080")
    assert not origin_allowed("http://x", None)


async def test_garbage_from_the_browser_is_answered_not_fatal(stack):
    async with stack.dock.connect() as ws:
        await hello_and_topics(ws)
        await ws.send("pas du json")
        assert (await recv_until(ws, lambda m: m["t"] == "err"))["code"] == "bad_message"
        await send_json(ws, {"t": "sub", "topics": ["inconnu"]})
        assert (await recv_until(ws, lambda m: m["t"] == "err"))["code"] == "unknown_topic"
        await send_json(ws, {"t": "ping", "n": 7})
        assert (await recv_until(ws, lambda m: m["t"] == "pong"))["n"] == 7


# --- fichiers de l'interface ----------------------------------------------------------------


async def test_static_files_are_served_with_safe_headers(tmp_path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>MyDock</title>")
    (web / "assets" / "app-abc123.js").write_text("console.log('ok')")
    async with (
        running_dock(tmp_path / "dock", web_dir=web) as dock,
        httpx.AsyncClient(trust_env=False, base_url=dock.origin) as client,
    ):
        index = await client.get("/")
        assert index.status_code == 200
        assert "MyDock" in index.text
        assert index.headers["cache-control"] == "no-cache"
        csp = index.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert index.headers["x-content-type-options"] == "nosniff"
        asset = await client.get("/assets/app-abc123.js")
        assert "immutable" in asset.headers["cache-control"]
        assert (await client.get("/inexistant")).status_code == 404
        health = (await client.get("/api/health")).json()
        assert health["ok"] is True


async def test_a_missing_interface_explains_itself(tmp_path):
    async with (
        running_dock(tmp_path, web_dir=tmp_path / "absent") as dock,
        httpx.AsyncClient(trust_env=False, base_url=dock.origin) as client,
    ):
        page = await client.get("/")
    assert page.status_code == 503
    assert "Interface non installée" in page.text
