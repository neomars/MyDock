import json
from pathlib import Path

import pytest

from mydock_dock.markets.errors import (
    BadResponse,
    Blocked,
    NetworkError,
    NotFound,
    RateLimited,
    Unavailable,
    Unsupported,
)
from mydock_dock.markets.limits import Gate
from mydock_dock.markets.model import Instrument, Quote
from mydock_dock.markets.providers import binance, demo, finnhub, yahoo
from mydock_dock.markets.testing import FakeFetcher, json_response, text_response

DATA = Path(__file__).parent / "data" / "markets"
NOW = 1793370000  # 30 octobre 2026, 14 h 20 UTC : la Bourse de New York est ouverte

AAPL = Instrument("stock", "AAPL")
FCHI = Instrument("stock", "^FCHI")
BTC = Instrument("crypto", "BTC")
ETH = Instrument("crypto", "ETH")
SOL = Instrument("crypto", "SOL")


def sample(name: str):
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


def instant_gate(**kwargs) -> Gate:
    return Gate(interval=0, concurrency=kwargs.get("concurrency", 4))


# --- Yahoo Finance -----------------------------------------------------------------------------


def test_yahoo_reads_a_stock_with_its_day_curve():
    quote = yahoo.parse_quote(sample("yahoo_aapl_1d"), AAPL, now=NOW)
    assert quote.price == 231.2
    assert quote.currency == "USD"
    assert quote.source == "yahoo"
    assert quote.name == "Apple Inc."
    assert quote.change == pytest.approx(2.7)
    assert quote.change_pct == pytest.approx(1.1816, abs=1e-4)
    assert (quote.high, quote.low) == (232.4, 227.9)
    assert quote.state == "open"
    assert quote.traded_at == 1793369990
    assert 20 <= len(quote.spark) <= 48  # les deux trous (null) de la courbe sont sautés
    assert all(isinstance(v, float) for v in quote.spark)
    assert quote.spark[-1] == 231.2


@pytest.mark.parametrize(
    ("now", "state"),
    [
        (1793340000, "closed"),  # avant l'ouverture de Paris (Euronext n'a pas de pré-séance)
        (NOW, "open"),
        (1793380000, "closed"),  # après la clôture de 17 h 30
    ],
)
def test_the_market_state_follows_the_trading_hours_yahoo_gives(now, state):
    assert yahoo.parse_quote(sample("yahoo_fchi_1d"), FCHI, now=now).state == state


def test_the_nyse_pre_and_post_sessions_are_told_apart():
    body = sample("yahoo_aapl_1d")
    assert yahoo.parse_quote(body, AAPL, now=1793350000).state == "pre"
    assert yahoo.parse_quote(body, AAPL, now=1793395000).state == "post"
    assert yahoo.parse_quote(body, AAPL, now=1793410000).state == "closed"


def test_an_index_reports_its_currency_and_its_name():
    quote = yahoo.parse_quote(sample("yahoo_fchi_1d"), FCHI, now=NOW)
    assert (quote.currency, quote.name) == ("EUR", "CAC 40")
    assert quote.change == pytest.approx(-18.5)
    assert quote.change_pct < 0


def test_a_crypto_on_yahoo_has_no_market_state_and_a_known_name():
    quote = yahoo.parse_quote(sample("yahoo_btc_eur_1d"), BTC, now=NOW)
    assert quote.state is None
    assert quote.name == "Bitcoin"
    assert quote.currency == "EUR"
    assert len(quote.spark) == 48
    assert quote.price == 61250.5


def test_a_missing_price_falls_back_to_the_last_point_of_the_curve():
    body = sample("yahoo_aapl_1d")
    del body["chart"]["result"][0]["meta"]["regularMarketPrice"]
    assert yahoo.parse_quote(body, AAPL, now=NOW).price == 231.2


def test_a_missing_previous_close_means_no_variation_not_a_wrong_one():
    body = sample("yahoo_aapl_1d")
    del body["chart"]["result"][0]["meta"]["chartPreviousClose"]
    quote = yahoo.parse_quote(body, AAPL, now=NOW)
    assert (quote.change, quote.change_pct) == (None, None)
    body["chart"]["result"][0]["meta"]["previousClose"] = 230.0
    assert yahoo.parse_quote(body, AAPL, now=NOW).change == pytest.approx(1.2)


