"""Tests de bout en bout du serveur de l'agent : vrai uvicorn, vrai client WebSocket."""

import asyncio
import ipaddress

import pytest
from websockets.exceptions import InvalidStatus

from mydock_agent.access import AccessGate
from mydock_agent.server import GateMiddleware
from mydock_agent.testing import (
    TOKEN,
    ScriptedProvider,
    recv_json,
    recv_until,
    running_agent,
    send_json,
    wait_closed,
)
from mydock_common import protocol as proto


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_connection_without_a_token_is_refused(agent):
    with pytest.raises(InvalidStatus) as info:
        async with agent.connect(headers={}):
            pass
    assert info.value.response.status_code == 403


async def test_connection_with_a_wrong_token_is_refused(agent):
    with pytest.raises(InvalidStatus) as info:
        async with agent.connect(token="x" * 40):
            pass
    assert info.value.response.status_code == 403


async def test_token_in_the_url_is_not_accepted(agent):
    from websockets.asyncio.client import connect

    with pytest.raises(InvalidStatus):
        async with connect(f"{agent.url}?token={TOKEN}", open_timeout=3):
            pass


async def test_hello_describes_the_agent(agent):
    async with agent.connect() as ws:
        hello = await recv_json(ws)
    assert hello["t"] == "hello"
    assert hello["proto"] == proto.PROTOCOL_VERSION
    assert hello["agent"]["demo"] is True
    assert hello["agent"]["topics"] == ["host"]
    assert hello["agent"]["actions"] == ["diag.confirm_demo", "diag.ping"]


async def test_subscribing_returns_the_current_state(agent):
    async with agent.connect() as ws:
        await recv_json(ws)  # hello
        await send_json(ws, {"t": "sub", "topics": ["host"]})
        snap = await recv_json(ws)
    assert snap == {"t": "snap", "topic": "host", "rev": 1, "data": {"n": 0}}


async def test_only_changes_are_pushed(agent):
    provider: ScriptedProvider = agent.hub._providers["host"]
    async with agent.connect() as ws:
        await recv_json(ws)
        await send_json(ws, {"t": "sub", "topics": ["host"]})
        await recv_until(ws, lambda m: m["t"] == "snap")

        assert await provider.push({"n": 1}) is True
        first = await recv_json(ws)
        assert first["data"] == {"n": 1}

        assert await provider.push({"n": 1}) is False  # identique : rien ne part
        assert await provider.push({"n": 2}) is True
        second = await recv_json(ws)
        assert second["data"] == {"n": 2}
        assert second["rev"] == first["rev"] + 1
        with pytest.raises(TimeoutError):
            await recv_json(ws, timeout=0.2)


async def test_unsubscribed_topics_stop_flowing(agent):
    provider = agent.hub._providers["host"]
    async with agent.connect() as ws:
        await recv_json(ws)
        await send_json(ws, {"t": "sub", "topics": ["host"]})
        await recv_until(ws, lambda m: m["t"] == "snap")
        await send_json(ws, {"t": "unsub", "topics": ["host"]})
        await send_json(ws, {"t": "ping", "n": 1})
        await recv_until(ws, lambda m: m["t"] == "pong")
        await provider.push({"n": 5})
        with pytest.raises(TimeoutError):
            await recv_json(ws, timeout=0.2)


async def test_ping_pong_echoes_the_counter(agent):
    async with agent.connect() as ws:
        await recv_json(ws)
        await send_json(ws, {"t": "ping", "n": 42})
        pong = await recv_json(ws)
    assert pong["t"] == "pong"
    assert pong["n"] == 42
    assert pong["ts"] > 1_700_000_000_000


async def test_bad_messages_get_an_error_but_keep_the_connection(agent):
    async with agent.connect() as ws:
        await recv_json(ws)
        await send_json(ws, {"t": "nope"})
        assert (await recv_json(ws))["code"] == "unknown_type"
        await ws.send("pas du json")
        assert (await recv_json(ws))["code"] == "bad_message"
        await ws.send(b"\x00\x01")
        assert (await recv_json(ws))["code"] == "bad_message"
        await send_json(ws, {"t": "sub", "topics": ["inconnu"]})
        assert (await recv_json(ws))["code"] == "unknown_topic"
        await send_json(ws, {"t": "ping", "n": 1})
        assert (await recv_json(ws))["t"] == "pong"  # toujours vivant


async def test_a_flood_of_garbage_closes_the_connection(agent):
    async with agent.connect() as ws:
        await recv_json(ws)
        for _ in range(proto.MAX_TOPICS_PER_MESSAGE):
            await ws.send("poubelle")
        closed = await wait_closed(ws)
    assert closed.rcvd is not None


async def test_oversized_messages_close_the_connection(agent):
    async with agent.connect() as ws:
        await recv_json(ws)
        await ws.send("x" * (proto.MAX_MESSAGE_BYTES + 10))
        closed = await wait_closed(ws)
    assert closed.rcvd is not None
    assert closed.rcvd.code == 1009


