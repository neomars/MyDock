"""Le service des cotations : ce que le dock sait de la bourse, et quand il le redemande.

Principes :
  - **Rien ne tourne quand personne ne regarde.** Les cotations ne sont redemandées que pendant
    qu'un navigateur est abonné au thème « markets » (la page Bourse est ouverte) ; sinon le dock
    ne fait aucune requête. À l'ouverture de la page, la dernière cotation connue s'affiche tout de
    suite (gardée dans un fichier, elle survit à un redémarrage) et la mise à jour part aussitôt.
  - **Une valeur, un rythme** : 15 s pour une crypto, 30 s pour une valeur dont la place cote, 5 min
    place fermée. Les fournisseurs ont des limites de débit (`limits.py`), des pauses après un
    refus, et se relaient : chaque valeur est proposée au premier fournisseur qui la connaît, puis
    au suivant s'il est en panne ou ne la connaît pas.
  - **Rien d'inventé** : une cotation dont le rafraîchissement échoue reste affichée avec son âge
    et la raison de l'échec ; un champ que le fournisseur ne donne pas reste vide.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import tempfile
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from mydock_dock.markets.errors import NotFound, ProviderError, Unsupported
from mydock_dock.markets.limits import Backoff
from mydock_dock.markets.model import (
    RANGES,
    HistoryPoint,
    Instrument,
    Quote,
    crypto_name,
    normalize_symbol,
    parse_instrument,
    parse_watchlist,
)
from mydock_dock.markets.providers.base import QuoteProvider
from mydock_dock.store import StateStore
from mydock_dock.validation import ValidationFailure

log = logging.getLogger(__name__)

CACHE_VERSION = 1
CACHE_MAX_AGE = 7 * 86_400.0  # une cotation de plus d'une semaine ne vaut plus d'être montrée
MAX_HISTORY_CACHE = 64


@dataclass(frozen=True)
class Timing:
    """Les rythmes, en secondes (réglables pour les tests)."""

    crypto_ttl: float = 15.0
    stock_ttl: float = 30.0
    off_hours_ttl: float = 60.0  # avant et après la séance
    closed_ttl: float = 300.0
    error_retry: float = 20.0
    request_timeout: float = 30.0
    min_sleep: float = 0.5  # jamais de boucle serrée, même si tout est dû
    min_manual_refresh: float = 3.0
    cache_write_every: float = 60.0
    spark_keep: float = 900.0  # un mini-graphique perdu en route n'est gardé que 15 min
    history_ttl: Mapping[str, float] = field(
        default_factory=lambda: {"1d": 60.0, "5d": 300.0, "1mo": 1800.0, "1y": 1800.0}
    )


@dataclass
class Entry:
    quote: Quote | None = None
    fetched: float | None = None  # horloge murale : quand le dock l'a obtenue
    error: str | None = None
    due: float = 0.0  # horloge monotone : quand la redemander


@dataclass
class ProviderStatus:
    backoff: Backoff
    error: str | None = None
    retry_at: float | None = None  # horloge murale
    last_ok: float | None = None


def quote_to_dict(quote: Quote) -> dict[str, Any]:
    return {
        "price": quote.price,
        "currency": quote.currency,
        "change": quote.change,
        "change_pct": quote.change_pct,
        "high": quote.high,
        "low": quote.low,
        "state": quote.state,
        "traded_at": quote.traded_at,
        "spark": list(quote.spark),
        "source": quote.source,
        "name": quote.name,
    }


def quote_from_dict(raw: Mapping[str, Any]) -> Quote | None:
    """Relit une cotation gardée dans le fichier ; tout ce qui n'est pas du bon type est refusé."""
    try:
        price = float(raw["price"])
        spark = tuple(float(v) for v in raw.get("spark") or ())
        if not math.isfinite(price):
            return None

        def opt(name: str) -> float | None:
            value = raw.get(name)
            return float(value) if isinstance(value, int | float) and math.isfinite(value) else None

        def text(name: str) -> str | None:
            value = raw.get(name)
            return value if isinstance(value, str) and len(value) <= 120 else None

        return Quote(
            price=price,
            currency=text("currency"),
            source=text("source") or "?",
            name=text("name"),
            change=opt("change"),
            change_pct=opt("change_pct"),
            high=opt("high"),
            low=opt("low"),
            state=text("state"),
            traded_at=opt("traded_at"),
            spark=spark,
        )
    except (KeyError, TypeError, ValueError):
        return None