def test_yahoo_errors_are_told_apart():
    with pytest.raises(NotFound):
        yahoo.parse_quote(sample("yahoo_not_found"), AAPL, now=NOW)
    with pytest.raises(BadResponse, match="interval=7m"):
        yahoo.parse_quote(sample("yahoo_error"), AAPL, now=NOW)
    with pytest.raises(BadResponse, match="pas de cours"):
        yahoo.parse_quote(sample("yahoo_no_price"), AAPL, now=NOW)
    for odd in ([], "texte", {"chart": []}, {"chart": {"result": [{"meta": 3}], "error": None}}):
        with pytest.raises(BadResponse):
            yahoo.parse_quote(odd, AAPL, now=NOW)
    with pytest.raises(NotFound):
        yahoo.parse_quote({"chart": {"result": [], "error": None}}, AAPL, now=NOW)


def test_trading_hours_without_regular_session_give_no_state():
    assert yahoo.market_state({}, NOW) is None
    assert yahoo.market_state({"currentTradingPeriod": {"pre": {}}}, NOW) is None
    assert (
        yahoo.market_state({"currentTradingPeriod": {"regular": {"start": "x", "end": 1}}}, NOW)
        is None
    )


async def test_yahoo_asks_one_request_per_value_with_a_browser_identity():
    fetcher = FakeFetcher()
    fetcher.on("/AAPL", json_response(sample("yahoo_aapl_1d")))
    fetcher.on("%5EFCHI", json_response(sample("yahoo_fchi_1d")))
    provider = yahoo.YahooProvider(fetcher, gate=instant_gate(), now=lambda: NOW)
    results = await provider.quotes([AAPL, FCHI], "EUR")
    assert sorted(results) == ["stock:AAPL", "stock:^FCHI"]
    assert all(isinstance(r, Quote) for r in results.values())
    first = fetcher.calls[0]
    assert first.headers["User-Agent"].startswith("Mozilla/5.0")
    assert first.params == {"range": "1d", "interval": "5m", "includePrePost": "false"}
    assert {c.url for c in fetcher.calls} == {
        "https://query2.finance.yahoo.com/v8/finance/chart/AAPL",
        "https://query2.finance.yahoo.com/v8/finance/chart/%5EFCHI",
    }


async def test_symbols_are_escaped_into_the_path_and_cryptos_carry_the_currency():
    fetcher = FakeFetcher().on("chart/", json_response(sample("yahoo_btc_eur_1d")))
    provider = yahoo.YahooProvider(fetcher, gate=instant_gate(), now=lambda: NOW)
    await provider.quotes(
        [BTC, Instrument("stock", "EURUSD=X"), Instrument("stock", "BRK-B")], "EUR"
    )
    assert sorted(c.url.rsplit("/", 1)[1] for c in fetcher.calls) == [
        "BRK-B",
        "BTC-EUR",
        "EURUSD%3DX",
    ]
    assert yahoo.yahoo_symbol(BTC, "USD") == "BTC-USD"
    assert yahoo.yahoo_symbol(AAPL, "USD") == "AAPL"


async def test_one_unknown_value_does_not_spoil_the_others():
    fetcher = FakeFetcher()
    fetcher.on("/AAPL", json_response(sample("yahoo_aapl_1d")))
    fetcher.on("/ZZZZ", json_response(sample("yahoo_not_found"), status=404))
    provider = yahoo.YahooProvider(fetcher, gate=instant_gate(), now=lambda: NOW)
    results = await provider.quotes([AAPL, Instrument("stock", "ZZZZ")], "EUR")
    assert isinstance(results["stock:AAPL"], Quote)
    assert isinstance(results["stock:ZZZZ"], NotFound)


async def test_a_404_without_a_readable_body_is_an_unknown_value():
    fetcher = FakeFetcher().on("/QQQQ", text_response("<html>Not Found</html>", status=404))
    provider = yahoo.YahooProvider(fetcher, gate=instant_gate(), now=lambda: NOW)
    fetcher.on("/AAPL", json_response(sample("yahoo_aapl_1d")))
    results = await provider.quotes([Instrument("stock", "QQQQ"), AAPL], "EUR")
    assert isinstance(results["stock:QQQQ"], NotFound)


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (
            text_response("Too Many Requests", status=429, headers={"Retry-After": "30"}),
            RateLimited,
        ),
        (text_response("", status=403), Blocked),
        (text_response("", status=503), Unavailable),
        (text_response("<html>pas du json</html>"), BadResponse),
        (NetworkError("Yahoo ne répond pas"), NetworkError),
    ],
)
async def test_when_every_request_fails_the_whole_provider_is_reported_down(reply, error):
    fetcher = FakeFetcher().on("chart/", reply)
    provider = yahoo.YahooProvider(fetcher, gate=instant_gate(), now=lambda: NOW)
    with pytest.raises(error) as caught:
        await provider.quotes([AAPL, FCHI], "EUR")
    assert caught.value.provider_down


