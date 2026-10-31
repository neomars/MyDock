"""Assemble les fournisseurs de cotations selon la configuration."""

from __future__ import annotations

from mydock_dock.markets.http import Fetcher, UrllibFetcher
from mydock_dock.markets.providers.base import QuoteProvider
from mydock_dock.markets.providers.binance import BinanceProvider
from mydock_dock.markets.providers.demo import DemoProvider
from mydock_dock.markets.providers.finnhub import FinnhubProvider
from mydock_dock.markets.providers.yahoo import YahooProvider


def build_providers(
    *, demo: bool = False, finnhub_key: str = "", fetcher: Fetcher | None = None
) -> list[QuoteProvider]:
    """Dans l'ordre où ils sont proposés : Binance pour les cryptos, puis Yahoo Finance pour tout,
    puis Finnhub si une clé est configurée. Le mode démo n'a qu'un fournisseur, sans réseau."""
    if demo:
        return [DemoProvider()]
    http = fetcher or UrllibFetcher()
    providers: list[QuoteProvider] = [BinanceProvider(http), YahooProvider(http)]
    if finnhub_key:
        providers.append(FinnhubProvider(http, finnhub_key))
    return providers