async def test_actions_roundtrip(agent):
    async with agent.connect() as ws:
        await recv_json(ws)
        await send_json(ws, {"t": "act", "id": "a1", "a": "diag.ping"})
        ok = await recv_json(ws)
        assert ok["id"] == "a1"
        assert ok["ok"] is True
        assert "agent_ms" in ok["r"]

        await send_json(ws, {"t": "act", "id": "a2", "a": "rm.rf"})
        assert (await recv_json(ws))["e"]["code"] == "unknown_action"

        await send_json(ws, {"t": "act", "id": "a3", "a": "diag.ping", "p": {"x": 1}})
        assert (await recv_json(ws))["e"]["code"] == "bad_params"


async def test_sensitive_action_requires_on_screen_confirmation(agent):
    async with agent.connect() as ws:
        await recv_json(ws)
        await send_json(ws, {"t": "act", "id": "s1", "a": "diag.confirm_demo"})
        needs = await recv_json(ws)
        assert needs["e"]["code"] == "confirm_required"
        token = needs["confirm"]["token"]
        await send_json(ws, {"t": "act", "id": "s2", "a": "diag.confirm_demo", "confirm": token})
        assert (await recv_json(ws))["ok"] is True
        await send_json(ws, {"t": "act", "id": "s3", "a": "diag.confirm_demo", "confirm": token})
        assert (await recv_json(ws))["e"]["code"] == "confirm_invalid"


async def test_confirmation_cannot_be_used_from_another_connection(agent):
    async with agent.connect() as first, agent.connect() as second:
        await recv_json(first)
        await recv_json(second)
        await send_json(first, {"t": "act", "id": "s1", "a": "diag.confirm_demo"})
        token = (await recv_json(first))["confirm"]["token"]
        await send_json(
            second, {"t": "act", "id": "s2", "a": "diag.confirm_demo", "confirm": token}
        )
        assert (await recv_json(second))["e"]["code"] == "confirm_invalid"


async def test_a_slow_action_does_not_block_other_messages(agent):
    async def slow(_ctx, _params):
        await asyncio.sleep(0.4)
        return {"slept": True}

    agent.registry.register("diag.slow", slow, timeout=2)
    async with agent.connect() as ws:
        await recv_json(ws)
        await send_json(ws, {"t": "act", "id": "slow", "a": "diag.slow"})
        await send_json(ws, {"t": "ping", "n": 1})
        first = await recv_json(ws)
        assert first["t"] == "pong"  # le pong passe avant la fin de l'action lente
        assert (await recv_json(ws, timeout=2))["id"] == "slow"


async def test_lazy_topics_start_on_subscribe_and_stop_on_disconnect():
    system = ScriptedProvider("system", lazy=True)
    async with running_agent([ScriptedProvider("host", lazy=False), system]) as agent:
        await asyncio.sleep(0.05)
        assert system.starts == 0
        async with agent.connect() as ws:
            await recv_json(ws)
            await send_json(ws, {"t": "sub", "topics": ["system"]})
            await recv_until(ws, lambda m: m["t"] == "snap")
            assert system.starts == 1
        await asyncio.sleep(0.2)
        assert system.stops == 1  # la déconnexion a désabonné et arrêté le collecteur


async def test_repeated_bad_tokens_block_the_address_then_it_recovers():
    clock = FakeClock()
    async with running_agent(max_failures=3, block=300, clock=clock) as agent:
        for _ in range(3):
            with pytest.raises(InvalidStatus):
                async with agent.connect(token="x" * 40):
                    pass
        with pytest.raises(InvalidStatus):  # bloqué : même le bon jeton est refusé
            async with agent.connect():
                pass
        clock.now += 301
        async with agent.connect() as ws:
            assert (await recv_json(ws))["t"] == "hello"


# --- barrière par adresse, testée au niveau ASGI (tous les clients de test sont en 127.0.0.1) ---


class Recorder:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.inner_called = False

    async def send(self, message):
        self.sent.append(message)

    async def receive(self):
        return {"type": "websocket.connect"}

    async def inner(self, scope, receive, send):
        self.inner_called = True


def middleware(recorder: Recorder) -> GateMiddleware:
    gate = AccessGate(TOKEN, (ipaddress.ip_network("192.0.2.50/32"),))
    return GateMiddleware(recorder.inner, gate)


async def test_unlisted_http_client_gets_an_empty_403():
    rec = Recorder()
    await middleware(rec)({"type": "http", "client": ("203.0.113.9", 4000)}, rec.receive, rec.send)
    assert not rec.inner_called
    assert rec.sent[0]["status"] == 403
    assert rec.sent[1]["body"] == b""


async def test_unlisted_websocket_client_is_closed_before_accept():
    rec = Recorder()
    scope = {"type": "websocket", "client": ("203.0.113.9", 4000)}
    await middleware(rec)(scope, rec.receive, rec.send)
    assert not rec.inner_called
    assert rec.sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.parametrize("client", [("192.0.2.50", 1), ("127.0.0.1", 1), ("::ffff:192.0.2.50", 1)])
async def test_listed_clients_reach_the_application(client):
    rec = Recorder()
    await middleware(rec)({"type": "websocket", "client": client}, rec.receive, rec.send)
    assert rec.inner_called


async def test_missing_or_non_ip_client_is_refused():
    for client in (None, ("testclient", 5000)):
        rec = Recorder()
        await middleware(rec)({"type": "http", "client": client}, rec.receive, rec.send)
        assert not rec.inner_called


async def test_lifespan_passes_through():
    rec = Recorder()
    await middleware(rec)({"type": "lifespan"}, rec.receive, rec.send)
    assert rec.inner_called