async def test_the_retry_after_of_yahoo_reaches_the_caller():
    fetcher = FakeFetcher().on(
        "chart/", text_response("Too Many Requests", status=429, headers={"Retry-After": "30"})
    )
    provider = yahoo.YahooProvider(fetcher, gate=instant_gate(), now=lambda: NOW)
    with pytest.raises(RateLimited) as caught:
        await provider.quotes([AAPL], "EUR")
    assert caught.value.retry_after == 30


@pytest.mark.parametrize(
    ("range_", "interval"), [("1d", "5m"), ("5d", "15m"), ("1mo", "1h"), ("1y", "1d")]
)
async def test_yahoo_history_asks_the_right_granularity(range_, interval):
    fetcher = FakeFetcher().on("chart/", json_response(sample("yahoo_aapl_1mo")))
    provider = yahoo.YahooProvider(fetcher, gate=instant_gate(), now=lambda: NOW)
    points = await provider.history(AAPL, range_, "EUR")
    assert fetcher.calls[0].params["range"] == range_
    assert fetcher.calls[0].params["interval"] == interval
    assert len(points) == 159  # le dernier point, vide, est sauté
    assert points[0].at < points[-1].at
    assert all(isinstance(p.close, float) for p in points)


async def test_a_long_history_is_thinned_for_the_screen():
    body = sample("yahoo_aapl_1mo")
    result = body["chart"]["result"][0]
    stamps = [1793000000 + 600 * i for i in range(1000)]
    result["timestamp"] = stamps
    result["indicators"]["quote"][0]["close"] = [100 + i * 0.01 for i in range(1000)]
    fetcher = FakeFetcher().on("chart/", json_response(body))
    points = await yahoo.YahooProvider(fetcher, gate=instant_gate()).history(AAPL, "1y", "EUR")
    assert len(points) == 240
    assert (points[0].at, points[-1].at) == (stamps[0], stamps[-1])


# --- Binance -----------------------------------------------------------------------------------


async def test_binance_gives_every_crypto_in_one_request_and_a_sparkline_each():
    fetcher = FakeFetcher()
    fetcher.on("/ticker/24hr", json_response(sample("binance_ticker_batch")))
    fetcher.on("/klines", json_response(sample("binance_klines_30m")))
    provider = binance.BinanceProvider(fetcher, gate=instant_gate())
    results = await provider.quotes([BTC, ETH], "EUR")
    btc, eth = results["crypto:BTC"], results["crypto:ETH"]
    assert isinstance(btc, Quote)
    assert isinstance(eth, Quote)
    assert (btc.price, btc.currency, btc.source, btc.name) == (61250.5, "EUR", "binance", "Bitcoin")
    assert (btc.change, btc.change_pct) == (850.5, 1.409)
    assert (btc.high, btc.low) == (61800.0, 60100.0)
    assert btc.traded_at == 1793369999
    assert eth.change_pct == -1.11
    assert len(btc.spark) == 48
    ticker_calls = fetcher.calls_to("/ticker/24hr")
    assert len(ticker_calls) == 1  # une seule requête pour toutes les cryptos
    assert ticker_calls[0].url == "https://api.binance.com/api/v3/ticker/24hr"
    assert ticker_calls[0].params == {"symbols": '["BTCEUR","ETHEUR"]'}
    assert {c.params["symbol"] for c in fetcher.calls_to("/klines")} == {"BTCEUR", "ETHEUR"}
    assert fetcher.calls_to("/klines")[0].params["interval"] == "30m"
    assert fetcher.calls_to("/klines")[0].params["limit"] == "48"


async def test_in_dollars_binance_quotes_the_tether_pair():
    fetcher = FakeFetcher()
    fetcher.on("/ticker/24hr", json_response(sample("binance_ticker_batch")))
    fetcher.on("/klines", json_response([]))
    provider = binance.BinanceProvider(fetcher, gate=instant_gate())
    results = await provider.quotes([BTC], "USD")
    assert fetcher.calls[0].params == {"symbols": '["BTCUSDT"]'}
    assert isinstance(results["crypto:BTC"], NotFound)  # la réponse jouée ne contient pas BTCUSDT
    assert binance.pair_of(BTC, "USD") == "BTCUSDT"
    assert binance.pair_of(BTC, "EUR") == "BTCEUR"


