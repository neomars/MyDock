import pytest

from mydock_dock.markets.model import (
    MAX_WATCHLIST,
    Instrument,
    crypto_name,
    downsample,
    normalize_symbol,
    parse_currency,
    parse_instrument,
    parse_watchlist,
    rounded,
)
from mydock_dock.validation import ValidationFailure


@pytest.mark.parametrize(
    ("kind", "typed", "symbol"),
    [
        ("crypto", "btc", "BTC"),
        ("crypto", "  eth ", "ETH"),
        ("crypto", "BTC-EUR", "BTC"),  # la devise se choisit à part
        ("crypto", "1inch", "1INCH"),
        ("stock", "mstr", "MSTR"),
        ("stock", "^fchi", "^FCHI"),
        ("stock", "cw8.pa", "CW8.PA"),
        ("stock", "eurusd=x", "EURUSD=X"),
        ("stock", "brk-b", "BRK-B"),
        ("stock", "BTC-USD", "BTC-USD"),
    ],
)
def test_symbols_are_normalized(kind, typed, symbol):
    assert normalize_symbol(kind, typed) == symbol


@pytest.mark.parametrize(
    ("kind", "typed"),
    [
        ("crypto", ""),
        ("crypto", "   "),
        ("crypto", "B"),
        ("crypto", "BITCOINCASHXXX"),
        ("crypto", "BTC USD"),
        ("crypto", "BTC/EUR"),
        ("crypto", "ÉTH"),
        ("stock", "../etc/passwd"),
        ("stock", "AAPL?x=1"),
        ("stock", "AAPL;ls"),
        ("stock", "a" * 21),
        ("stock", ".AAPL"),
        ("stock", "AA PL"),
        ("stock", "http://exemple.fr"),
        ("stock", 12),
        ("stock", None),
        ("obligation", "AAPL"),
    ],
)
def test_dangerous_or_malformed_symbols_are_refused(kind, typed):
    with pytest.raises(ValidationFailure):
        normalize_symbol(kind, typed)


def test_an_instrument_has_a_stable_id_and_a_serializable_form():
    inst = parse_instrument({"kind": "stock", "symbol": "cw8.pa"})
    assert inst == Instrument("stock", "CW8.PA")
    assert inst.id == "stock:CW8.PA"
    assert inst.to_dict() == {"kind": "stock", "symbol": "CW8.PA"}


@pytest.mark.parametrize(
    "raw",
    [
        "BTC",
        None,
        [],
        {"kind": "crypto"},
        {"symbol": "BTC"},
        {"kind": "crypto", "symbol": "BTC", "url": "http://x"},
        {"kind": 3, "symbol": "BTC"},
    ],
)
def test_malformed_watchlist_entries_are_refused(raw):
    with pytest.raises(ValidationFailure):
        parse_instrument(raw)


def test_a_watchlist_keeps_its_order_and_refuses_duplicates_and_excess():
    raw = [
        {"kind": "stock", "symbol": "MSTR"},
        {"kind": "crypto", "symbol": "BTC"},
        {"kind": "stock", "symbol": "BTC"},  # même symbole, autre type : permis
    ]
    assert [i.id for i in parse_watchlist(raw)] == ["stock:MSTR", "crypto:BTC", "stock:BTC"]
    with pytest.raises(ValidationFailure, match="deux fois"):
        parse_watchlist([raw[0], raw[0]])
    with pytest.raises(ValidationFailure, match="invalide"):
        parse_watchlist("BTC")
    too_many = [{"kind": "stock", "symbol": f"S{i}"} for i in range(MAX_WATCHLIST + 1)]
    with pytest.raises(ValidationFailure):
        parse_watchlist(too_many)
    assert len(parse_watchlist(too_many[:MAX_WATCHLIST])) == MAX_WATCHLIST


def test_currencies_are_euro_or_dollar_only():
    assert parse_currency("EUR") == "EUR"
    assert parse_currency("USD") == "USD"
    for bad in ("eur", "GBP", None, 1, ""):
        with pytest.raises(ValidationFailure):
            parse_currency(bad)


def test_crypto_names_fall_back_to_the_symbol():
    assert crypto_name("BTC") == "Bitcoin"
    assert crypto_name("ZZZZ") == "ZZZZ"


def test_downsampling_keeps_both_ends_and_the_order():
    values = list(range(1000))
    sampled = downsample(values, 48)
    assert len(sampled) == 48
    assert sampled[0] == 0
    assert sampled[-1] == 999
    assert sampled == sorted(sampled)
    assert downsample([1, 2, 3], 48) == [1, 2, 3]
    assert downsample(values, 1) == values  # une limite absurde ne détruit rien


def test_rounded_hides_binary_noise():
    assert rounded(0.1 + 0.2) == 0.3
    assert rounded(None) is None
    assert rounded(1.23456789, 4) == 1.2346
