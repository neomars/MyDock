"""File d'envoi d'un client WebSocket.

Deux sortes de messages :
  - les messages de contrôle (acquittements, erreurs, pong) partent tous, dans l'ordre ;
  - les instantanés d'état : seul le dernier de chaque thème compte. Un client lent ne fait
    donc jamais grossir la mémoire, et il ne retarde ni les autres clients ni l'agent.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable


class Outbox:
    def __init__(self, *, max_control: int = 512):
        self._control: deque[str] = deque()
        self._snapshots: dict[str, str] = {}
        self._wake = asyncio.Event()
        self._closed = False
        self._max_control = max_control
        self.overflowed = False

    def push_control(self, text: str) -> None:
        if self._closed:
            return
        if len(self._control) >= self._max_control:
            self.overflowed = True  # client qui ne lit plus : l'appelant le déconnecte
            self._wake.set()
            return
        self._control.append(text)
        self._wake.set()

    def push_snapshot(self, topic: str, text: str) -> None:
        if self._closed:
            return
        self._snapshots[topic] = text
        self._wake.set()

    def close(self) -> None:
        self._closed = True
        self._wake.set()

    async def run(
        self, send: Callable[[str], Awaitable[None]], *, send_timeout: float = 10.0
    ) -> None:
        """Envoie les messages jusqu'à la fermeture. Les erreurs de `send` remontent."""
        while True:
            await self._wake.wait()
            self._wake.clear()
            if self.overflowed:
                raise ConnectionError("File d'envoi saturée : le client ne lit plus")
            while self._control:
                await asyncio.wait_for(send(self._control.popleft()), send_timeout)
            if self._snapshots:
                pending, self._snapshots = self._snapshots, {}
                for text in pending.values():
                    await asyncio.wait_for(send(text), send_timeout)
            if self._closed and not self._control and not self._snapshots:
                return
