"""Navigateur simulé -> vrai serveur dock -> faux fournisseurs : la bourse, PC éteint ou allumé."""

import asyncio
import json
from pathlib import Path

import pytest

from mydock_common.config import load_toml
from mydock_dock.config import DockConfig
from mydock_dock.markets.errors import NetworkError
from mydock_dock.markets.service import Timing
from mydock_dock.markets.testing import FakeFetcher, json_response, text_response
from mydock_dock.testing import (
    call,
    eventually,
    recv_json,
    recv_until,
    running_dock,
    send_json,
    snapshot_of,
)

DATA = Path(__file__).parent / "data" / "markets"
TIMING = Timing(
    crypto_ttl=0.2,
    stock_ttl=0.2,
    closed_ttl=0.2,
    error_retry=0.2,
    min_sleep=0.02,
    min_manual_refresh=0.0,
    cache_write_every=0.0,
)


def sample(name):
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


def binance_fetcher() -> FakeFetcher:
    fetcher = FakeFetcher()
    fetcher.on("/ticker/24hr", json_response(sample("binance_ticker_batch")))
    fetcher.on("/klines", json_response(sample("binance_klines_30m")))
    fetcher.on("chart/AAPL", json_response(sample("yahoo_aapl_1d")))
    fetcher.on("chart/MSTR", json_response(sample("yahoo_aapl_1d")))
    return fetcher


async def subscribe(ws, *topics):
    await send_json(ws, {"t": "sub", "topics": list(topics)})


async def priced(ws, ident="crypto:BTC"):
    """Attend un instantané de « markets » où la valeur a un prix."""

    def has_price(message):
        if message.get("t") != "snap" or message.get("topic") != "markets":
            return False
        return any(i["id"] == ident and i["price"] is not None for i in message["data"]["items"])

    return (await recv_until(ws, has_price, timeout=8))["data"]


async def test_the_page_gets_the_list_at_once_and_prices_as_soon_as_they_arrive(tmp_path):
    fetcher = binance_fetcher()
    async with (
        running_dock(tmp_path, markets_fetcher=fetcher, markets_timing=TIMING) as dock,
        dock.connect() as ws,
    ):
        await recv_json(ws)  # hello
        await subscribe(ws, "markets")
        first = await snapshot_of(ws, "markets")
        assert [i["id"] for i in first["data"]["items"]] == ["crypto:BTC", "crypto:ETH"]
        assert first["data"]["currency"] == "EUR"
        data = await priced(ws)
        btc = data["items"][0]
        assert (btc["price"], btc["currency"], btc["source"]) == (61250.5, "EUR", "binance")
        assert len(btc["spark"]) == 48
        assert {p["id"]: p["state"] for p in data["providers"]}["binance"] == "ok"


async def test_markets_are_not_pushed_until_asked_and_work_with_the_pc_off(tmp_path):
    fetcher = binance_fetcher()
    async with running_dock(tmp_path, markets_fetcher=fetcher, markets_timing=TIMING) as dock:
        assert not dock.server.link.connected  # aucun agent : le PC est « éteint »
        async with dock.connect() as ws:
            topics = []
            while len(topics) < 3:
                message = await recv_json(ws)
                if message.get("t") == "snap":
                    topics.append(message["topic"])
            assert sorted(topics) == ["dock", "link", "ui"]
            await asyncio.sleep(0.4)
            assert fetcher.calls == []  # personne n'a demandé la bourse : aucune requête
            await subscribe(ws, "markets")
            assert (await priced(ws))["items"][0]["price"] == 61250.5


async def test_quotes_stop_being_asked_when_the_last_page_closes(tmp_path):
    fetcher = binance_fetcher()
    async with running_dock(tmp_path, markets_fetcher=fetcher, markets_timing=TIMING) as dock:
        service = dock.server.markets
        async with dock.connect() as first, dock.connect() as second:
            await subscribe(first, "markets")
            await subscribe(second, "markets")
            await priced(first)
            await first.close()
            await eventually(lambda: len(dock.server.browsers) == 1)
            assert service._watching  # la seconde page regarde encore
            await send_json(second, {"t": "unsub", "topics": ["markets"]})
            await eventually(lambda: not service._watching)
        await asyncio.sleep(0.5)  # la passe en cours se termine
        seen = len(fetcher.calls)
        await asyncio.sleep(0.6)
        assert len(fetcher.calls) == seen


async def test_changing_the_list_is_remembered_and_pushed(tmp_path):
    fetcher = binance_fetcher()
    async with running_dock(tmp_path, markets_fetcher=fetcher, markets_timing=TIMING) as dock:
        async with dock.connect() as ws:
            await subscribe(ws, "markets")
            await priced(ws)
            ack = await call(
                ws,
                "m1",
                "dock.markets.set",
                {
                    "watchlist": [
                        {"kind": "stock", "symbol": "mstr"},
                        {"kind": "crypto", "symbol": "btc"},
                    ],
                    "currency": "USD",
                },
            )
            assert ack["ok"] is True
            assert ack["r"]["currency"] == "USD"
            assert ack["r"]["watchlist"] == [
                {"kind": "stock", "symbol": "MSTR"},
                {"kind": "crypto", "symbol": "BTC"},
            ]
            data = (
                await recv_until(
                    ws,
                    lambda m: (
                        m.get("topic") == "markets"
                        and [i["id"] for i in m["data"]["items"]] == ["stock:MSTR", "crypto:BTC"]
                    ),
                )
            )["data"]
            assert data["currency"] == "USD"
        state = json.loads((tmp_path / "state" / "state.json").read_text())
        assert state["markets"]["currency"] == "USD"
        assert state["markets"]["watchlist"][0] == {"kind": "stock", "symbol": "MSTR"}


