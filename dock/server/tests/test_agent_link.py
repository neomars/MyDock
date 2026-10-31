"""Le lien du dock vers l'agent : connexion, classification des pannes, reconnexion, actions."""

import asyncio
import errno
import logging
import socket

import pytest
from websockets.asyncio.server import serve

from mydock_agent.testing import TOKEN, ScriptedProvider, running_agent
from mydock_common import protocol as proto
from mydock_dock import agent_link
from mydock_dock.agent_link import (
    MAX_CANDIDATES,
    SLOW_RETRY,
    AgentLink,
    LinkFailure,
    LinkSettings,
    LinkState,
    LinkStatus,
    backoff_delay,
    classify_os_error,
    most_informative,
    open_session,
    probe,
    resolve_ipv4_all,
    target_label,
)
from mydock_dock.testing import eventually, free_port


class Harness:
    """Fait tourner un AgentLink et garde la trace de ses changements d'état."""

    def __init__(self, settings: LinkSettings):
        self.settings = settings
        self.statuses: list[LinkStatus] = []
        self.snapshots: list[tuple[str, int, dict]] = []
        self.link = AgentLink(
            lambda: self.settings,
            on_status=self.statuses.append,
            on_snapshot=lambda topic, rev, data: self.snapshots.append((topic, rev, data)),
            backoff=lambda _attempt: 0.05,
        )
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "Harness":
        self._task = asyncio.create_task(self.link.run())
        return self

    async def __aexit__(self, *exc) -> None:
        assert self._task is not None
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        await self.link.close()

    @property
    def state(self) -> LinkState:
        return self.link.status.state

    async def reaches(self, state: LinkState, timeout: float = 5.0) -> None:
        await eventually(lambda: self.state == state, timeout)


def settings_for(agent, token: str = TOKEN) -> LinkSettings:
    return LinkSettings("127.0.0.1", agent.port, token)


# --- délais, résolution, classification -------------------------------------------------------


def test_backoff_follows_the_documented_steps():
    middle = lambda: 0.5  # noqa: E731 - pas de variation aléatoire
    assert [backoff_delay(a, jitter=0.15, rand=middle) for a in range(0, 9)] == [
        1,
        1,
        2,
        3,
        5,
        8,
        10,
        10,
        10,
    ]


def test_backoff_jitter_stays_within_15_percent():
    assert backoff_delay(3, rand=lambda: 0.0) == pytest.approx(3 * 0.85)
    assert backoff_delay(3, rand=lambda: 1.0) == pytest.approx(3 * 1.15)


async def test_ip_literals_are_not_resolved():
    assert await resolve_ipv4_all("192.0.2.7", 8765) == ["192.0.2.7"]
    assert await resolve_ipv4_all("127.0.0.1", 8765) == ["127.0.0.1"]  # saisie telle quelle


async def test_unknown_names_are_explained():
    with pytest.raises(LinkFailure) as info:
        await resolve_ipv4_all("introuvable.invalid", 8765, timeout=2)
    assert info.value.state == LinkState.UNREACHABLE
    assert info.value.address is None
    assert "introuvable" in info.value.detail
    assert "adresse IP" in info.value.detail


def fake_dns(monkeypatch, *addresses: str) -> None:
    """Le résolveur renvoie ces adresses IPv4, dans cet ordre (et rien d'autre)."""

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        assert family == socket.AF_INET  # l'IPv6 n'est jamais demandée
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


async def test_a_name_gives_every_ipv4_once_with_lan_addresses_first(monkeypatch):
    fake_dns(monkeypatch, "127.0.1.1", "192.168.1.3", "192.168.1.3", "169.254.3.4", "172.17.0.1")
    assert await resolve_ipv4_all("ubuntu.local", 8765) == [
        "192.168.1.3",
        "172.17.0.1",
        "127.0.1.1",
    ]


async def test_no_more_candidates_than_the_limit(monkeypatch):
    fake_dns(monkeypatch, *(f"10.0.0.{n}" for n in range(1, 9)))
    assert len(await resolve_ipv4_all("pc.local", 8765)) == MAX_CANDIDATES


