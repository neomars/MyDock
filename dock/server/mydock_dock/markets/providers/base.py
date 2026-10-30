"""Ce que tous les fournisseurs de cotations partagent : l'interface et la lecture des réponses."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any, Protocol

from mydock_dock.markets.errors import (
    BadResponse,
    Blocked,
    NotFound,
    ProviderError,
    RateLimited,
    Unavailable,
)
from mydock_dock.markets.http import HttpResponse
from mydock_dock.markets.model import HistoryPoint, Instrument, Quote


class QuoteProvider(Protocol):
    id: str  # « yahoo », « binance »… (aussi la clé d'état dans l'écran)
    label: str  # « Yahoo Finance » : ce que l'écran affiche

    def supports(self, instrument: Instrument) -> bool:
        """Ce fournisseur sait-il coter cette valeur ?"""

    async def quotes(
        self, instruments: Sequence[Instrument], currency: str
    ) -> dict[str, Quote | ProviderError]:
        """Une cotation (ou la raison de son absence) par valeur, indexée par `Instrument.id`.

        Lève une `ProviderError` si le fournisseur tout entier est en panne (réseau, quota, refus).
        """

    async def history(
        self, instrument: Instrument, range_: str, currency: str
    ) -> list[HistoryPoint]:
        """Le cours sur la période (`1d`, `5d`, `1mo`, `1y`), du plus ancien au plus récent."""


def number(value: Any) -> float | None:
    """Un nombre fini ; les textes numériques de Binance sont acceptés, tout le reste est ignoré."""
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def raise_for_status(response: HttpResponse, label: str) -> None:
    """Traduit un code HTTP d'échec en l'erreur qui explique à l'écran ce qui se passe."""
    status = response.status
    if 200 <= status < 300:
        return
    if status in (418, 429):
        raise RateLimited(
            f"{label} limite le nombre de requêtes : nouvel essai plus tard.",
            retry_after=response.retry_after,
        )
    if status in (401, 403):
        raise Blocked(f"{label} refuse l'accès (HTTP {status}).")
    if status == 451:
        raise Blocked(f"{label} n'est pas accessible depuis ce pays (HTTP 451).")
    if status == 404:
        raise NotFound(f"{label} ne connaît pas cette valeur.")
    if status >= 500:
        raise Unavailable(
            f"{label} est indisponible (HTTP {status}).", retry_after=response.retry_after
        )
    raise BadResponse(f"{label} a répondu HTTP {status}.")


def parse_json(response: HttpResponse, label: str) -> Any:
    try:
        return json.loads(response.body)
    except (ValueError, UnicodeDecodeError):
        raise BadResponse(f"Réponse illisible de {label}.") from None


def first_provider_error(results: dict[str, Quote | ProviderError]) -> ProviderError | None:
    """L'erreur de panne du fournisseur si **toutes** les réponses en sont une (sinon None)."""
    if not results:
        return None
    errors = [r for r in results.values() if isinstance(r, ProviderError)]
    if len(errors) == len(results) and all(e.provider_down for e in errors):
        return errors[0]
    return None
