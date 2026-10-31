import asyncio
import contextlib
import json
import time

import pytest

from mydock_dock.markets.errors import (
    NetworkError,
    NotFound,
    ProviderError,
    RateLimited,
    Unsupported,
)
from mydock_dock.markets.model import Candidate, HistoryPoint, Instrument, Quote
from mydock_dock.markets.service import MarketsService, Timing
from mydock_dock.store import StateStore
from mydock_dock.testing import eventually
from mydock_dock.validation import ValidationFailure

BTC = Instrument("crypto", "BTC")
ETH = Instrument("crypto", "ETH")
AAPL = Instrument("stock", "AAPL")
FCHI = Instrument("stock", "^FCHI")

FAST = Timing(
    crypto_ttl=0.05,
    stock_ttl=0.05,
    off_hours_ttl=0.1,
    closed_ttl=0.4,
    error_retry=0.05,
    request_timeout=2.0,
    min_sleep=0.01,
    min_manual_refresh=0.0,
    cache_write_every=0.0,
)


def quote(price: float = 100.0, **kwargs) -> Quote:
    return Quote(
        price=price,
        currency=kwargs.pop("currency", "EUR"),
        source=kwargs.pop("source", "fake"),
        **kwargs,
    )


class FakeProvider:
    """Un fournisseur dont on écrit les réponses : le service est testé sans aucun format réseau."""

    def __init__(self, id: str, label: str, kinds=("crypto", "stock")):
        self.id = id
        self.label = label
        self.kinds = kinds
        self.calls: list[tuple[list[str], str]] = []
        self.history_calls: list[tuple[str, str]] = []
        self.count = 0
        self.on_quotes = self.default_quotes
        self.on_history = self.default_history
        self.on_search = lambda kind, query: []
        self.searches: list[tuple[str, str]] = []
        self.delay = 0.0

    def supports(self, instrument: Instrument) -> bool:
        return instrument.kind in self.kinds

    def default_quotes(self, instruments, currency):
        self.count += 1
        return {
            i.id: quote(
                100.0 + self.count, currency=currency, source=self.id, name=f"{i.symbol} SA"
            )
            for i in instruments
        }

    def default_history(self, instrument, range_, currency):
        return [HistoryPoint(1000 + i * 60, 100.0 + i) for i in range(30)]

    async def quotes(self, instruments, currency):
        self.calls.append(([i.id for i in instruments], currency))
        if self.delay:
            await asyncio.sleep(self.delay)
        reply = self.on_quotes(list(instruments), currency)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def search(self, kind, query):
        self.searches.append((kind, query))
        reply = self.on_search(kind, query)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def history(self, instrument, range_, currency):
        self.history_calls.append((instrument.id, range_))
        reply = self.on_history(instrument, range_, currency)
        if isinstance(reply, Exception):
            raise reply
        return reply


@contextlib.asynccontextmanager
async def running(
    tmp_path, *providers, watchlist=None, timing=FAST, cache=False, watching=True, **kwargs
):
    store = StateStore(tmp_path / "state")
    if watchlist is not None:
        store.update_markets({"watchlist": [i.to_dict() for i in watchlist]})
    changes: list[int] = []
    service = MarketsService(
        store=store,
        providers=providers,
        cache_path=tmp_path / "state" / "markets-cache.json" if cache else None,
        on_change=lambda: changes.append(1),
        timing=timing,
        **kwargs,
    )
    task = asyncio.create_task(service.run())
    if watching:
        service.set_watching(True)
    try:
        yield service, store, changes
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def items(service):
    return {item["id"]: item for item in service.payload()["items"]}


# --- ce que voit l'écran avant tout ----------------------------------------------------------------


