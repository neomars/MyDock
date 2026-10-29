"""Hub de l'agent : thèmes d'état, abonnements et diffusion sur changement uniquement.

Chaque thème (host, system, music…) a un « fournisseur » qui publie l'état complet. Le hub
compare avec le dernier état publié : si rien n'a changé, rien n'est envoyé. Les fournisseurs
« paresseux » ne tournent que tant qu'au moins un client est abonné à leur thème, ce qui garde
l'agent à presque 0 % de CPU au repos.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from mydock_common.outbox import Outbox
from mydock_common.protocol import AGENT_TOPICS, ErrorCode, ProtocolError

log = logging.getLogger(__name__)

Publish = Callable[[dict[str, Any]], Awaitable[bool]]
RESTART_DELAY = 5.0


class Provider:
    """Source de données d'un thème. Les sous-classes implémentent `run`."""

    topic: str = ""
    #: vrai : démarré au premier abonné, arrêté au dernier ; faux : toujours actif.
    lazy: bool = True

    async def run(self, publish: Publish) -> None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class TopicState:
    rev: int
    payload: str  # JSON canonique des données
    text: str  # message « snap » prêt à envoyer


class ClientConnection:
    _ids = itertools.count(1)

    def __init__(self, peer: str):
        self.id = next(self._ids)
        self.peer = peer
        self.subscriptions: set[str] = set()
        self.outbox = Outbox()


class Hub:
    def __init__(self, providers: Iterable[Provider] = ()):
        self._providers: dict[str, Provider] = {}
        self._state: dict[str, TopicState] = {}
        self._subs: dict[str, set[ClientConnection]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        for provider in providers:
            self.add_provider(provider)

    # --- fournisseurs -------------------------------------------------------------------

    def add_provider(self, provider: Provider) -> None:
        if provider.topic not in AGENT_TOPICS:
            raise ValueError(f"Thème inconnu : {provider.topic!r}")
        if provider.topic in self._providers:
            raise ValueError(f"Thème déjà fourni : {provider.topic!r}")
        self._providers[provider.topic] = provider

    @property
    def topics(self) -> list[str]:
        return [t for t in AGENT_TOPICS if t in self._providers]

    async def start(self) -> None:
        for provider in self._providers.values():
            if not provider.lazy:
                self._start(provider)

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _start(self, provider: Provider) -> None:
        task = self._tasks.get(provider.topic)
        if task is None or task.done():
            self._tasks[provider.topic] = asyncio.create_task(
                self._supervise(provider), name=f"provider-{provider.topic}"
            )

    async def _supervise(self, provider: Provider) -> None:
        """Relance un fournisseur qui plante, sans jamais faire tomber l'agent."""
        topic = provider.topic

        async def publish(data: dict[str, Any]) -> bool:
            return await self.publish(topic, data)

        while True:
            try:
                await provider.run(publish)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(
                    "Le fournisseur « %s » a planté ; nouvel essai dans %ss", topic, RESTART_DELAY
                )
                await self.publish(
                    topic, {"unavailable": "Erreur interne du collecteur, nouvel essai en cours."}
                )
                await asyncio.sleep(RESTART_DELAY)

    # --- état ---------------------------------------------------------------------------

    async def publish(self, topic: str, data: dict[str, Any]) -> bool:
        """Enregistre le nouvel état et le diffuse s'il a changé. Retourne vrai si diffusé."""
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        previous = self._state.get(topic)
        if previous is not None and previous.payload == payload:
            return False
        rev = previous.rev + 1 if previous else 1
        text = f'{{"t":"snap","topic":"{topic}","rev":{rev},"data":{payload}}}'
        self._state[topic] = TopicState(rev, payload, text)
        for conn in tuple(self._subs.get(topic, ())):
            conn.outbox.push_snapshot(topic, text)
        return True

    def snapshot(self, topic: str) -> dict[str, Any] | None:
        state = self._state.get(topic)
        return json.loads(state.payload) if state else None

    # --- abonnements --------------------------------------------------------------------

    def subscribe(self, conn: ClientConnection, topics: Iterable[str]) -> None:
        topics = list(topics)
        unknown = [t for t in topics if t not in AGENT_TOPICS]
        if unknown:
            raise ProtocolError(ErrorCode.UNKNOWN_TOPIC, f"Thème inconnu : {unknown[0]}")
        for topic in topics:
            self._subs.setdefault(topic, set()).add(conn)
            conn.subscriptions.add(topic)
            provider = self._providers.get(topic)
            if provider is None:
                # Thème prévu par le protocole mais pas encore fourni par cette version.
                text = (
                    f'{{"t":"snap","topic":"{topic}","rev":0,"data":'
                    '{"unavailable":"Pas encore disponible dans cette version de l\'agent."}}'
                )
                conn.outbox.push_snapshot(topic, text)
                continue
            state = self._state.get(topic)
            if state is not None:
                conn.outbox.push_snapshot(topic, state.text)
            if provider.lazy:
                self._start(provider)

    def unsubscribe(self, conn: ClientConnection, topics: Iterable[str]) -> None:
        for topic in topics:
            conn.subscriptions.discard(topic)
            subscribers = self._subs.get(topic)
            if subscribers is None:
                continue
            subscribers.discard(conn)
            if not subscribers:
                del self._subs[topic]
                self._stop_if_idle(topic)

    def detach(self, conn: ClientConnection) -> None:
        self.unsubscribe(conn, list(conn.subscriptions))

    def _stop_if_idle(self, topic: str) -> None:
        provider = self._providers.get(topic)
        if provider is None or not provider.lazy:
            return
        task = self._tasks.pop(topic, None)
        if task is not None:
            task.cancel()
        self._state.pop(topic, None)  # pas d'état périmé au prochain abonné

    def subscriber_count(self, topic: str) -> int:
        return len(self._subs.get(topic, ()))

    def running_topics(self) -> list[str]:
        return sorted(t for t, task in self._tasks.items() if not task.done())
