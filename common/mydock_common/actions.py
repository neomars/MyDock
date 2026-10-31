"""Registre des actions de l'agent : une liste blanche d'actions nommées.

Le dock ne peut jamais envoyer de commande : seulement le nom d'une action enregistrée ici
et des paramètres validés par son modèle pydantic (champs inconnus refusés). Les actions
sensibles exigent une confirmation à usage unique, liée à la connexion, à l'action et aux
paramètres, valable quelques secondes : un message rejoué ou détourné ne suffit pas.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from mydock_common.protocol import ACTION_NAME_RE, Act, ErrorCode, ack_error, ack_ok

log = logging.getLogger(__name__)


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionError(Exception):
    """Échec prévu d'une action, avec un message affichable à l'écran."""

    def __init__(self, code: ErrorCode | str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ActionContext:
    conn_id: int
    peer: str


Handler = Callable[[ActionContext, Any], Awaitable[Any]]
#: Libellé de confirmation : un texte fixe, ou une fonction des paramètres validés
#: (« Terminer firefox (PID 1234) » : la fenêtre de confirmation dit ce qui va arriver).
Label = str | Callable[[Any], str]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    handler: Handler
    params: type[BaseModel]
    sensitive: bool
    label: Label
    timeout: float

    def confirmation_label(self, params: BaseModel) -> str:
        try:
            text = self.label(params) if callable(self.label) else self.label
        except Exception:
            log.exception("Libellé de confirmation illisible pour %s", self.name)
            text = ""
        return text or self.name  # jamais de fenêtre sans texte


class ConfirmStore:
    """Jetons de confirmation à usage unique."""

    def __init__(
        self,
        *,
        ttl: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        max_pending: int = 64,
    ):
        self.ttl = ttl
        self._clock = clock
        self._max_pending = max_pending
        self._pending: dict[str, tuple[float, int, str, str]] = {}

    @staticmethod
    def _fingerprint(params: Any) -> str:
        return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)

    def _purge(self) -> None:
        now = self._clock()
        for token in [t for t, entry in self._pending.items() if entry[0] <= now]:
            del self._pending[token]

    def issue(self, conn_id: int, action: str, params: Any) -> str:
        self._purge()
        while len(self._pending) >= self._max_pending:
            del self._pending[next(iter(self._pending))]
        token = secrets.token_urlsafe(16)
        self._pending[token] = (
            self._clock() + self.ttl,
            conn_id,
            action,
            self._fingerprint(params),
        )
        return token

    def redeem(self, conn_id: int, action: str, params: Any, token: str) -> bool:
        """Consomme le jeton. Vrai seulement s'il correspond exactement et n'a pas expiré."""
        self._purge()
        entry = self._pending.pop(token, None)
        if entry is None:
            return False
        _expires, issued_to, issued_action, fingerprint = entry
        return (
            issued_to == conn_id
            and issued_action == action
            and fingerprint == self._fingerprint(params)
        )


class ActionRegistry:
    def __init__(self, *, confirm_ttl: float = 10.0, clock: Callable[[], float] = time.monotonic):
        self._specs: dict[str, ActionSpec] = {}
        self.confirmations = ConfirmStore(ttl=confirm_ttl, clock=clock)

    def register(
        self,
        name: str,
        handler: Handler,
        *,
        params: type[BaseModel] | None = None,
        sensitive: bool = False,
        label: Label = "",
        timeout: float = 5.0,
    ) -> None:
        if not ACTION_NAME_RE.fullmatch(name):
            raise ValueError(f"Nom d'action invalide : {name!r}")
        if name in self._specs:
            raise ValueError(f"Action déjà enregistrée : {name!r}")
        if sensitive and not label:
            raise ValueError(f"L'action sensible {name!r} doit avoir un libellé de confirmation")
        self._specs[name] = ActionSpec(name, handler, params or NoParams, sensitive, label, timeout)

    @property
    def names(self) -> list[str]:
        return sorted(self._specs)

    def is_sensitive(self, name: str) -> bool:
        spec = self._specs.get(name)
        return bool(spec and spec.sensitive)

    async def invoke(self, act: Act, ctx: ActionContext) -> dict[str, Any]:
        """Exécute l'action et retourne toujours un message « ack » (jamais d'exception)."""
        spec = self._specs.get(act.name)
        if spec is None:
            return ack_error(act.id, ErrorCode.UNKNOWN_ACTION, "Action inconnue")
        try:
            params = spec.params.model_validate(act.params)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"]) or "paramètres"
            return ack_error(act.id, ErrorCode.BAD_PARAMS, f"Paramètre invalide : {where}")

        if spec.sensitive:
            canonical = params.model_dump(mode="json")
            if act.confirm is None:
                token = self.confirmations.issue(ctx.conn_id, spec.name, canonical)
                return ack_error(
                    act.id,
                    ErrorCode.CONFIRM_REQUIRED,
                    "Confirmation requise",
                    extra={
                        "confirm": {
                            "token": token,
                            "ttl": self.confirmations.ttl,
                            "label": spec.confirmation_label(params),
                        }
                    },
                )
            if not self.confirmations.redeem(ctx.conn_id, spec.name, canonical, act.confirm):
                return ack_error(
                    act.id, ErrorCode.CONFIRM_INVALID, "Confirmation invalide ou expirée"
                )

        started = time.perf_counter()

        def elapsed_ms() -> float:
            return (time.perf_counter() - started) * 1000

        try:
            async with asyncio.timeout(spec.timeout):
                result = await spec.handler(ctx, params)
        except ActionError as exc:
            return ack_error(act.id, exc.code, exc.message, ms=elapsed_ms())
        except TimeoutError:
            return ack_error(
                act.id, ErrorCode.TIMEOUT, "L'action a pris trop de temps", ms=elapsed_ms()
            )
        except Exception:
            log.exception("Échec inattendu de l'action %s", spec.name)
            return ack_error(act.id, ErrorCode.FAILED, "L'action a échoué", ms=elapsed_ms())
        return ack_ok(act.id, result, ms=elapsed_ms())