async def test_before_any_data_the_list_shows_loading_values_and_idle_providers(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    async with running(tmp_path, fake, watching=False) as (service, _store, _changes):
        payload = service.payload()
        assert payload["currency"] == "EUR"
        assert payload["demo"] is False
        assert payload["fetched_at"] is None
        assert [i["id"] for i in payload["items"]] == ["crypto:BTC", "crypto:ETH"]
        btc = payload["items"][0]
        assert (btc["price"], btc["error"], btc["fetched"]) == (None, None, None)
        assert (btc["name"], btc["spark"]) == ("Bitcoin", [])
        assert payload["providers"] == [
            {
                "id": "fake",
                "label": "Fournisseur",
                "state": "idle",
                "message": None,
                "retry_at": None,
            }
        ]


async def test_nothing_is_asked_while_nobody_looks(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    async with running(tmp_path, fake, watching=False) as (service, _store, _changes):
        await asyncio.sleep(0.3)
        assert fake.calls == []
        service.set_watching(True)
        await eventually(lambda: len(fake.calls) >= 1)
        assert fake.calls[0] == (["crypto:BTC", "crypto:ETH"], "EUR")  # une requête pour la liste


async def test_the_quotes_stop_being_asked_when_the_page_closes(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    async with running(tmp_path, fake) as (service, _store, _changes):
        await eventually(lambda: len(fake.calls) >= 3)
        service.set_watching(False)
        await asyncio.sleep(0.15)  # laisse finir la passe en cours
        seen = len(fake.calls)
        await asyncio.sleep(0.4)
        assert len(fake.calls) == seen


async def test_a_refresh_publishes_prices_ages_and_provider_state(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    before = time.time()
    async with running(tmp_path, fake) as (service, _store, changes):
        await eventually(lambda: items(service)["crypto:BTC"]["price"] is not None)
        btc = items(service)["crypto:BTC"]
        assert btc["currency"] == "EUR"
        assert btc["source"] == "fake"
        assert btc["name"] == "BTC SA"
        assert before <= btc["fetched"] <= time.time()
        assert btc["error"] is None
        payload = service.payload()
        assert payload["fetched_at"] >= btc["fetched"] - 1
        assert payload["providers"][0]["state"] == "ok"
        assert changes  # le dock a prévenu les navigateurs


async def test_each_kind_of_value_is_asked_at_its_own_pace(tmp_path):
    crypto = FakeProvider("crypto", "Crypto", kinds=("crypto",))
    stocks = FakeProvider("stocks", "Actions", kinds=("stock",))

    def closed(instruments, currency):
        stocks.count += 1
        return {
            i.id: quote(7000.0, state="closed", currency="EUR", source="stocks")
            for i in instruments
        }

    stocks.on_quotes = closed
    async with running(tmp_path, crypto, stocks, watchlist=[BTC, FCHI]):
        await asyncio.sleep(0.9)
        assert len(crypto.calls) >= 6  # toutes les 50 ms
        assert 1 <= len(stocks.calls) <= 4  # place fermée : toutes les 400 ms


# --- les fournisseurs se relaient --------------------------------------------------------------------


async def test_a_provider_that_is_down_is_replaced_by_the_next_and_then_left_alone(tmp_path):
    first = FakeProvider("first", "Premier")
    second = FakeProvider("second", "Second")
    first.on_quotes = lambda instruments, currency: NetworkError("Premier ne répond pas.")
    async with running(tmp_path, first, second) as (service, _store, _changes):
        await eventually(lambda: items(service)["crypto:BTC"]["price"] is not None)
        assert items(service)["crypto:BTC"]["source"] == "second"
        await asyncio.sleep(0.3)  # plusieurs passes : le premier est en pause (5 s au moins)
        assert len(first.calls) == 1
        assert len(second.calls) >= 3
        states = {p["id"]: p for p in service.payload()["providers"]}
        assert states["first"]["state"] == "paused"
        assert states["first"]["message"] == "Premier ne répond pas."
        assert states["first"]["retry_at"] > time.time()
        assert states["second"]["state"] == "ok"


async def test_a_value_unknown_to_one_provider_is_asked_to_the_next(tmp_path):
    first = FakeProvider("first", "Premier")
    second = FakeProvider("second", "Second")

    def partial(instruments, currency):
        return {
            i.id: quote(1.0, source="first") if i.symbol == "BTC" else NotFound("inconnu")
            for i in instruments
        }

    first.on_quotes = partial
    async with running(tmp_path, first, second) as (service, _store, _changes):
        await eventually(lambda: all(i["price"] is not None for i in service.payload()["items"]))
        sources = {k: v["source"] for k, v in items(service).items()}
        assert sources == {"crypto:BTC": "first", "crypto:ETH": "second"}
        assert second.calls[0][0] == ["crypto:ETH"]  # seule la valeur inconnue lui est proposée
        assert service.payload()["providers"][0]["state"] == "ok"  # le premier a bien répondu


async def test_when_everything_fails_the_last_quote_stays_with_its_age_and_the_reason(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    async with running(tmp_path, fake) as (service, _store, _changes):
        await eventually(lambda: items(service)["crypto:BTC"]["price"] is not None)
        good = dict(items(service)["crypto:BTC"])
        fake.on_quotes = lambda instruments, currency: NetworkError("Fournisseur ne répond pas.")
        await eventually(lambda: items(service)["crypto:BTC"]["error"] is not None)
        stale = items(service)["crypto:BTC"]
        assert stale["error"] == "Fournisseur : Fournisseur ne répond pas."
        assert (
            stale["price"] == good["price"] or stale["price"] is not None
        )  # la dernière cotation reste
        assert stale["fetched"] is not None
        assert service.payload()["providers"][0]["state"] in ("paused", "error")


async def test_a_value_nobody_knows_says_so(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    fake.on_quotes = lambda instruments, currency: {
        i.id: NotFound("Fournisseur ne connaît pas cette valeur.") for i in instruments
    }
    async with running(tmp_path, fake) as (service, _store, _changes):
        await eventually(lambda: items(service)["crypto:BTC"]["error"] is not None)
        btc = items(service)["crypto:BTC"]
        assert btc["price"] is None
        assert "ne connaît pas" in btc["error"]
        assert (
            service.payload()["providers"][0]["state"] == "ok"
        )  # une valeur inconnue n'est pas une panne


async def test_a_rate_limit_counts_once_per_pass_not_once_per_value(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    fake.on_quotes = lambda instruments, currency: {
        i.id: RateLimited("Trop de requêtes.", retry_after=30) for i in instruments
    }
    async with running(tmp_path, fake) as (service, _store, _changes):
        await eventually(lambda: service.payload()["providers"][0]["state"] == "paused")
        status = service._status["fake"]
        assert status.backoff.failures == 1
        retry_in = status.retry_at - time.time()
        assert 25 < retry_in <= 30


async def test_a_provider_that_crashes_is_treated_as_down_and_the_loop_survives(tmp_path):
    broken = FakeProvider("broken", "Cassé")
    good = FakeProvider("good", "Bon")
    broken.on_quotes = lambda instruments, currency: 1 / 0
    async with running(tmp_path, broken, good) as (service, _store, _changes):
        await eventually(lambda: items(service)["crypto:BTC"]["price"] is not None)
        assert items(service)["crypto:BTC"]["source"] == "good"
        assert service._status["broken"].error == "Cassé : erreur interne."


async def test_a_provider_that_hangs_times_out(tmp_path):
    slow = FakeProvider("slow", "Lent")
    good = FakeProvider("good", "Bon")
    slow.delay = 5.0
    timing = Timing(**{**FAST.__dict__, "request_timeout": 0.1})
    async with running(tmp_path, slow, good, timing=timing) as (service, _store, _changes):
        await eventually(lambda: items(service)["crypto:BTC"]["price"] is not None)
        assert items(service)["crypto:BTC"]["source"] == "good"
        assert service._status["slow"].error == "Lent ne répond pas."


async def test_a_new_quote_keeps_the_name_and_the_curve_the_provider_leaves_out(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    full = quote(10.0, name="Bitcoin SA", spark=(1.0, 2.0, 3.0))
    bare = quote(11.0)
    replies = [full, bare]
    fake.on_quotes = lambda instruments, currency: {
        i.id: replies[0 if len(fake.calls) == 1 else 1] for i in instruments
    }
    async with running(tmp_path, fake, watchlist=[BTC]) as (service, _store, _changes):
        await eventually(
            lambda: len(fake.calls) >= 2 and items(service)["crypto:BTC"]["price"] == 11.0
        )
        btc = items(service)["crypto:BTC"]
        assert btc["name"] == "Bitcoin SA"
        assert btc["spark"] == [1.0, 2.0, 3.0]


# --- les actions de l'écran --------------------------------------------------------------------------


async def test_the_refresh_button_asks_everything_now_but_not_twice_in_a_row(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    timing = Timing(**{**FAST.__dict__, "crypto_ttl": 60.0, "min_manual_refresh": 0.5})
    async with running(tmp_path, fake, timing=timing, watching=False) as (
        service,
        _store,
        _changes,
    ):
        assert (await service.refresh()) == {"refreshed": True}
        assert len(fake.calls) == 1
        assert (await service.refresh()) == {"refreshed": False}
        assert len(fake.calls) == 1
        await asyncio.sleep(0.6)
        assert (await service.refresh()) == {"refreshed": True}
        assert len(fake.calls) == 2


async def test_check_finds_a_value_and_names_it(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    async with running(tmp_path, fake, watching=False) as (service, _store, _changes):
        found = await service.check("stock", "mstr")
        assert found == {
            "id": "stock:MSTR",
            "kind": "stock",
            "symbol": "MSTR",
            "name": "MSTR SA",
            "price": 101.0,
            "currency": "EUR",
            "source": "fake",
        }
        assert fake.calls == [(["stock:MSTR"], "EUR")]
        assert (await service.check("crypto", "sol"))["id"] == "crypto:SOL"


async def test_check_names_a_crypto_even_when_the_provider_gives_no_name(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    fake.on_quotes = lambda instruments, currency: {i.id: quote(5.0) for i in instruments}
    async with running(tmp_path, fake, watching=False) as (service, _store, _changes):
        assert (await service.check("crypto", "eth"))["name"] == "Ethereum"
        assert (await service.check("stock", "zzz"))["name"] is None


async def test_check_says_the_symbol_is_unknown_when_providers_answer_but_do_not_know_it(tmp_path):
    one, two = FakeProvider("one", "Un"), FakeProvider("two", "Deux")
    for provider in (one, two):
        provider.on_quotes = lambda instruments, currency: {
            i.id: NotFound("non") for i in instruments
        }
    async with running(tmp_path, one, two, watching=False) as (service, _store, _changes):
        with pytest.raises(NotFound, match="« QQQQ » est inconnu de Un, Deux"):
            await service.check("stock", "qqqq")


async def test_check_says_it_cannot_verify_when_providers_are_down(tmp_path):
    one = FakeProvider("one", "Un")
    one.on_quotes = lambda instruments, currency: NetworkError("Un ne répond pas.")
    async with running(tmp_path, one, watching=False) as (service, _store, _changes):
        with pytest.raises(ProviderError, match=r"Impossible de vérifier.*Un : Un ne répond pas"):
            await service.check("stock", "aapl")
        with pytest.raises(
            ProviderError, match="aucun fournisseur disponible"
        ):  # il est maintenant en pause
            await service.check("stock", "aapl")


async def test_check_refuses_a_malformed_symbol_before_asking_anybody(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    async with running(tmp_path, fake, watching=False) as (service, _store, _changes):
        for kind, symbol in (
            ("stock", "A B"),
            ("crypto", ""),
            ("obligation", "X"),
            ("stock", "../x"),
        ):
            with pytest.raises(ValidationFailure):
                await service.check(kind, symbol)
        assert fake.calls == []


# --- historique ------------------------------------------------------------------------------------


async def test_history_is_cached_per_range_and_shared_between_simultaneous_requests(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    fake.on_history = lambda instrument, range_, currency: (
        [HistoryPoint(1000 + i, 100.0 + i) for i in range(10)]
    )
    async with running(tmp_path, fake, watching=False) as (service, _store, _changes):
        first, second = await asyncio.gather(
            service.history("crypto:BTC", "1d"), service.history("crypto:BTC", "1d")
        )
        assert first == second
        assert first["id"] == "crypto:BTC"
        assert first["range"] == "1d"
        assert first["source"] == "fake"
        assert first["currency"] == "EUR"
        assert first["points"][0] == [1000, 100.0]
        assert len(first["points"]) == 10
        assert len(fake.history_calls) == 1
        await service.history("crypto:BTC", "1d")
        assert len(fake.history_calls) == 1  # servi depuis la mémoire
        await service.history("crypto:BTC", "5d")
        assert fake.history_calls == [("crypto:BTC", "1d"), ("crypto:BTC", "5d")]


async def test_history_is_asked_again_after_its_lifetime(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    timing = Timing(
        **{**FAST.__dict__, "history_ttl": {"1d": 0.1, "5d": 0.1, "1mo": 0.1, "1y": 0.1}}
    )
    async with running(tmp_path, fake, timing=timing, watching=False) as (
        service,
        _store,
        _changes,
    ):
        await service.history("crypto:BTC", "1d")
        await asyncio.sleep(0.15)
        await service.history("crypto:BTC", "1d")
        assert len(fake.history_calls) == 2


async def test_history_only_serves_the_values_of_the_list_and_known_ranges(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    async with running(tmp_path, fake, watching=False) as (service, _store, _changes):
        for ident in ("stock:AAPL", "BTC", 3, "crypto:", "stock:A B"):
            with pytest.raises(ValidationFailure):
                await service.history(ident, "1d")
        for bad in ("2y", "", None, "1D"):
            with pytest.raises(ValidationFailure, match="Période"):
                await service.history("crypto:BTC", bad)
        assert fake.history_calls == []


async def test_history_skips_a_provider_that_does_not_offer_it(tmp_path):
    first, second = FakeProvider("first", "Premier"), FakeProvider("second", "Second")
    first.on_history = lambda instrument, range_, currency: Unsupported("pas d'historique")
    async with running(tmp_path, first, second, watching=False) as (service, _store, _changes):
        assert (await service.history("crypto:BTC", "1mo"))["source"] == "second"
        assert service.payload()["providers"][0]["state"] == "idle"  # ce n'est pas une panne


async def test_history_failures_are_explained(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    fake.on_history = lambda instrument, range_, currency: NetworkError(
        "Fournisseur ne répond pas."
    )
    async with running(tmp_path, fake, watching=False) as (service, _store, _changes):
        with pytest.raises(
            ProviderError, match="Historique indisponible : Fournisseur : Fournisseur ne répond pas"
        ):
            await service.history("crypto:BTC", "1d")
    short = FakeProvider("short", "Court")
    short.on_history = lambda instrument, range_, currency: [HistoryPoint(1, 1.0)]
    async with running(tmp_path / "b", short, watching=False) as (service, _store, _changes):
        with pytest.raises(ProviderError, match="pas assez de points"):
            await service.history("crypto:BTC", "1d")
    nobody = FakeProvider("nobody", "Personne", kinds=())
    async with running(tmp_path / "c", nobody, watching=False) as (service, _store, _changes):
        with pytest.raises(ProviderError, match="aucun fournisseur ne l'offre"):
            await service.history("crypto:BTC", "1d")


# --- liste suivie et devise -------------------------------------------------------------------------


async def test_changing_the_list_drops_the_old_values_and_asks_for_the_new_ones(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    async with running(tmp_path, fake) as (service, store, _changes):
        await eventually(lambda: items(service)["crypto:ETH"]["price"] is not None)
        store.update_markets({"watchlist": [ETH.to_dict(), AAPL.to_dict()]})
        service.watchlist_changed()
        assert list(items(service)) == ["crypto:ETH", "stock:AAPL"]
        assert items(service)["crypto:ETH"]["price"] is not None  # ce qu'on avait reste affiché
        assert items(service)["stock:AAPL"]["price"] is None
        await eventually(lambda: items(service)["stock:AAPL"]["price"] is not None)
        assert "crypto:BTC" not in service._entries


async def test_changing_the_currency_asks_again_in_the_new_currency(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    async with running(tmp_path, fake, watchlist=[BTC]) as (service, store, _changes):
        await eventually(lambda: items(service)["crypto:BTC"]["currency"] == "EUR")
        store.update_markets({"currency": "USD"})
        service.watchlist_changed()
        await eventually(lambda: items(service)["crypto:BTC"]["currency"] == "USD")
        assert fake.calls[-1][1] == "USD"
        assert service.payload()["currency"] == "USD"


async def test_a_damaged_list_in_the_state_file_does_not_stop_the_dock(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    async with running(tmp_path, fake) as (service, store, _changes):
        store._data["markets"]["watchlist"] = [{"kind": "stock", "symbol": "A B"}, 3]
        assert service.watchlist == []
        assert service.payload()["items"] == []


# --- gardé sur le disque ----------------------------------------------------------------------------


async def test_the_last_quotes_survive_a_restart_and_show_their_age(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    async with running(tmp_path, fake, cache=True) as (service, _store, _changes):
        await eventually(lambda: items(service)["crypto:ETH"]["price"] is not None)
        service.set_watching(False)
        await asyncio.sleep(0.15)
        before = items(service)["crypto:BTC"]
    cache = tmp_path / "state" / "markets-cache.json"
    assert cache.is_file()
    again = FakeProvider("again", "Autre")
    async with running(tmp_path, again, cache=True, watching=False) as (service, _store, _changes):
        btc = items(service)["crypto:BTC"]
        assert btc["price"] == before["price"]
        assert btc["fetched"] == before["fetched"]
        assert btc["name"] == before["name"]
        assert again.calls == []  # montré sans rien demander


async def test_the_cache_ignores_what_it_must_not_trust(tmp_path):
    store = StateStore(tmp_path / "state")
    path = tmp_path / "state" / "markets-cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    def attempt(data) -> dict:
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        service = MarketsService(store=store, providers=[], cache_path=path, timing=FAST)
        return items(service)["crypto:BTC"]

    good = {"fetched": time.time() - 60, "quote": {"price": 5.0, "source": "x", "currency": "EUR"}}
    base = {"version": 1, "currency": "EUR", "items": {"crypto:BTC": good}}
    assert attempt(base)["price"] == 5.0
    assert attempt({**base, "currency": "USD"})["price"] is None  # autre devise : autres prix
    assert attempt({**base, "version": 9})["price"] is None
    old = {**good, "fetched": time.time() - 8 * 86_400}
    assert attempt({**base, "items": {"crypto:BTC": old}})["price"] is None
    assert (
        attempt({**base, "items": {"crypto:BTC": {**good, "quote": {"price": "x"}}}})["price"]
        is None
    )
    assert (
        attempt({**base, "items": {"crypto:BTC": {**good, "quote": {"price": float("nan")}}}})[
            "price"
        ]
        is None
    )
    assert attempt({**base, "items": {"crypto:BTC": "x"}})["price"] is None
    assert attempt({**base, "items": {"stock:AAPL": good}})["price"] is None  # pas dans la liste
    assert attempt("{ pas du json")["price"] is None
    assert attempt([1, 2])["price"] is None


async def test_the_cache_is_written_atomically_and_contains_no_secret(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    async with running(tmp_path, fake, cache=True) as (service, _store, _changes):
        await eventually(lambda: (tmp_path / "state" / "markets-cache.json").is_file())
        service.save()
    raw = json.loads((tmp_path / "state" / "markets-cache.json").read_text())
    assert raw["version"] == 1
    assert raw["currency"] == "EUR"
    assert set(raw["items"]) <= {"crypto:BTC", "crypto:ETH"}
    assert not list((tmp_path / "state").glob(".markets.*"))  # pas de fichier temporaire oublié


# --- annonce des changements ------------------------------------------------------------------------


async def test_listeners_are_told_only_when_the_payload_changes(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    fake.on_quotes = lambda instruments, currency: {i.id: quote(5.0) for i in instruments}
    timing = Timing(**{**FAST.__dict__, "crypto_ttl": 60.0})
    async with running(tmp_path, fake, timing=timing, watching=False) as (service, _store, changes):
        await service.refresh()
        count = len(changes)
        assert count >= 1
        service._publish()
        service._publish()
        assert len(changes) == count  # rien n'a changé : personne n'est dérangé


# --- recherche -------------------------------------------------------------------------------------


async def test_search_puts_the_known_cryptos_first_and_adds_what_the_provider_finds(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    fake.on_search = lambda kind, query: [
        Candidate("crypto", "STRK", "Strike", None, "Crypto"),
        Candidate("crypto", "BTC", "Bitcoin (doublon)", None, "Crypto"),
    ]
    async with running(tmp_path, fake, watching=False) as (service, _store, _changes):
        found = await service.search("crypto", "  bit  ")
        assert found["query"] == "bit"
        assert found["kind"] == "crypto"
        assert found["source"] == "fake"
        assert [r["symbol"] for r in found["results"]] == ["BTC", "BCH", "STRK"]
        assert found["results"][0] == {
            "kind": "crypto", "symbol": "BTC", "name": "Bitcoin", "exchange": None, "label": "Crypto",
        }  # fmt: skip
        assert fake.searches == [("crypto", "bit")]


async def test_search_for_a_stock_asks_the_providers_only(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    fake.on_search = lambda kind, query: (
        [Candidate("stock", "AAPL", "Apple Inc.", "NASDAQ", "Action")] if query == "apple" else []
    )
    async with running(tmp_path, fake, watching=False) as (service, _store, _changes):
        found = await service.search("stock", "apple")
        assert [r["symbol"] for r in found["results"]] == ["AAPL"]
        assert found["results"][0]["exchange"] == "NASDAQ"
        empty = await service.search("stock", "rien du tout")
        assert empty["results"] == []  # rien trouvé n'est pas une erreur
        assert empty["source"] == "fake"


async def test_search_normalizes_spaces_and_refuses_bad_queries(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    async with running(tmp_path, fake, watching=False) as (service, _store, _changes):
        assert (await service.search("stock", "  apple \t  inc "))["query"] == "apple inc"
        for kind, query in (
            ("stock", "a"),
            ("stock", " "),
            ("stock", "x" * 61),
            ("stock", "bell\x07"),
            ("stock", None),
            ("stock", 12),
            ("bond", "apple"),
            (None, "apple"),
        ):
            with pytest.raises(ValidationFailure):
                await service.search(kind, query)
        assert len(fake.searches) == 1


async def test_search_still_finds_known_cryptos_when_the_provider_is_down(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    fake.on_search = lambda kind, query: NetworkError("Fournisseur ne répond pas.")
    async with running(tmp_path, fake, watching=False) as (service, _store, _changes):
        found = await service.search("crypto", "eth")
        assert [r["symbol"] for r in found["results"]][:1] == ["ETH"]
        assert found["source"] == "local"
        assert service.payload()["providers"][0]["state"] == "paused"


async def test_search_explains_when_nothing_can_be_searched(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")
    fake.on_search = lambda kind, query: NetworkError("Fournisseur ne répond pas.")
    async with running(tmp_path, fake, watching=False) as (service, _store, _changes):
        with pytest.raises(
            ProviderError, match="Recherche impossible : Fournisseur : Fournisseur ne répond pas"
        ):
            await service.search("stock", "apple")
        with pytest.raises(
            ProviderError, match="Recherche impossible"
        ):  # puis le fournisseur est en pause
            await service.search("stock", "apple")


async def test_search_skips_providers_that_cannot_search(tmp_path):
    class NoSearch(FakeProvider):
        search = None

    mute = NoSearch("mute", "Muet")
    fake = FakeProvider("fake", "Fournisseur")
    fake.on_search = lambda kind, query: [Candidate("stock", "MSTR", "Strategy", None, "Action")]
    async with running(tmp_path, mute, fake, watching=False) as (service, _store, _changes):
        assert [r["symbol"] for r in (await service.search("stock", "strat"))["results"]] == [
            "MSTR"
        ]


# --- fréquence ---------------------------------------------------------------------------------------


async def test_the_refresh_pace_slows_every_rhythm_down(tmp_path):
    quick = FakeProvider("quick", "Vif")
    async with running(tmp_path / "a", quick, watchlist=[BTC]):
        await asyncio.sleep(0.8)
    calm = FakeProvider("calm", "Calme")
    async with running(tmp_path / "b", calm, watchlist=[BTC]) as (service, store, _changes):
        store.update_markets({"refresh": 60})  # quatre fois plus lent
        service.watchlist_changed()
        assert service.payload()["refresh"] == 60
        await asyncio.sleep(0.8)
    assert len(quick.calls) >= 8
    assert len(calm.calls) <= 6
    assert len(quick.calls) > 2 * len(calm.calls)


async def test_the_refresh_pace_never_makes_a_closed_market_faster_than_its_own_rhythm(tmp_path):
    fake = FakeProvider("fake", "Fournisseur")

    def closed(instruments, currency):
        return {i.id: quote(7000.0, state="closed", source="fake") for i in instruments}

    fake.on_quotes = closed
    timing = Timing(**{**FAST.__dict__, "stock_ttl": 0.05, "closed_ttl": 0.5})
    async with running(tmp_path, fake, watchlist=[FCHI], timing=timing):
        await asyncio.sleep(1.2)
    assert len(fake.calls) <= 4  # place fermée : toutes les 0,5 s, quel que soit le rythme vif