async def test_the_sparkline_is_asked_again_only_after_five_minutes():
    fetcher = FakeFetcher()
    fetcher.on("/ticker/24hr", json_response(sample("binance_ticker_batch")))
    fetcher.on("/klines", json_response(sample("binance_klines_30m")))
    clock = [1000.0]
    provider = binance.BinanceProvider(fetcher, gate=instant_gate(), monotonic=lambda: clock[0])
    await provider.quotes([BTC], "EUR")
    clock[0] += 120
    again = await provider.quotes([BTC], "EUR")
    assert len(fetcher.calls_to("/klines")) == 1
    assert len(again["crypto:BTC"].spark) == 48
    clock[0] += 400
    await provider.quotes([BTC], "EUR")
    assert len(fetcher.calls_to("/klines")) == 2


async def test_a_failing_sparkline_never_spoils_the_quote():
    fetcher = FakeFetcher()
    fetcher.on("/ticker/24hr", json_response(sample("binance_ticker_batch")))
    fetcher.on("/klines", text_response("", status=500))
    provider = binance.BinanceProvider(fetcher, gate=instant_gate())
    quote = (await provider.quotes([BTC], "EUR"))["crypto:BTC"]
    assert quote.price == 61250.5
    assert quote.spark == ()


async def test_an_unknown_pair_is_isolated_by_asking_pair_by_pair():
    fetcher = FakeFetcher()
    fetcher.on(
        lambda c: c.params.get("symbols") is not None,
        json_response(sample("binance_invalid_symbol"), status=400),
    )
    fetcher.on(
        lambda c: c.params.get("symbol") == "SOLEUR" and "ticker" in c.url,
        json_response(sample("binance_ticker_single")),
    )
    fetcher.on(
        lambda c: c.params.get("symbol") == "ZZZEUR" and "ticker" in c.url,
        json_response(sample("binance_invalid_symbol"), status=400),
    )
    fetcher.on("/klines", json_response(sample("binance_klines_30m")))
    provider = binance.BinanceProvider(fetcher, gate=instant_gate())
    results = await provider.quotes([SOL, Instrument("crypto", "ZZZ")], "EUR")
    assert isinstance(results["crypto:SOL"], Quote)
    assert results["crypto:SOL"].price == 128.4
    assert isinstance(results["crypto:ZZZ"], NotFound)
    assert not fetcher.unexpected


async def test_a_pair_missing_from_the_reply_is_unknown():
    fetcher = FakeFetcher()
    fetcher.on("/ticker/24hr", json_response(sample("binance_ticker_batch")[:1]))
    fetcher.on("/klines", json_response(sample("binance_klines_30m")))
    results = await binance.BinanceProvider(fetcher, gate=instant_gate()).quotes([BTC, ETH], "EUR")
    assert isinstance(results["crypto:BTC"], Quote)
    assert isinstance(results["crypto:ETH"], NotFound)


async def test_a_restricted_country_falls_back_to_the_market_data_mirror_and_remembers_it():
    fetcher = FakeFetcher()
    fetcher.on("https://api.binance.com", json_response(sample("binance_restricted"), status=451))
    fetcher.on(
        "https://data-api.binance.vision/api/v3/ticker",
        json_response(sample("binance_ticker_batch")),
    )
    fetcher.on(
        "https://data-api.binance.vision/api/v3/klines", json_response(sample("binance_klines_30m"))
    )
    provider = binance.BinanceProvider(
        fetcher, gate=instant_gate(), monotonic=iter(range(10_000)).__next__
    )
    assert isinstance((await provider.quotes([BTC], "EUR"))["crypto:BTC"], Quote)
    assert fetcher.calls[0].url.startswith("https://api.binance.com")
    before = len(fetcher.calls_to("api.binance.com"))
    await provider.quotes([BTC], "EUR")
    assert (
        len(fetcher.calls_to("api.binance.com")) == before
    )  # le miroir est maintenant le premier choix


