"""L'association d'un PC au dock : un code affiché sur l'écran du Pi, tapé sur le PC.

Le code prouve que la personne qui installe l'agent est **devant le dock** : il n'apparaît que sur
l'écran du Pi (jamais sur le portail du réseau local), il est valable dix minutes, il n'admet que
quelques erreurs avant d'être annulé, et un même poste ne peut pas multiplier les essais. Un code,
c'est huit caractères d'un alphabet sans ambiguïté (ni 0/O ni 1/I/L) : environ 40 bits, contre cinq
essais au plus par code : une devinette réussit moins d'une fois sur cent milliards.

Ce module ne sait rien du réseau ni de la configuration : il tient l'état d'une session
d'association et décide si un code présenté est le bon. Le serveur du dock (app.py) fait le reste.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # 31 signes : ni 0, O, 1, I ni L
CODE_LENGTH = 8
SESSION_SECONDS = 600.0
MAX_WRONG = 5
THROTTLE_WINDOW = 60.0
THROTTLE_MAX = 12  # demandes par minute et par adresse, bonnes ou mauvaises
MAX_TRACKED_PEERS = 256


class PairingRefused(Exception):
    """Une demande d'association refusée, avec un message affichable (par le PC, en français)."""

    def __init__(
        self, code: str, message: str, *, status: int = 400, extra: dict[str, Any] | None = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra or {}  # ajouté à la réponse : de quoi aider le PC à corriger


def new_code(choose: Callable[[str], str] = secrets.choice) -> str:
    return "".join(choose(ALPHABET) for _ in range(CODE_LENGTH))


def format_code(code: str) -> str:
    """« K7QM2XPD » -> « K7QM-2XPD » : deux groupes de quatre se lisent et se tapent mieux."""
    return f"{code[:4]}-{code[4:]}"


def normalize_code(value: object) -> str | None:
    """Le code tel qu'on l'a tapé (casse, espaces et tirets libres) ; None s'il n'a pas la bonne forme."""
    if not isinstance(value, str) or len(value) > 32:
        return None
    cleaned = "".join(ch for ch in value.upper() if ch not in " -")
    if len(cleaned) != CODE_LENGTH or any(ch not in ALPHABET for ch in cleaned):
        return None
    return cleaned


@dataclass
class _Session:
    code: str
    expires_at: float  # horloge monotone
    expires_ms: int  # heure murale, pour l'écran
    wrong: int = 0


class Pairing:
    """Au plus une session ouverte à la fois ; en ouvrir une nouvelle annule la précédente."""

    def __init__(
        self,
        *,
        on_change: Callable[[], None] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        session_seconds: float = SESSION_SECONDS,
        max_wrong: int = MAX_WRONG,
        choose: Callable[[str], str] = secrets.choice,
    ):
        self._on_change = on_change
        self._clock = clock
        self._wall = wall
        self._seconds = session_seconds
        self._max_wrong = max_wrong
        self._choose = choose
        self._session: _Session | None = None
        self._hits: dict[str, deque[float]] = {}
        #: Comment la dernière session s'est terminée : « paired », « expired », « locked », « closed ».
        self.last: dict[str, Any] | None = None

    # --- la session ----------------------------------------------------------------------

    def start(self) -> None:
        """Ouvre une session avec un nouveau code (l'ancien cesse de valoir)."""
        now = self._clock()
        self._session = _Session(
            code=new_code(self._choose),
            expires_at=now + self._seconds,
            expires_ms=int((self._wall() + self._seconds) * 1000),
        )
        self.last = None
        self._on_change()

    def stop(self, reason: str = "closed") -> None:
        if self._session is None:
            return
        self._session = None
        self.last = {"result": reason, "at": int(self._wall() * 1000)}
        self._on_change()

    def tick(self) -> None:
        """À appeler régulièrement : constate l'expiration d'une session (et prévient l'écran)."""
        self.is_open()

    def is_open(self) -> bool:
        session = self._session
        if session is None:
            return False
        if self._clock() >= session.expires_at:
            self.stop("expired")
            return False
        return True

    def info(self) -> dict[str, Any]:
        """L'état pour l'écran du Pi (le code n'est jamais envoyé ailleurs)."""
        session = self._session if self.is_open() else None
        if session is None:
            return {"open": False, "last": self.last}
        return {
            "open": True,
            "code": format_code(session.code),
            "expires_at": session.expires_ms,
            "wrong_left": self._max_wrong - session.wrong,
            "last": None,
        }

    def finish(self, name: str, host: str) -> None:
        """Le PC est associé : la session se termine, l'écran le dit."""
        self._session = None
        self.last = {"result": "paired", "name": name, "host": host, "at": int(self._wall() * 1000)}
        self._on_change()

    # --- ce que le PC présente -----------------------------------------------------------

    def _throttle(self, peer: str) -> None:
        now = self._clock()
        hits = self._hits.setdefault(peer, deque())
        while hits and now - hits[0] > THROTTLE_WINDOW:
            hits.popleft()
        if len(hits) >= THROTTLE_MAX:
            raise PairingRefused(
                "too_many",
                "Trop de demandes depuis cette adresse : réessaie dans une minute.",
                status=429,
            )
        hits.append(now)
        if len(self._hits) > MAX_TRACKED_PEERS:  # la mémoire ne grandit pas sans fin
            for other in [
                p for p, q in self._hits.items() if not q or now - q[-1] > THROTTLE_WINDOW
            ]:
                del self._hits[other]

    def check(self, peer: str, presented: object) -> None:
        """Passe si le code est bon ; sinon lève `PairingRefused` (et compte l'essai)."""
        self._throttle(peer)
        if not self.is_open():
            raise PairingRefused(
                "not_open",
                "Aucune association n'est en cours : sur l'écran du dock, "
                "ouvre Réglages → « Associer un PC » pour afficher un code.",
                status=409,
            )
        session = self._session
        assert session is not None
        candidate = normalize_code(presented)
        # Comparaison à temps constant, y compris quand le code a une mauvaise forme.
        good = hmac.compare_digest((candidate or "-" * CODE_LENGTH).encode(), session.code.encode())
        if good and candidate is not None:
            return
        session.wrong += 1
        if session.wrong >= self._max_wrong:
            log.warning("Association annulée : trop de codes faux depuis %s", peer)
            self.stop("locked")
            raise PairingRefused(
                "locked",
                "Trop d'essais : l'association est annulée. Sur l'écran du dock, "
                "ouvre Réglages → « Associer un PC » pour obtenir un nouveau code.",
                status=403,
            )
        self._on_change()
        raise PairingRefused("bad_code", "Code incorrect.", status=403)
