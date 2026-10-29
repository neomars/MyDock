"""Le lien du dock vers l'agent : connexion, classification des pannes, reconnexion, actions."""

import asyncio
import errno
import socket

import pytest
from websockets.asyncio.server import serve

from mydock_agent.testing import TOKEN, ScriptedProvider, running_agent
from mydock_common import protocol as proto
from mydock_dock import agent_link
from mydock_dock.agent_link import (
    SLOW_RETRY,
    AgentLink,
    LinkFailure,
    LinkSettings,
    LinkState,
    LinkStatus,
    backoff_delay,
    classify_os_error,
    open_session,
    probe,
    resolve_ipv4,
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
    assert await resolve_ipv4("192.0.2.7", 8765) == "192.0.2.7"


async def test_unknown_names_are_explained():
    with pytest.raises(LinkFailure) as info:
        await resolve_ipv4("introuvable.invalid", 8765, timeout=2)
    assert info.value.state == LinkState.UNREACHABLE
    assert "introuvable" in info.value.detail
    assert "adresse IP" in info.value.detail


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