async def test_a_network_failure_on_the_main_host_also_falls_back():
    fetcher = FakeFetcher()
    fetcher.on("https://api.binance.com", NetworkError("api.binance.com ne répond pas"))
    fetcher.on(
        "data-api.binance.vision/api/v3/ticker", json_response(sample("binance_ticker_batch"))
    )
    fetcher.on("data-api.binance.vision/api/v3/klines", json_response([]))
    provider = binance.BinanceProvider(fetcher, gate=instant_gate())
    assert isinstance((await provider.quotes([BTC], "EUR"))["crypto:BTC"], Quote)


async def test_when_every_server_refuses_the_country_the_reason_is_reported():
    fetcher = FakeFetcher().on("binance", json_response(sample("binance_restricted"), status=451))
    provider = binance.BinanceProvider(fetcher, gate=instant_gate())
    with pytest.raises(Blocked, match="pays"):
        await provider.quotes([BTC], "EUR")
    assert len(fetcher.calls) == 2  # les deux serveurs ont été essayés


async def test_when_no_server_answers_the_first_network_error_is_reported():
    fetcher = FakeFetcher().on("binance", NetworkError("pas de réponse"))
    with pytest.raises(NetworkError, match="pas de réponse"):
        await binance.BinanceProvider(fetcher, gate=instant_gate()).quotes([BTC], "EUR")


async def test_binance_rate_limits_are_reported_with_their_delay():
    fetcher = FakeFetcher().on(
        "binance", text_response("", status=429, headers={"Retry-After": "60"})
    )
    with pytest.raises(RateLimited) as caught:
        await binance.BinanceProvider(fetcher, gate=instant_gate()).quotes([BTC], "EUR")
    assert caught.value.retry_after == 60


async def test_garbage_from_binance_is_a_bad_response():
    fetcher = FakeFetcher().on("/ticker/24hr", json_response({"pas": "une liste"}))
    with pytest.raises(BadResponse):
        await binance.BinanceProvider(fetcher, gate=instant_gate()).quotes([BTC], "EUR")


def test_binance_klines_skip_malformed_rows():
    rows = [
        [1793283600000, "1", "2", "0.5", "61000.5", "3"],
        [1793285400000, "?", "?", "?", "oups", "?"],
        "x",
        [1],
    ]
    points = binance.parse_klines(rows)
    assert [(p.at, p.close) for p in points] == [(1793283600, 61000.5)]
    with pytest.raises(BadResponse):
        binance.parse_klines({"code": -1})


@pytest.mark.parametrize(
    ("range_", "interval", "limit"),
    [("1d", "5m", "288"), ("5d", "30m", "240"), ("1mo", "4h", "180"), ("1y", "1d", "365")],
)
async def test_binance_history_asks_the_right_candles(range_, interval, limit):
    fetcher = FakeFetcher().on("/klines", json_response(sample("binance_klines_30m")))
    points = await binance.BinanceProvider(fetcher, gate=instant_gate()).history(BTC, range_, "EUR")
    assert fetcher.calls[0].params == {"symbol": "BTCEUR", "interval": interval, "limit": limit}
    assert len(points) == 48
    assert points[0].at < points[-1].at


async def test_binance_does_not_quote_stocks():
    provider = binance.BinanceProvider(FakeFetcher(), gate=instant_gate())
    assert provider.supports(BTC)
    assert not provider.supports(AAPL)
    with pytest.raises(Unsupported):
        await provider.history(AAPL, "1d", "EUR")


# --- Finnhub -----------------------------------------------------------------------------------


async def test_finnhub_quotes_a_stock_with_the_key_in_a_header_never_in_the_address():
    fetcher = FakeFetcher().on("finnhub.io/api/v1/quote", json_response(sample("finnhub_quote")))
    provider = finnhub.FinnhubProvider(fetcher, "cle-secrete-123", gate=instant_gate())
    quote = (await provider.quotes([Instrument("stock", "MSTR")], "EUR"))["stock:MSTR"]
    assert (quote.price, quote.currency, quote.source) == (182.4, "USD", "finnhub")
    assert (quote.change, quote.change_pct) == (3.15, 1.7576)
    assert (quote.high, quote.low, quote.traded_at) == (184.2, 178.9, 1793369900)
    assert quote.name is None
    assert quote.spark == ()
    call = fetcher.calls[0]
    assert call.headers == {"X-Finnhub-Token": "cle-secrete-123"}
    assert "cle-secrete-123" not in call.full_url
    assert call.params == {"symbol": "MSTR"}


