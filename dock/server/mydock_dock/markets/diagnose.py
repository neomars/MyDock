"""« Tester la connexion » de la Bourse : ce que le Pi voit vraiment, serveur par serveur, puis chaque fournisseur.

Quand les cotations n'arrivent pas, le message de l'écran dit seulement « connexion impossible ». Ce test sépare les
causes qu'on rencontre : l'heure du Pi fausse (les connexions sécurisées sont alors refusées), un nom introuvable (pas
de DNS), un IPv6 annoncé mais muet, un serveur qui répond mais refuse la demande.
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from mydock_dock import net
from mydock_dock.markets.errors import ProviderError
from mydock_dock.markets.model import Instrument, Quote
from mydock_dock.markets.providers.base import QuoteProvider

#: La valeur qu'on demande à chaque fournisseur pour voir s'il répond.
PROBES = {
    "binance": Instrument("crypto", "BTC"),
    "yahoo": Instrument("stock", "AAPL"),
    "finnhub": Instrument("stock", "AAPL"),
}
#: Les serveurs que chaque fournisseur contacte.
HOSTS: dict[str, tuple[str, ...]] = {
    "binance": ("api.binance.com", "data-api.binance.vision"),
    "yahoo": ("query2.finance.yahoo.com",),
    "finnhub": ("finnhub.io",),
}
MIN_YEAR = 2024  # avant : l'heure du Pi est celle de sa fabrication, pas la vraie

Resolver = Callable[[str], Awaitable[list[Any]]]
Connector = Callable[[Any], Awaitable[float]]
TlsCheck = Callable[[str], Awaitable[tuple[bool, str]]]


async def _resolve(host: str) -> list[Any]:
    return await asyncio.to_thread(socket.getaddrinfo, host, 443, 0, socket.SOCK_STREAM)


async def _connect(info: Any) -> float:
    """Le temps d'une connexion TCP vers cette adresse ; lève OSError ou TimeoutError."""

    def attempt() -> float:
        started = time.monotonic()
        sock = socket.socket(info[0], info[1], info[2])
        try:
            sock.settimeout(net.ATTEMPT_TIMEOUT)
            sock.connect(info[4])
        finally:
            sock.close()
        return time.monotonic() - started

    return await asyncio.to_thread(attempt)


async def _tls(host: str) -> tuple[bool, str]:
    """La poignée de main TLS vérifiée avec les autorités du Pi (c'est elle que refuse un « certificat non reconnu »)."""
    return await asyncio.to_thread(net.check_tls, host)


def _seconds(value: float) -> str:
    return f"{value:.2f} s".replace(".", ",")


def clock_check(now: float) -> dict[str, Any]:
    moment = time.gmtime(now)
    text = time.strftime("%d/%m/%Y %H:%M UTC", moment)
    if moment.tm_year < MIN_YEAR:
        return {
            "label": "Heure du Pi",
            "ok": False,
            "detail": f"{text} : l'heure est fausse, les connexions sécurisées sont refusées (sudo timedatectl).",
        }
    return {"label": "Heure du Pi", "ok": True, "detail": text}


async def host_check(
    host: str, resolve: Resolver, connect: Connector, tls: TlsCheck = _tls
) -> dict[str, Any]:
    try:
        infos = await resolve(host)
    except OSError:
        return {
            "label": host,
            "ok": False,
            "detail": "nom introuvable : le Pi n'a pas de DNS ou pas d'accès à Internet",
        }
    parts: list[str] = []
    reached = False
    for name, group in (
        ("IPv4", [i for i in infos if i[0] == socket.AF_INET]),
        ("IPv6", [i for i in infos if i[0] != socket.AF_INET]),
    ):
        if not group:
            parts.append(f"{name} : aucune adresse")
            continue
        try:
            parts.append(f"{name} {_seconds(await connect(group[0]))}")
            reached = True
        except TimeoutError:
            parts.append(f"{name} : pas de réponse en {net.ATTEMPT_TIMEOUT:g} s")
        except OSError as exc:
            parts.append(f"{name} : {exc.strerror or 'refusée'}")
    secure = True
    if reached:
        secure, text = await tls(host)
        parts.append(f"TLS : {text}")
    return {"label": host, "ok": reached and secure, "detail": " · ".join(parts)}


async def provider_check(provider: QuoteProvider, currency: str, timeout: float) -> dict[str, Any]:
    instrument = PROBES.get(provider.id, Instrument("crypto", "BTC"))
    started = time.monotonic()
    try:
        async with asyncio.timeout(timeout):
            results = await provider.quotes([instrument], currency)
        outcome = results[instrument.id]
    except ProviderError as exc:
        return {"label": provider.label, "ok": False, "detail": exc.message}
    except TimeoutError:
        return {"label": provider.label, "ok": False, "detail": "ne répond pas (délai dépassé)"}
    except Exception as exc:  # un fournisseur qui plante ne doit pas cacher les autres résultats
        return {
            "label": provider.label,
            "ok": False,
            "detail": f"erreur inattendue ({type(exc).__name__})",
        }
    elapsed = _seconds(time.monotonic() - started)
    if isinstance(outcome, Quote):
        price = f"{outcome.price:g}" + (f" {outcome.currency}" if outcome.currency else "")
        return {
            "label": provider.label,
            "ok": True,
            "detail": f"{instrument.symbol} : {price} en {elapsed}",
        }
    return {"label": provider.label, "ok": False, "detail": outcome.message}


def verdict(
    checks: list[dict[str, Any]], hosts: list[dict[str, Any]], providers: list[dict[str, Any]]
) -> str | None:
    """Une phrase qui dit quoi faire, d'après ce que le test a vu."""
    if not checks[0]["ok"]:
        return "Corrige d'abord l'heure du Pi : tant qu'elle est fausse, aucune connexion sécurisée n'aboutit."
    if any("TLS : certificat refusé" in h["detail"] for h in hosts):
        return (
            "Le Pi refuse le certificat de ces serveurs : vérifie l'heure du Pi, puis "
            "`sudo apt install --reinstall ca-certificates`. Un routeur ou un filtre qui remplace les certificats "
            "(contrôle parental, pare-feu) produit le même refus."
        )
    if hosts and not any(h["ok"] for h in hosts):
        return "Le Pi n'atteint aucun serveur : vérifie son accès à Internet (câble ou Wi-Fi, DNS de la box)."
    if providers and not any(p["ok"] for p in providers):
        return "Les serveurs répondent mais refusent les demandes : lis le message de chaque fournisseur."
    if any("IPv6" in h["detail"] and "pas de réponse" in h["detail"] for h in hosts):
        return "L'IPv6 de ton réseau ne répond pas : le dock passe par l'IPv4, ça marche malgré ce message."
    return None


async def diagnose(
    providers: Sequence[QuoteProvider],
    currency: str,
    *,
    timeout: float = 8.0,
    now: Callable[[], float] = time.time,
    resolve: Resolver = _resolve,
    connect: Connector = _connect,
    tls: TlsCheck = _tls,
) -> dict[str, Any]:
    names: list[str] = []
    for provider in providers:
        for host in HOSTS.get(provider.id, ()):
            if host not in names:
                names.append(host)
    clock = clock_check(now())
    hosts, results = await asyncio.gather(
        asyncio.gather(*(host_check(h, resolve, connect, tls) for h in names)),
        asyncio.gather(*(provider_check(p, currency, timeout) for p in providers)),
    )
    hosts, results = list(hosts), list(results)
    checks = [clock, *hosts, *results]
    return {"checks": checks, "verdict": verdict(checks, hosts, results)}
