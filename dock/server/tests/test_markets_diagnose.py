"""« Tester la connexion » de la Bourse : chaque cause possible se lit dans le résultat."""

from __future__ import annotations

import socket

import pytest

from mydock_dock.markets import diagnose as dg
from mydock_dock.markets.errors import BadResponse, NetworkError
from mydock_dock.markets.model import Quote

GOOD_CLOCK = 1_794_000_000.0  # novembre 2026
V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))
V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0))


class FakeProvider:
    def __init__(self, id_, label, outcome):
        self.id, self.label, self._outcome = id_, label, outcome

    async def quotes(self, instruments, currency):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return {i.id: self._outcome for i in instruments}


QUOTE = Quote(price=85000.5, currency="EUR", source="binance")


def resolver(table):
    async def resolve(host):
        value = table.get(host, OSError("introuvable"))
        if isinstance(value, Exception):
            raise value
        return value

    return resolve


def connector(dead=()):
    async def connect(info):
        if info[4][0] in dead:
            raise TimeoutError
        return 0.0421

    return connect


async def run(providers, table, *, dead=(), now=GOOD_CLOCK):
    return await dg.diagnose(
        providers, "EUR", now=lambda: now, resolve=resolver(table), connect=connector(dead)
    )


async def test_everything_working_says_so_and_has_no_complaint():
    providers = [
        FakeProvider("binance", "Binance", QUOTE),
        FakeProvider("yahoo", "Yahoo Finance", QUOTE),
    ]
    table = {
        h: [V4, V6]
        for h in ("api.binance.com", "data-api.binance.vision", "query2.finance.yahoo.com")
    }
    result = await run(providers, table)
    assert result["verdict"] is None
    assert all(c["ok"] for c in result["checks"])
    labels = [c["label"] for c in result["checks"]]
    assert labels[0] == "Heure du Pi"
    assert "api.binance.com" in labels
    assert "Binance" in labels
    hosts = {c["label"]: c["detail"] for c in result["checks"]}
    assert hosts["api.binance.com"] == "IPv4 0,04 s · IPv6 0,04 s"
    assert "BTC : 85000.5 EUR" in hosts["Binance"]


async def test_a_wrong_clock_is_named_first():
    providers = [
        FakeProvider(
            "binance",
            "Binance",
            NetworkError("Connexion sécurisée à api.binance.com refusée (certificat)."),
        )
    ]
    result = await run(
        providers, {"api.binance.com": [V4], "data-api.binance.vision": [V4]}, now=0.0
    )
    assert result["checks"][0]["ok"] is False
    assert "heure est fausse" in result["checks"][0]["detail"]
    assert "heure du Pi" in result["verdict"]


async def test_no_dns_means_the_pi_has_no_internet():
    providers = [
        FakeProvider("binance", "Binance", NetworkError("Le nom api.binance.com est introuvable")),
        FakeProvider("yahoo", "Yahoo Finance", NetworkError("introuvable")),
    ]
    result = await run(providers, {})
    hosts = [
        c
        for c in result["checks"]
        if "." in c["label"] and c["label"] != "Heure du Pi" and not c["label"].startswith("Yahoo")
    ]
    assert hosts
    assert all(not h["ok"] and "DNS" in h["detail"] for h in hosts)
    assert "accès à Internet" in result["verdict"]


async def test_a_silent_ipv6_is_reported_but_ipv4_is_enough():
    providers = [FakeProvider("yahoo", "Yahoo Finance", QUOTE)]
    result = await run(providers, {"query2.finance.yahoo.com": [V6, V4]}, dead={"2001:db8::1"})
    host = next(c for c in result["checks"] if c["label"] == "query2.finance.yahoo.com")
    assert host["ok"] is True
    assert "IPv4 0,04 s" in host["detail"]
    assert "IPv6 : pas de réponse" in host["detail"]
    assert "IPv6" in result["verdict"]
    assert "passe par l'IPv4" in result["verdict"]


async def test_servers_that_answer_but_refuse_are_said_per_provider():
    providers = [
        FakeProvider(
            "binance",
            "Binance",
            BadResponse("HTTP 451 : Binance n'est pas accessible depuis ce pays."),
        ),
        FakeProvider("yahoo", "Yahoo Finance", BadResponse("HTTP 429")),
    ]
    table = {
        h: [V4] for h in ("api.binance.com", "data-api.binance.vision", "query2.finance.yahoo.com")
    }
    result = await run(providers, table)
    details = {c["label"]: c["detail"] for c in result["checks"]}
    assert "451" in details["Binance"]
    assert "429" in details["Yahoo Finance"]
    assert "refusent" in result["verdict"]


async def test_a_provider_that_crashes_does_not_hide_the_others():
    providers = [
        FakeProvider("binance", "Binance", RuntimeError("bug")),
        FakeProvider("yahoo", "Yahoo Finance", QUOTE),
    ]
    table = {
        h: [V4] for h in ("api.binance.com", "data-api.binance.vision", "query2.finance.yahoo.com")
    }
    result = await run(providers, table)
    details = {c["label"]: c for c in result["checks"]}
    assert details["Binance"]["ok"] is False
    assert "RuntimeError" in details["Binance"]["detail"]
    assert details["Yahoo Finance"]["ok"] is True


async def test_a_slow_provider_is_cut_at_the_timeout():
    import asyncio

    class Slow(FakeProvider):
        async def quotes(self, instruments, currency):
            await asyncio.sleep(5)

    result = await dg.diagnose(
        [Slow("yahoo", "Yahoo Finance", QUOTE)], "EUR", timeout=0.05, now=lambda: GOOD_CLOCK,
        resolve=resolver({"query2.finance.yahoo.com": [V4]}), connect=connector(),
    )  # fmt: skip
    assert (
        "délai dépassé"
        in next(c for c in result["checks"] if c["label"] == "Yahoo Finance")["detail"]
    )


async def test_the_demo_provider_has_no_host_and_still_works():
    result = await run([FakeProvider("demo", "Démonstration", QUOTE)], {})
    assert [c["label"] for c in result["checks"]] == ["Heure du Pi", "Démonstration"]


@pytest.mark.parametrize(("year", "ok"), [(2023, False), (2024, True), (2026, True)])
def test_the_clock_check_threshold(year, ok):
    import calendar

    assert dg.clock_check(calendar.timegm((year, 6, 1, 12, 0, 0)))["ok"] is ok