class MarketsService:
    def __init__(
        self,
        *,
        store: StateStore,
        providers: Sequence[QuoteProvider],
        cache_path: Path | None = None,
        on_change: Callable[[], None] = lambda: None,
        timing: Timing | None = None,
        demo: bool = False,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._store = store
        self._providers = list(providers)
        self._cache_path = cache_path
        self._on_change = on_change
        self._t = timing or Timing()
        self._demo = demo
        self._clock = clock
        self._mono = monotonic
        self._entries: dict[str, Entry] = {}
        self._status = {p.id: ProviderStatus(Backoff(monotonic)) for p in self._providers}
        self._watching = False
        self._wake = asyncio.Event()
        self._refresh_lock = asyncio.Lock()
        self._last_manual = -1e9
        self._last_text = ""
        self._dirty = False
        self._last_write = -1e9
        self._history: OrderedDict[tuple[str, str, str], tuple[float, dict[str, Any]]] = (
            OrderedDict()
        )
        self._history_inflight: dict[tuple[str, str, str], asyncio.Task[dict[str, Any]]] = {}
        self._load_cache()

    # --- la liste suivie -------------------------------------------------------------------

    @property
    def currency(self) -> str:
        return str(self._store.markets["currency"])

    @property
    def watchlist(self) -> list[Instrument]:
        try:
            return parse_watchlist(self._store.markets["watchlist"])
        except ValidationFailure:  # un fichier d'état abîmé à la main ne doit pas arrêter le dock
            log.warning("Liste de suivi illisible : ignorée")
            return []

    def watchlist_changed(self) -> None:
        """Appelé après un changement de liste ou de devise : on redemande ce qui manque."""
        wanted = {i.id for i in self.watchlist}
        self._entries = {k: v for k, v in self._entries.items() if k in wanted}
        for entry in self._entries.values():
            entry.due = 0.0
        self._dirty = True
        self._publish()
        self._wake.set()

    # --- cache sur disque ------------------------------------------------------------------

    def _load_cache(self) -> None:
        if self._cache_path is None:
            return
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != CACHE_VERSION:
                return
            if raw.get("currency") != self.currency:
                return  # les prix d'une autre devise ne sont pas ceux-là
            now = self._clock()
            wanted = {i.id for i in self.watchlist}
            for key, item in (raw.get("items") or {}).items():
                fetched = item.get("fetched") if isinstance(item, dict) else None
                quote = quote_from_dict(item.get("quote") or {}) if isinstance(item, dict) else None
                if (
                    key in wanted
                    and quote
                    and isinstance(fetched, int | float)
                    and 0 <= now - fetched < CACHE_MAX_AGE
                ):
                    self._entries[key] = Entry(quote, float(fetched))
        except (OSError, ValueError, AttributeError):
            return

    def _write_cache(self) -> None:
        if self._cache_path is None:
            return
        items = {
            key: {"fetched": e.fetched, "quote": quote_to_dict(e.quote)}
            for key, e in self._entries.items()
            if e.quote is not None and e.fetched is not None
        }
        data = {"version": CACHE_VERSION, "currency": self.currency, "items": items}
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".markets.", dir=self._cache_path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False)
                os.replace(tmp, self._cache_path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
            self._last_write = self._mono()
            self._dirty = False
        except OSError as exc:
            log.warning("Impossible d'écrire %s (%s)", self._cache_path, exc)

    def save(self) -> None:
        """Écrit le cache maintenant (à l'arrêt du dock)."""
        if self._dirty:
            self._write_cache()

    # --- ce que voit l'écran ---------------------------------------------------------------

    def payload(self) -> dict[str, Any]:
        watchlist = self.watchlist
        entries = [self._entries.get(i.id) for i in watchlist]
        fetched = [e.fetched for e in entries if e and e.fetched is not None]
        return {
            "currency": self.currency,
            "demo": self._demo,
            "items": [self._item(i) for i in watchlist],
            "fetched_at": max(fetched) if fetched else None,
            "providers": [self._provider_state(p) for p in self._providers],
        }

    def _item(self, instrument: Instrument) -> dict[str, Any]:
        entry = self._entries.get(instrument.id) or Entry()
        quote = entry.quote
        fallback_name = crypto_name(instrument.symbol) if instrument.kind == "crypto" else None
        data = quote_to_dict(quote) if quote else {}
        return {
            "id": instrument.id,
            "kind": instrument.kind,
            "symbol": instrument.symbol,
            "name": (quote.name if quote and quote.name else fallback_name),
            "price": data.get("price"),
            "currency": data.get("currency"),
            "change": data.get("change"),
            "change_pct": data.get("change_pct"),
            "high": data.get("high"),
            "low": data.get("low"),
            "state": data.get("state"),
            "traded_at": data.get("traded_at"),
            "spark": data.get("spark", []),
            "source": data.get("source"),
            "fetched": entry.fetched,
            "error": entry.error,
        }

    def _provider_state(self, provider: QuoteProvider) -> dict[str, Any]:
        status = self._status[provider.id]
        if status.error is None:
            state = "ok" if status.last_ok is not None else "idle"
        else:
            state = "paused" if status.backoff.paused else "error"
        return {
            "id": provider.id,
            "label": provider.label,
            "state": state,
            "message": status.error,
            "retry_at": status.retry_at if state == "paused" else None,
        }

    def _publish(self) -> None:
        text = json.dumps(self.payload(), sort_keys=True, separators=(",", ":"))
        if text != self._last_text:
            self._last_text = text
            self._on_change()

    # --- abonnement : qui regarde -----------------------------------------------------------

    def set_watching(self, watching: bool) -> None:
        if watching and not self._watching:
            self._watching = True
            self._wake.set()
        elif not watching:
            self._watching = False

    # --- la boucle -------------------------------------------------------------------------

    async def run(self) -> None:
        while True:
            self._wake.clear()  # avant la passe : un réveil survenu pendant elle n'est pas perdu
            try:
                delay = await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Mise à jour des cotations interrompue ; nouvel essai dans 30 s")
                delay = 30.0
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), delay)

    async def _tick(self) -> float | None:
        """Une passe : redemande ce qui est dû ; retourne le délai avant la suivante (None : attendre)."""
        if not self._watching:
            self.save()
            return None
        watchlist = self.watchlist
        now = self._mono()
        due = [i for i in watchlist if self._entries.get(i.id, Entry()).due <= now]
        if due:
            async with self._refresh_lock:
                await self._refresh(due)
        now = self._mono()
        upcoming = [self._entries[i.id].due for i in watchlist if i.id in self._entries]
        if not upcoming:
            return None if not watchlist else 5.0
        return min(60.0, max(self._t.min_sleep, min(upcoming) - now))

    # --- une mise à jour -------------------------------------------------------------------

    def _ttl(self, instrument: Instrument, quote: Quote) -> float:
        if instrument.kind == "crypto":
            return self._t.crypto_ttl
        if quote.state == "closed":
            return self._t.closed_ttl
        if quote.state in ("pre", "post"):
            return self._t.off_hours_ttl
        return self._t.stock_ttl

    def _usable(self, provider: QuoteProvider) -> bool:
        return not self._status[provider.id].backoff.paused

    def _provider_failed(self, provider: QuoteProvider, error: ProviderError) -> None:
        status = self._status[provider.id]
        pause = status.backoff.failed(error.retry_after)
        status.error = error.message
        status.retry_at = self._clock() + pause
        log.info("%s : %s (pause de %.0f s)", provider.label, error.message, pause)

    def _provider_worked(self, provider: QuoteProvider) -> None:
        status = self._status[provider.id]
        status.backoff.succeeded()
        status.error = None
        status.retry_at = None
        status.last_ok = self._clock()

    async def _refresh(self, due: Sequence[Instrument]) -> None:
        """Propose chaque valeur due aux fournisseurs, dans l'ordre, jusqu'à ce que l'un la cote."""
        currency = self.currency
        pending = list(due)
        reasons: dict[str, str] = {}
        for provider in self._providers:
            mine = [i for i in pending if provider.supports(i)]
            if not mine or not self._usable(provider):
                continue
            try:
                async with asyncio.timeout(self._t.request_timeout):
                    results = await provider.quotes(mine, currency)
            except ProviderError as exc:
                self._provider_failed(provider, exc)
                for instrument in mine:
                    reasons[instrument.id] = f"{provider.label} : {exc.message}"
                continue
            except TimeoutError:
                down = ProviderError(f"{provider.label} ne répond pas.")
                self._provider_failed(provider, down)
                for instrument in mine:
                    reasons[instrument.id] = down.message
                continue
            except Exception:
                log.exception("%s a planté", provider.label)
                self._provider_failed(
                    provider, ProviderError(f"{provider.label} : erreur interne.")
                )
                continue
            down = None
            answered = False
            for instrument in mine:
                result = results.get(instrument.id)
                if isinstance(result, Quote):
                    self._store_quote(instrument, result)
                    pending.remove(instrument)
                    answered = True
                elif isinstance(result, ProviderError):
                    reasons[instrument.id] = f"{provider.label} : {result.message}"
                    if result.provider_down:
                        down = down or result
                    else:
                        answered = True  # il a répondu : seule cette valeur lui est inconnue
            if down is not None:
                self._provider_failed(provider, down)  # une seule fois par passe, pas par valeur
            elif answered:
                self._provider_worked(provider)
        for instrument in pending:
            self._store_error(instrument, reasons.get(instrument.id))
        self._dirty = True
        self._publish()
        if self._mono() - self._last_write >= self._t.cache_write_every:
            self._write_cache()

    def _store_quote(self, instrument: Instrument, quote: Quote) -> None:
        entry = self._entries.setdefault(instrument.id, Entry())
        previous = entry.quote
        if previous is not None:
            recent = (
                entry.fetched is not None and self._clock() - entry.fetched < self._t.spark_keep
            )
            quote = replace(
                quote,
                name=quote.name or previous.name,
                spark=quote.spark or (previous.spark if recent else ()),
            )
        entry.quote = quote
        entry.fetched = self._clock()
        entry.error = None
        entry.due = self._mono() + self._ttl(instrument, quote)

    def _store_error(self, instrument: Instrument, reason: str | None) -> None:
        entry = self._entries.setdefault(instrument.id, Entry())
        entry.error = reason or "Aucun fournisseur de cotations disponible."
        waits = [
            self._status[p.id].backoff.remaining
            for p in self._providers
            if p.supports(instrument) and not self._usable(p)
        ]
        entry.due = self._mono() + max(self._t.error_retry, min(waits, default=0.0))

    # --- actions de l'écran ----------------------------------------------------------------

    async def refresh(self) -> dict[str, Any]:
        """Redemande tout maintenant (bouton « Actualiser »), au plus une fois toutes les 3 s."""
        now = self._mono()
        if now - self._last_manual < self._t.min_manual_refresh:
            return {"refreshed": False}
        self._last_manual = now
        async with self._refresh_lock:
            await self._refresh(self.watchlist)
        return {"refreshed": True}

    async def check(self, kind: object, symbol: object) -> dict[str, Any]:
        """Vérifie qu'une valeur existe chez un fournisseur avant de l'ajouter à la liste."""
        instrument = parse_instrument({"kind": kind, "symbol": symbol})
        currency = self.currency
        reasons: list[str] = []
        unknown_at: list[str] = []
        for provider in self._providers:
            if not provider.supports(instrument) or not self._usable(provider):
                continue
            try:
                async with asyncio.timeout(self._t.request_timeout):
                    results = await provider.quotes([instrument], currency)
            except ProviderError as exc:
                self._provider_failed(provider, exc)
                reasons.append(f"{provider.label} : {exc.message}")
                continue
            except TimeoutError:
                reasons.append(f"{provider.label} ne répond pas.")
                continue
            result = results.get(instrument.id)
            if isinstance(result, Quote):
                return {
                    "id": instrument.id,
                    "kind": instrument.kind,
                    "symbol": instrument.symbol,
                    "name": result.name
                    or (crypto_name(instrument.symbol) if instrument.kind == "crypto" else None),
                    "price": result.price,
                    "currency": result.currency,
                    "source": result.source,
                }
            if isinstance(result, NotFound):
                unknown_at.append(provider.label)
            elif isinstance(result, ProviderError):
                if result.provider_down:
                    self._provider_failed(provider, result)
                reasons.append(f"{provider.label} : {result.message}")
        if unknown_at and not reasons:
            raise NotFound(
                f"« {instrument.symbol} » est inconnu de {', '.join(unknown_at)}. Vérifie le symbole."
            )
        raise ProviderError(
            "Impossible de vérifier ce symbole : "
            + (" ; ".join(reasons) or "aucun fournisseur disponible.")
        )

    async def history(self, ident: object, range_: object) -> dict[str, Any]:
        """Le cours d'une valeur de la liste sur une période, pour le graphique de détail."""
        instrument = self._instrument_from_id(ident)
        if range_ not in RANGES:
            raise ValidationFailure("Période inconnue (1d, 5d, 1mo ou 1y).")
        key = (instrument.id, str(range_), self.currency)
        cached = self._history.get(key)
        ttl = self._t.history_ttl[str(range_)]
        if cached and self._mono() - cached[0] < ttl:
            self._history.move_to_end(key)
            return cached[1]
        task = self._history_inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch_history(instrument, str(range_)))
            self._history_inflight[key] = task
            task.add_done_callback(lambda _t: self._history_inflight.pop(key, None))
        result = await asyncio.shield(task)
        self._history[key] = (self._mono(), result)
        while len(self._history) > MAX_HISTORY_CACHE:
            self._history.popitem(last=False)
        return result

    def _instrument_from_id(self, ident: object) -> Instrument:
        if not isinstance(ident, str) or ":" not in ident:
            raise ValidationFailure("Valeur inconnue.")
        kind, _, symbol = ident.partition(":")
        instrument = Instrument(kind, normalize_symbol(kind, symbol))
        if instrument.id not in {i.id for i in self.watchlist}:
            raise ValidationFailure("Cette valeur n'est pas dans la liste suivie.")
        return instrument

    async def _fetch_history(self, instrument: Instrument, range_: str) -> dict[str, Any]:
        currency = self.currency
        reasons: list[str] = []
        for provider in self._providers:
            if not provider.supports(instrument) or not self._usable(provider):
                continue
            try:
                async with asyncio.timeout(self._t.request_timeout):
                    points: list[HistoryPoint] = await provider.history(
                        instrument, range_, currency
                    )
            except Unsupported:
                continue
            except NotFound as exc:
                reasons.append(f"{provider.label} : {exc.message}")
                continue
            except ProviderError as exc:
                self._provider_failed(provider, exc)
                reasons.append(f"{provider.label} : {exc.message}")
                continue
            except TimeoutError:
                reasons.append(f"{provider.label} ne répond pas.")
                continue
            if len(points) >= 2:
                quote = (self._entries.get(instrument.id) or Entry()).quote
                return {
                    "id": instrument.id,
                    "range": range_,
                    "currency": quote.currency if quote and quote.currency else currency,
                    "source": provider.id,
                    "points": [p.to_list() for p in points],
                    "fetched": self._clock(),
                }
            reasons.append(f"{provider.label} : pas assez de points.")
        raise ProviderError(
            "Historique indisponible : " + (" ; ".join(reasons) or "aucun fournisseur ne l'offre.")
        )