@pytest.mark.parametrize(
    "params",
    [
        {"watchlist": [{"kind": "crypto", "symbol": "BTC"}, {"kind": "crypto", "symbol": "btc"}]},
        {"watchlist": [{"kind": "bourse", "symbol": "BTC"}]},
        {"watchlist": [{"kind": "stock", "symbol": "../etc"}]},
        {"watchlist": [{"kind": "stock", "symbol": f"S{i}"} for i in range(21)]},
        {"watchlist": [{"kind": "stock", "symbol": "AAPL", "url": "http://x"}]},
        {"watchlist": "BTC"},
        {"currency": "GBP"},
        {"currency": "eur"},
        {"inconnu": 1},
    ],
)
async def test_a_bad_list_or_currency_is_refused_and_changes_nothing(tmp_path, params):
    async with running_dock(tmp_path, markets_fetcher=FakeFetcher(), markets_timing=TIMING) as dock:
        async with dock.connect() as ws:
            ack = await call(ws, "m1", "dock.markets.set", params)
            assert ack["ok"] is False
            assert ack["e"]["code"] == "bad_params"
        assert dock.server.store.markets["currency"] == "EUR"
        assert [i["symbol"] for i in dock.server.store.markets["watchlist"]] == ["BTC", "ETH"]


async def test_check_names_a_value_before_it_is_added(tmp_path):
    fetcher = binance_fetcher()
    async with (
        running_dock(tmp_path, markets_fetcher=fetcher, markets_timing=TIMING) as dock,
        dock.connect() as ws,
    ):
        ack = await call(ws, "c1", "dock.markets.check", {"kind": "stock", "symbol": "aapl"})
        assert ack["ok"] is True
        assert ack["r"]["name"] == "Apple Inc."
        assert (ack["r"]["price"], ack["r"]["currency"], ack["r"]["source"]) == (
            231.2,
            "USD",
            "yahoo",
        )
        ack = await call(ws, "c2", "dock.markets.check", {"kind": "crypto", "symbol": "btc"})
        assert ack["r"]["name"] == "Bitcoin"
        bad = await call(ws, "c3", "dock.markets.check", {"kind": "stock", "symbol": "A B"})
        assert bad["e"]["code"] == "bad_params"


async def test_check_reports_an_unknown_symbol_and_a_provider_that_is_down(tmp_path):
    fetcher = FakeFetcher()
    fetcher.on("chart/QQQQ", json_response(sample("yahoo_not_found"), status=404))
    async with (
        running_dock(tmp_path, markets_fetcher=fetcher, markets_timing=TIMING) as dock,
        dock.connect() as ws,
    ):
        unknown = await call(ws, "c1", "dock.markets.check", {"kind": "stock", "symbol": "qqqq"})
        assert unknown["ok"] is False
        assert unknown["e"]["code"] == "not_found"
        assert "« QQQQ » est inconnu de Yahoo Finance" in unknown["e"]["msg"]
    down = FakeFetcher().on(
        "chart/", NetworkError("query2.finance.yahoo.com ne répond pas (délai dépassé).")
    )
    async with (
        running_dock(tmp_path / "b", markets_fetcher=down, markets_timing=TIMING) as dock,
        dock.connect() as ws,
    ):
        ack = await call(ws, "c2", "dock.markets.check", {"kind": "stock", "symbol": "aapl"})
        assert ack["e"]["code"] == "unavailable"
        assert "Impossible de vérifier" in ack["e"]["msg"]
        assert "ne répond pas" in ack["e"]["msg"]


async def test_history_comes_back_for_a_value_of_the_list(tmp_path):
    fetcher = binance_fetcher()
    async with (
        running_dock(tmp_path, markets_fetcher=fetcher, markets_timing=TIMING) as dock,
        dock.connect() as ws,
    ):
        ack = await call(ws, "h1", "dock.markets.history", {"id": "crypto:BTC", "range": "5d"})
        assert ack["ok"] is True
        assert ack["r"]["range"] == "5d"
        assert ack["r"]["source"] == "binance"
        assert len(ack["r"]["points"]) == 48
        assert ack["r"]["points"][0][0] < ack["r"]["points"][-1][0]
        klines = [
            c for c in fetcher.calls if "/klines" in c.url and c.params.get("interval") == "30m"
        ]
        assert klines  # la période de 5 jours = bougies de 30 minutes
        outside = await call(ws, "h2", "dock.markets.history", {"id": "stock:AAPL", "range": "1d"})
        assert outside["e"]["code"] == "bad_params"
        wrong = await call(ws, "h3", "dock.markets.history", {"id": "crypto:BTC", "range": "10y"})
        assert wrong["e"]["code"] == "bad_params"