async def test_a_name_that_only_gives_loopback_is_still_tried(monkeypatch):
    fake_dns(monkeypatch, "127.0.1.1")
    assert await resolve_ipv4_all("ce-pi.local", 8765) == ["127.0.1.1"]


async def test_a_name_without_any_ipv4_is_unknown(monkeypatch):
    fake_dns(monkeypatch)
    with pytest.raises(LinkFailure) as info:
        await resolve_ipv4_all("pc.local", 8765)
    assert "introuvable" in info.value.detail


def test_targets_show_the_typed_name_and_the_address_used():
    assert target_label("ubuntu.local", "192.168.1.3", 8765) == "ubuntu.local (192.168.1.3:8765)"
    assert target_label("192.168.1.3", "192.168.1.3", 8765) == "192.168.1.3:8765"


@pytest.mark.parametrize(
    ("exc", "state"),
    [
        (ConnectionRefusedError(), LinkState.HOST_UP_AGENT_DOWN),
        (TimeoutError(), LinkState.UNREACHABLE),
        (OSError(errno.EHOSTUNREACH, "no route"), LinkState.UNREACHABLE),
        (OSError(errno.ENETUNREACH, "net"), LinkState.UNREACHABLE),
        (OSError(errno.EIO, "autre"), LinkState.UNREACHABLE),
    ],
)
def test_os_errors_say_where_the_problem_is(exc, state):
    assert classify_os_error(exc, 8765).state == state


def test_os_error_messages_name_the_address_that_was_tried():
    refused = classify_os_error(ConnectionRefusedError(), 8765, "192.168.1.3:8765").detail
    silent = classify_os_error(TimeoutError(), 8765, "192.168.1.3:8765").detail
    assert "192.168.1.3:8765" in refused
    assert "n'écoute pas" in refused
    assert "192.168.1.3:8765" in silent
    assert "pare-feu" in silent  # la cause la plus courante d'un PC allumé mais muet
    assert "port 8765" in classify_os_error(TimeoutError(), 8765).detail  # sans adresse connue


def fail(state: LinkState, address: str) -> LinkFailure:
    return LinkFailure(state, f"échec {state} sur {address}", address)


def test_the_failure_closest_to_the_agent_is_the_one_reported():
    best = most_informative(
        [fail(LinkState.UNREACHABLE, "10.0.0.1"), fail(LinkState.HOST_UP_AGENT_DOWN, "10.0.0.2")]
    )
    assert best.state == LinkState.HOST_UP_AGENT_DOWN
    assert best.address == "10.0.0.2"
    assert "Autres adresses essayées : 10.0.0.1" in best.detail
    best = most_informative(
        [fail(LinkState.HOST_UP_AGENT_DOWN, "10.0.0.1"), fail(LinkState.AUTH_FAILED, "10.0.0.2")]
    )
    assert best.state == LinkState.AUTH_FAILED


def test_a_single_failure_is_reported_as_is():
    only = fail(LinkState.UNREACHABLE, "10.0.0.1")
    assert most_informative([only]) is only


# --- ouverture de session ---------------------------------------------------------------------


async def test_open_session_returns_hello_and_resolved_address():
    async with running_agent() as agent:
        ws, hello, ip = await open_session(settings_for(agent))
        await ws.close()
    assert hello["t"] == "hello"
    assert ip == "127.0.0.1"


async def test_a_port_with_nothing_listening_means_the_agent_is_down():
    with pytest.raises(LinkFailure) as info:
        await open_session(LinkSettings("127.0.0.1", free_port(), TOKEN))
    assert info.value.state == LinkState.HOST_UP_AGENT_DOWN