async def test_finnhub_unknown_symbols_come_back_as_zeros_and_are_reported_unknown():
    fetcher = FakeFetcher().on("finnhub.io", json_response(sample("finnhub_unknown")))
    results = await finnhub.FinnhubProvider(fetcher, "k", gate=instant_gate()).quotes([AAPL], "EUR")
    assert isinstance(results["stock:AAPL"], NotFound)


@pytest.mark.parametrize(
    ("status", "error"), [(401, Blocked), (403, Blocked), (429, RateLimited), (500, Unavailable)]
)
async def test_finnhub_failures_are_reported(status, error):
    fetcher = FakeFetcher().on("finnhub.io", text_response("", status=status))
    with pytest.raises(error):
        await finnhub.FinnhubProvider(fetcher, "k", gate=instant_gate()).quotes([AAPL], "EUR")


async def test_finnhub_only_covers_stocks_and_has_no_history():
    provider = finnhub.FinnhubProvider(FakeFetcher(), "k", gate=instant_gate())
    assert provider.supports(AAPL)
    assert not provider.supports(BTC)
    with pytest.raises(Unsupported):
        await provider.history(AAPL, "1mo", "EUR")


def test_finnhub_derives_the_change_when_it_is_not_given():
    quote = finnhub.parse_quote({"c": 110.0, "pc": 100.0, "t": 1793369900})
    assert quote.change == 10.0
    assert quote.change_pct == 10.0
    with pytest.raises(BadResponse):
        finnhub.parse_quote([])


# --- démonstration -------------------------------------------------------------------------------


async def test_the_demo_gives_the_same_quote_for_the_same_instant():
    one = await demo.DemoProvider(now=lambda: NOW).quotes([BTC, AAPL], "EUR")
    two = await demo.DemoProvider(now=lambda: NOW).quotes([BTC, AAPL], "EUR")
    assert one == two
    btc = one["crypto:BTC"]
    assert (btc.source, btc.currency, btc.name) == ("demo", "EUR", "Bitcoin")
    assert len(btc.spark) == 48
    assert btc.low <= btc.price * 1.1
    assert btc.high >= btc.price * 0.9
    assert btc.change_pct == pytest.approx(btc.change / (btc.price - btc.change) * 100, rel=1e-3)
    assert one["stock:AAPL"].currency == "USD"
    assert one["stock:AAPL"].state == "open"


async def test_the_demo_moves_with_time_and_follows_the_chosen_currency():
    clock = [NOW]
    provider = demo.DemoProvider(now=lambda: clock[0])
    first = (await provider.quotes([BTC], "EUR"))["crypto:BTC"]
    clock[0] += 3600
    later = (await provider.quotes([BTC], "EUR"))["crypto:BTC"]
    assert first.price != later.price
    dollars = (await provider.quotes([BTC], "USD"))["crypto:BTC"]
    assert dollars.currency == "USD"
    assert dollars.price == pytest.approx(later.price * demo.USD_PER_EUR, rel=1e-6)


async def test_the_demo_has_a_closed_market_and_refuses_unknown_symbols():
    results = await demo.DemoProvider(now=lambda: NOW).quotes(
        [FCHI, Instrument("stock", "NOPE")], "EUR"
    )
    assert results["stock:^FCHI"].state == "closed"
    assert isinstance(results["stock:NOPE"], NotFound)
    with pytest.raises(NotFound):
        await demo.DemoProvider(now=lambda: NOW).history(Instrument("stock", "NOPE"), "1d", "EUR")


@pytest.mark.parametrize(("range_", "count"), [("1d", 96), ("5d", 120), ("1mo", 120), ("1y", 180)])
async def test_the_demo_history_has_a_point_count_per_range_oldest_first(range_, count):
    points = await demo.DemoProvider(now=lambda: NOW).history(BTC, range_, "EUR")
    assert len(points) == count
    assert points[-1].at == NOW
    assert [p.at for p in points] == sorted(p.at for p in points)


async def test_a_closed_market_shows_its_last_session_as_a_real_curve():
    provider = demo.DemoProvider(now=lambda: NOW)
    quote = (await provider.quotes([FCHI], "EUR"))["stock:^FCHI"]
    assert len(set(quote.spark)) > 40  # une courbe, pas un palier jusqu'à minuit
    assert quote.traded_at < NOW
    assert quote.spark[-1] == pytest.approx(quote.price, abs=1e-5)
    points = await provider.history(FCHI, "1d", "EUR")
    assert (points[-1].at, points[-1].close) == (int(quote.traded_at), quote.price)