async def test_the_refresh_button_asks_now(tmp_path):
    fetcher = binance_fetcher()
    timing = Timing(**{**TIMING.__dict__, "crypto_ttl": 600.0})
    async with (
        running_dock(tmp_path, markets_fetcher=fetcher, markets_timing=timing) as dock,
        dock.connect() as ws,
    ):
        await subscribe(ws, "markets")
        await priced(ws)
        before = len(fetcher.calls_to("/ticker/24hr"))
        ack = await call(ws, "r1", "dock.markets.refresh")
        assert ack["r"] == {"refreshed": True}
        assert len(fetcher.calls_to("/ticker/24hr")) == before + 1


async def test_provider_failures_reach_the_screen_with_the_old_quote(tmp_path):
    fetcher = binance_fetcher()
    async with (
        running_dock(tmp_path, markets_fetcher=fetcher, markets_timing=TIMING) as dock,
        dock.connect() as ws,
    ):
        await subscribe(ws, "markets")
        await priced(ws)
        fetcher._routes.insert(0, (lambda c: True, [text_response("", status=503)]))
        data = (
            await recv_until(
                ws,
                lambda m: (
                    m.get("topic") == "markets" and m["data"]["items"][0]["error"] is not None
                ),
                timeout=8,
            )
        )["data"]
        btc = data["items"][0]
        assert btc["price"] == 61250.5  # la dernière cotation reste affichée
        assert "indisponible" in btc["error"]
        assert btc["fetched"] is not None
        states = {p["id"]: p["state"] for p in data["providers"]}
        assert states["binance"] in ("paused", "error")


async def test_demo_markets_need_no_network_and_say_so(tmp_path):
    async with (
        running_dock(tmp_path, demo_markets=True, markets_timing=TIMING) as dock,
        dock.connect() as ws,
    ):
        await subscribe(ws, "markets")
        data = await priced(ws)
        assert data["demo"] is True
        assert data["items"][0]["source"] == "demo"
        assert [p["id"] for p in data["providers"]] == ["demo"]
        ack = await call(ws, "c1", "dock.markets.check", {"kind": "stock", "symbol": "nope"})
        assert ack["e"]["code"] == "not_found"
    assert not (tmp_path / "state" / "markets-cache.json").exists()  # la démo ne laisse rien


async def test_the_cache_file_brings_the_last_quotes_back_after_a_restart(tmp_path):
    fetcher = binance_fetcher()
    async with (
        running_dock(tmp_path, markets_fetcher=fetcher, markets_timing=TIMING) as dock,
        dock.connect() as ws,
    ):
        await subscribe(ws, "markets")
        await priced(ws)
    assert (tmp_path / "state" / "markets-cache.json").is_file()
    offline = FakeFetcher()  # le réseau est coupé au redémarrage
    async with (
        running_dock(tmp_path, markets_fetcher=offline, markets_timing=TIMING) as dock,
        dock.connect() as ws,
    ):
        await subscribe(ws, "markets")
        first = (await snapshot_of(ws, "markets"))["data"]
        assert first["items"][0]["price"] == 61250.5
        assert first["items"][0]["fetched"] is not None


def test_the_configuration_keeps_the_api_key_private(tmp_path):
    path = tmp_path / "dock.toml"
    config = DockConfig.from_dict({"markets": {"finnhub_key": "abcdef123456"}}, path)
    assert config.finnhub_key == "abcdef123456"
    assert "abcdef123456" not in repr(config)  # un secret n'est jamais affiché
    config.set_wol_mac("02:00:5e:10:00:01")  # une écriture quelconque n'efface pas la clé
    assert load_toml(path)["markets"]["finnhub_key"] == "abcdef123456"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert DockConfig.from_dict({}, path).finnhub_key == ""
    for bad in ("court", "a b c d e f g h", "é" * 10, "x" * 200):
        with pytest.raises(Exception, match="finnhub_key"):
            DockConfig.from_dict({"markets": {"finnhub_key": bad}}, path)


async def test_the_dock_topic_summarizes_the_markets_without_any_subscription(tmp_path):
    fetcher = FakeFetcher()
    async with (
        running_dock(tmp_path, markets_fetcher=fetcher, markets_timing=TIMING) as dock,
        dock.connect() as ws,
    ):
        dock.config.finnhub_key = "cle-secrete-123"
        info = dock.server.payload("dock")["markets"]
        assert info == {"count": 2, "currency": "EUR", "finnhub": True, "demo": False}
        assert "cle-secrete-123" not in json.dumps(dock.server.payload("dock"))
        ack = await call(
            ws, "m1", "dock.markets.set", {"watchlist": [{"kind": "stock", "symbol": "AAPL"}]}
        )
        assert ack["ok"] is True
        pushed = await recv_until(
            ws, lambda m: m.get("topic") == "dock" and m["data"]["markets"]["count"] == 1
        )
        assert pushed["data"]["markets"]["currency"] == "EUR"
        assert fetcher.calls == []  # le résumé ne demande aucune cotation
