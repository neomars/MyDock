import asyncio
import json

import pytest

from mydock_agent import hub as hub_module
from mydock_agent.hub import ClientConnection, Hub, Provider
from mydock_agent.testing import ScriptedProvider
from mydock_common.protocol import ErrorCode, ProtocolError


async def drain(conn: ClientConnection) -> list[dict]:
    """Messages en attente pour ce client (instantanés seulement)."""
    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)

    conn.outbox.close()
    await conn.outbox.run(send)
    return [json.loads(text) for text in sent]


async def test_publish_only_broadcasts_changes():
    provider = ScriptedProvider("music", lazy=False)
    hub = Hub([provider])
    conn = ClientConnection("c")
    await hub.start()
    await asyncio.sleep(0.01)
    hub.subscribe(conn, ["music"])
    assert await provider.push({"n": 1}) is True
    assert await provider.push({"n": 1}) is False  # identique : rien n'est envoyé
    assert await provider.push({"n": 2}) is True
    messages = await drain(conn)
    assert [m["data"] for m in messages] == [{"n": 2}]  # le client ne reçoit que le dernier état
    assert messages[0]["rev"] == 3  # 0 (initial) -> 1 -> 2 : trois états distincts
    await hub.stop()


async def test_key_order_does_not_count_as_a_change():
    provider = ScriptedProvider("music", lazy=False, initial={"a": 1, "b": 2})
    hub = Hub([provider])
    await hub.start()
    await asyncio.sleep(0.01)
    assert await provider.push({"b": 2, "a": 1}) is False
    await hub.stop()


async def test_new_subscriber_gets_the_current_state_immediately():
    provider = ScriptedProvider("host", lazy=False, initial={"hostname": "pc"})
    hub = Hub([provider])
    await hub.start()
    await asyncio.sleep(0.01)
    conn = ClientConnection("c")
    hub.subscribe(conn, ["host"])
    messages = await drain(conn)
    assert messages == [{"t": "snap", "topic": "host", "rev": 1, "data": {"hostname": "pc"}}]
    await hub.stop()


async def test_lazy_provider_runs_only_while_someone_is_subscribed():
    provider = ScriptedProvider("system", lazy=True)
    hub = Hub([provider])
    await hub.start()
    await asyncio.sleep(0.02)
    assert provider.starts == 0
    assert hub.running_topics() == []

    first, second = ClientConnection("a"), ClientConnection("b")
    hub.subscribe(first, ["system"])
    await asyncio.sleep(0.02)
    assert provider.starts == 1
    hub.subscribe(second, ["system"])
    await asyncio.sleep(0.02)
    assert provider.starts == 1  # pas de second démarrage

    hub.unsubscribe(first, ["system"])
    await asyncio.sleep(0.02)
    assert provider.stops == 0  # il reste un abonné
    hub.detach(second)  # la déconnexion désabonne aussi
    await asyncio.sleep(0.02)
    assert provider.stops == 1
    assert hub.snapshot("system") is None  # pas d'état périmé pour le prochain abonné

    hub.subscribe(ClientConnection("c"), ["system"])
    await asyncio.sleep(0.02)
    assert provider.starts == 2
    await hub.stop()


async def test_always_on_provider_is_not_stopped_by_unsubscribing():
    provider = ScriptedProvider("host", lazy=False)
    hub = Hub([provider])
    await hub.start()
    conn = ClientConnection("c")
    hub.subscribe(conn, ["host"])
    hub.detach(conn)
    await asyncio.sleep(0.02)
    assert provider.stops == 0
    assert hub.snapshot("host") == {"n": 0}
    await hub.stop()


async def test_unknown_topic_is_a_protocol_error():
    hub = Hub([ScriptedProvider("host", lazy=False)])
    with pytest.raises(ProtocolError) as info:
        hub.subscribe(ClientConnection("c"), ["host", "nimportequoi"])
    assert info.value.code == ErrorCode.UNKNOWN_TOPIC


async def test_known_topic_without_provider_reports_unavailable():
    hub = Hub([])
    conn = ClientConnection("c")
    hub.subscribe(conn, ["steam"])
    [message] = await drain(conn)
    assert message["topic"] == "steam"
    assert "unavailable" in message["data"]


def test_provider_topics_are_validated():
    hub = Hub([ScriptedProvider("host")])
    with pytest.raises(ValueError, match="inconnu"):
        hub.add_provider(ScriptedProvider("pas_un_theme"))
    with pytest.raises(ValueError, match="déjà"):
        hub.add_provider(ScriptedProvider("host"))


async def test_crashing_provider_is_restarted_and_flagged(monkeypatch):
    monkeypatch.setattr(hub_module, "RESTART_DELAY", 0.05)

    class Flaky(Provider):
        topic = "power"
        lazy = False
        attempts = 0

        async def run(self, publish):
            Flaky.attempts += 1
            if Flaky.attempts == 1:
                raise RuntimeError("boum")
            await publish({"ok": True})
            await asyncio.Event().wait()

    hub = Hub([Flaky()])
    await hub.start()
    await asyncio.sleep(0.01)
    assert "unavailable" in hub.snapshot("power")  # signalé aux clients pendant la panne
    await asyncio.sleep(0.15)
    assert hub.snapshot("power") == {"ok": True}  # puis rétabli tout seul
    await hub.stop()


async def test_stop_cancels_everything():
    providers = [ScriptedProvider("host", lazy=False), ScriptedProvider("system")]
    hub = Hub(providers)
    await hub.start()
    hub.subscribe(ClientConnection("c"), ["system"])
    await asyncio.sleep(0.02)
    await hub.stop()
    assert [p.stops for p in providers] == [1, 1]
    assert hub.running_topics() == []