async def test_a_peer_that_never_answers_the_handshake_is_reported_unreachable():
    async def say_nothing(reader, writer):
        await asyncio.sleep(5)

    server = await asyncio.start_server(say_nothing, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(LinkFailure) as info:
            await open_session(LinkSettings("127.0.0.1", port, TOKEN), open_timeout=0.3)
    finally:
        server.close()
    assert info.value.state == LinkState.UNREACHABLE
    assert "ne répond pas" in info.value.detail


async def test_wrong_token_is_reported_as_such():
    async with running_agent() as agent:
        with pytest.raises(LinkFailure) as info:
            await open_session(settings_for(agent, "x" * 40))
    assert info.value.state == LinkState.AUTH_FAILED
    assert "jeton" in info.value.detail


async def test_another_web_service_on_the_port_is_not_mistaken_for_the_agent():
    async def reply(reader, writer):
        await reader.read(1024)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(reply, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(LinkFailure) as info:
            await open_session(LinkSettings("127.0.0.1", port, TOKEN))
    finally:
        server.close()
    assert info.value.state == LinkState.HOST_UP_AGENT_DOWN
    assert "pas l'agent MyDock" in info.value.detail


async def test_incompatible_protocol_versions_are_reported():
    async def handler(ws):
        await ws.send(proto.dumps({"t": "hello", "proto": 99, "agent": {}}))
        await ws.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(LinkFailure) as info:
            await open_session(LinkSettings("127.0.0.1", port, TOKEN))
    assert info.value.state == LinkState.PROTOCOL_MISMATCH
    assert "99" in info.value.detail


async def test_an_agent_that_never_says_hello_is_given_up_on(monkeypatch):
    monkeypatch.setattr(agent_link, "HELLO_TIMEOUT", 0.2)

    async def handler(ws):
        await ws.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(LinkFailure) as info:
            await open_session(LinkSettings("127.0.0.1", port, TOKEN))
    assert info.value.state == LinkState.HOST_UP_AGENT_DOWN
    assert "n'a pas répondu" in info.value.detail


async def test_failures_name_the_address_that_was_tried():
    port = free_port()
    with pytest.raises(LinkFailure) as info:
        await open_session(LinkSettings("127.0.0.1", port, TOKEN))
    assert info.value.address == "127.0.0.1"
    assert f"127.0.0.1:{port}" in info.value.detail


async def test_silence_names_the_address_too():
    async def say_nothing(reader, writer):
        await asyncio.sleep(5)

    server = await asyncio.start_server(say_nothing, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(LinkFailure) as info:
            await open_session(LinkSettings("127.0.0.1", port, TOKEN), open_timeout=0.3)
    finally:
        server.close()
    assert f"127.0.0.1:{port}" in info.value.detail


async def test_a_refused_token_tells_which_address_the_agent_saw():
    async with running_agent() as agent:
        with pytest.raises(LinkFailure) as info:
            await open_session(settings_for(agent, "x" * 40))
    assert "ce Pi (127.0.0.1)" in info.value.detail  # à comparer avec allowed_clients du PC
    assert info.value.address == "127.0.0.1"


async def test_the_next_address_is_tried_when_the_first_refuses(monkeypatch):
    # 127.0.0.2 est une adresse de ce même ordinateur, mais l'agent n'écoute que sur 127.0.0.1.
    async def candidates(host, port, timeout=5.0):
        return ["127.0.0.2", "127.0.0.1"]

    monkeypatch.setattr(agent_link, "resolve_ipv4_all", candidates)
    async with running_agent() as agent:
        ws, hello, ip = await open_session(LinkSettings("pc.local", agent.port, TOKEN))
        await ws.close()
    assert ip == "127.0.0.1"
    assert hello["t"] == "hello"


async def test_all_addresses_failing_reports_the_most_telling_failure(monkeypatch):
    async def candidates(host, port, timeout=5.0):
        return ["127.0.0.2", "127.0.0.3"]

    monkeypatch.setattr(agent_link, "resolve_ipv4_all", candidates)
    port = free_port()
    with pytest.raises(LinkFailure) as info:
        await open_session(LinkSettings("pc.local", port, TOKEN))
    assert info.value.state == LinkState.HOST_UP_AGENT_DOWN
    assert f"pc.local (127.0.0.2:{port})" in info.value.detail
    assert "Autres adresses essayées : 127.0.0.3" in info.value.detail


async def test_the_address_that_worked_last_time_is_tried_first(monkeypatch):
    async def candidates(host, port, timeout=5.0):
        return ["10.0.0.1", "10.0.0.2", "10.0.0.3"]

    tried: list[str] = []

    async def refuse(ip, settings, open_timeout, ping_interval):
        tried.append(ip)
        raise LinkFailure(LinkState.UNREACHABLE, "silence", ip)

    monkeypatch.setattr(agent_link, "resolve_ipv4_all", candidates)
    monkeypatch.setattr(agent_link, "_open_at", refuse)
    with pytest.raises(LinkFailure):
        await open_session(LinkSettings("pc.local", 8765, TOKEN), prefer="10.0.0.3")
    assert tried == ["10.0.0.3", "10.0.0.1", "10.0.0.2"]
    tried.clear()
    with pytest.raises(LinkFailure):
        await open_session(LinkSettings("pc.local", 8765, TOKEN), prefer="192.0.2.99")  # inconnue
    assert tried == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]


async def test_a_refused_token_stops_the_search(monkeypatch):
    """Insister sur d'autres adresses ferait compter d'autres échecs d'authentification."""

    async def candidates(host, port, timeout=5.0):
        return ["10.0.0.1", "10.0.0.2", "10.0.0.3"]

    tried: list[str] = []

    async def open_at(ip, settings, open_timeout, ping_interval):
        tried.append(ip)
        raise LinkFailure(
            LinkState.AUTH_FAILED if ip == "10.0.0.2" else LinkState.UNREACHABLE, "x", ip
        )

    monkeypatch.setattr(agent_link, "resolve_ipv4_all", candidates)
    monkeypatch.setattr(agent_link, "_open_at", open_at)
    with pytest.raises(LinkFailure) as info:
        await open_session(LinkSettings("pc.local", 8765, TOKEN))
    assert info.value.state == LinkState.AUTH_FAILED
    assert tried == ["10.0.0.1", "10.0.0.2"]


async def test_probe_gives_a_verdict_without_side_effects():
    async with running_agent() as agent:
        state, detail, info = await probe(settings_for(agent))
        assert (state, detail) == (LinkState.CONNECTED, "Connexion réussie.")
        assert info["demo"] is True
        state, _, info = await probe(settings_for(agent, "x" * 40))
        assert state == LinkState.AUTH_FAILED
        assert info is None


# --- le lien vivant ----------------------------------------------------------------------------


async def test_link_connects_and_receives_snapshots():
    host = ScriptedProvider("host", lazy=False, initial={"hostname": "pc"})
    async with running_agent([host]) as agent, Harness(settings_for(agent)) as h:
        h.link.set_subscriptions({"host"})
        await h.reaches(LinkState.CONNECTED)
        await eventually(lambda: "host" in h.link.snapshots)
        assert h.link.snapshots["host"]["data"] == {"hostname": "pc"}
        assert h.snapshots[0][0] == "host"
        assert h.link.status.agent["demo"] is True
        assert h.link.status.resolved == "127.0.0.1"


async def test_the_address_being_tried_stays_in_the_status_during_an_outage():
    port = free_port()
    async with Harness(LinkSettings("127.0.0.1", port, TOKEN)) as h:
        await h.reaches(LinkState.HOST_UP_AGENT_DOWN)
        assert h.link.status.resolved == "127.0.0.1"
        assert f"127.0.0.1:{port}" in h.link.status.detail
        assert h.link.status.to_dict()["resolved"] == "127.0.0.1"


async def test_an_outage_is_logged_once_not_at_every_retry(caplog):
    caplog.set_level(logging.WARNING, logger="mydock_dock.agent_link")
    async with Harness(LinkSettings("127.0.0.1", free_port(), TOKEN)) as h:
        await h.reaches(LinkState.HOST_UP_AGENT_DOWN)
        await eventually(lambda: h.link.status.attempt >= 4, timeout=5)
    outage = [r for r in caplog.records if "Liaison avec l'agent" in r.getMessage()]
    assert len(outage) == 1
    assert "n'écoute pas" in outage[0].getMessage()


async def test_not_configured_until_settings_arrive():
    async with running_agent() as agent, Harness(LinkSettings()) as h:
        await h.reaches(LinkState.NOT_CONFIGURED)
        h.settings = settings_for(agent)
        h.link.reload()
        await h.reaches(LinkState.CONNECTED)


async def test_it_reconnects_by_itself_when_the_agent_restarts():
    port = free_port()
    async with Harness(LinkSettings("127.0.0.1", port, TOKEN)) as h:
        async with running_agent(port=port):
            await h.reaches(LinkState.CONNECTED)
            connected_since = h.link.status.since
        await h.reaches(LinkState.HOST_UP_AGENT_DOWN)  # agent arrêté, machine allumée
        assert h.link.status.attempt >= 1
        assert h.link.status.next_retry_at is not None
        async with running_agent(port=port):
            await h.reaches(LinkState.CONNECTED)
            assert h.link.status.since > connected_since


async def test_a_wrong_token_slows_down_retries_instead_of_hammering():
    async with running_agent() as agent, Harness(settings_for(agent, "x" * 40)) as h:
        await h.reaches(LinkState.AUTH_FAILED)
        status = h.link.status
        assert status.next_retry_at - (status.since) >= SLOW_RETRY - 1


async def test_fixing_the_token_reconnects_immediately():
    async with running_agent() as agent, Harness(settings_for(agent, "x" * 40)) as h:
        await h.reaches(LinkState.AUTH_FAILED)
        h.settings = settings_for(agent)
        h.link.reload()
        await h.reaches(LinkState.CONNECTED, timeout=2)  # sans attendre les 30 s


async def test_reload_drops_a_live_connection_to_apply_new_settings():
    async with running_agent() as agent, Harness(settings_for(agent)) as h:
        await h.reaches(LinkState.CONNECTED)
        h.settings = settings_for(agent, "x" * 40)
        h.link.reload()
        await h.reaches(LinkState.AUTH_FAILED)


# --- actions et abonnements ---------------------------------------------------------------------


async def test_request_returns_the_agent_ack_with_link_timing():
    async with running_agent() as agent, Harness(settings_for(agent)) as h:
        await h.reaches(LinkState.CONNECTED)
        reply = await h.link.request("diag.ping")
        assert reply["ok"] is True
        assert reply["hop"]["link_ms"] >= 0
        assert (await h.link.request("rm.rf"))["e"]["code"] == "unknown_action"


async def test_request_while_offline_fails_fast():
    async with Harness(LinkSettings("127.0.0.1", free_port(), TOKEN)) as h:
        reply = await asyncio.wait_for(h.link.request("diag.ping"), 1)
    assert reply["ok"] is False
    assert reply["e"]["code"] == "pc_offline"


async def test_requests_in_flight_fail_cleanly_when_the_connection_drops():
    port = free_port()
    async with Harness(LinkSettings("127.0.0.1", port, TOKEN)) as h:
        async with running_agent(port=port) as agent:

            async def slow(_ctx, _params):
                await asyncio.sleep(30)

            agent.registry.register("diag.slow", slow, timeout=60)
            await h.reaches(LinkState.CONNECTED)
            pending = asyncio.create_task(h.link.request("diag.slow", timeout=20))
            await asyncio.sleep(0.2)
        reply = await asyncio.wait_for(pending, 5)
    assert reply["e"]["code"] == "pc_offline"


async def test_slow_agent_answers_time_out():
    async with running_agent() as agent, Harness(settings_for(agent)) as h:

        async def slow(_ctx, _params):
            await asyncio.sleep(5)

        agent.registry.register("diag.slow", slow, timeout=10)
        await h.reaches(LinkState.CONNECTED)
        reply = await h.link.request("diag.slow", timeout=0.2)
    assert reply["e"]["code"] == "timeout"


async def test_subscriptions_follow_the_wanted_topics_and_survive_reconnections():
    port = free_port()
    providers = lambda: [ScriptedProvider("host", lazy=False), ScriptedProvider("music")]  # noqa: E731
    async with Harness(LinkSettings("127.0.0.1", port, TOKEN)) as h:
        h.link.set_subscriptions({"host", "music"})
        async with running_agent(providers(), port=port) as agent:
            await h.reaches(LinkState.CONNECTED)
            await eventually(lambda: agent.hub.subscriber_count("music") == 1)
            h.link.set_subscriptions({"host"})
            await eventually(lambda: agent.hub.subscriber_count("music") == 0)
            h.link.set_subscriptions({"host", "music"})
            await eventually(lambda: agent.hub.subscriber_count("music") == 1)
        async with running_agent(providers(), port=port) as agent:
            await eventually(lambda: agent.hub.subscriber_count("music") == 1, timeout=8)
            assert agent.hub.subscriber_count("host") == 1


def test_free_port_helper_gives_usable_ports():
    port = free_port()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))