# --- recherche -------------------------------------------------------------------------------------


def test_yahoo_search_keeps_what_can_be_followed_for_a_stock():
    found = yahoo.parse_search(sample("yahoo_search_apple"), "stock")
    assert (
        [(c.symbol, c.name, c.exchange, c.label) for c in found]
        == [
            ("AAPL", "Apple Inc.", "NASDAQ", "Action"),
            ("APC.DE", "Apple Inc.", "XETRA", "Action"),
            ("APLE", "Apple Hospitality REIT, Inc.", "NYSE", "Action"),
            ("APLY", "YieldMax AAPL Option Income Strategy ETF", "NYSEArca", "ETF"),
        ]
    )  # ni l'option, ni le doublon de AAPL à Mexico, ni l'entrée sans symbole ou au symbole invalide


def test_yahoo_search_turns_crypto_pairs_into_one_coin_each():
    found = yahoo.parse_search(sample("yahoo_search_bitcoin"), "crypto")
    assert [(c.symbol, c.name, c.exchange, c.label) for c in found] == [
        ("BTC", "Bitcoin", None, "Crypto"),  # BTC-USD et BTC-EUR ne font qu'une crypto
        ("BCH", "Bitcoin Cash", None, "Crypto"),
        (
            "STRK",
            "Strike",
            None,
            "Crypto",
        ),  # une crypto inconnue du dock : « USD » est retiré du nom
    ]
    stocks = yahoo.parse_search(sample("yahoo_search_bitcoin"), "stock")
    assert [c.symbol for c in stocks] == ["IBIT", "MSTR"]  # l'ETF et l'action, pas les cryptos


def test_yahoo_search_caps_its_results_and_refuses_garbage():
    many = {
        "quotes": [
            {"symbol": f"S{i}", "quoteType": "EQUITY", "shortname": f"S{i}"} for i in range(30)
        ]
    }
    assert len(yahoo.parse_search(many, "stock")) == 8
    assert yahoo.parse_search(sample("yahoo_search_empty"), "stock") == []
    for odd in ([], "x", {"quotes": "x"}, {}):
        with pytest.raises(BadResponse):
            yahoo.parse_search(odd, "stock")


async def test_yahoo_search_asks_with_a_browser_identity_and_the_typed_text():
    fetcher = FakeFetcher().on("/v1/finance/search", json_response(sample("yahoo_search_apple")))
    provider = yahoo.YahooProvider(fetcher, gate=instant_gate(), now=lambda: NOW)
    found = await provider.search("stock", "apple inc & co?")
    assert [c.symbol for c in found][:1] == ["AAPL"]
    call = fetcher.calls[0]
    assert call.url == "https://query2.finance.yahoo.com/v1/finance/search"
    assert (
        call.params["q"] == "apple inc & co?"
    )  # échappé par l'accès HTTP, pas collé dans l'adresse
    assert call.params["newsCount"] == "0"
    assert call.headers["User-Agent"].startswith("Mozilla/5.0")
    assert "apple" not in call.url


async def test_yahoo_search_failures_are_reported():
    fetcher = FakeFetcher().on("/search", text_response("Too Many Requests", status=429))
    provider = yahoo.YahooProvider(fetcher, gate=instant_gate(), now=lambda: NOW)
    with pytest.raises(RateLimited):
        await provider.search("stock", "apple")


async def test_the_demo_searches_its_catalog_by_name_or_symbol_without_accents():
    provider = demo.DemoProvider(now=lambda: NOW)
    assert [c.symbol for c in await provider.search("stock", "strategy")] == ["MSTR"]
    assert [c.symbol for c in await provider.search("stock", "cac")] == ["^FCHI"]
    assert [c.symbol for c in await provider.search("stock", "aapl")] == ["AAPL"]
    assert [c.symbol for c in await provider.search("crypto", "sol")] == ["SOL"]
    assert await provider.search("crypto", "strategy") == []  # pas du bon type
    assert await provider.search("stock", "zzz") == []


async def test_the_demo_has_one_value_whose_last_trade_is_fifteen_minutes_old():
    results = await demo.DemoProvider(now=lambda: NOW).quotes(
        [AAPL, Instrument("stock", "MSTR")], "EUR"
    )
    assert results["stock:AAPL"].traded_at == NOW - 900
    assert results["stock:MSTR"].traded_at == NOW
