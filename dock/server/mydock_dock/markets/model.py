"""Ce que le dock suit en bourse : des instruments, leurs cotations, leurs historiques.

Un instrument est soit une **crypto** (« BTC » : le fournisseur la cote dans la devise choisie, euro
ou dollar), soit une **valeur** au sens de Yahoo Finance (« MSTR », « ^FCHI », « CW8.PA »,
« EURUSD=X »). Rien de ce qui vient de l'écran n'atteint un fournisseur sans être passé par
`parse_instrument` : un symbole est de courtes lettres, chiffres et quelques signes, jamais une
adresse ni une option.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from mydock_dock.validation import ValidationFailure

KINDS = ("crypto", "stock")
CURRENCIES = ("EUR", "USD")
#: Secondes entre deux mises à jour d'une crypto ; les actions suivent au double (30 s au plus vif).
REFRESH_CHOICES = (15, 30, 60, 300)
DEFAULT_REFRESH = 15
RANGES = ("1d", "5d", "1mo", "1y")
MAX_WATCHLIST = 20
SPARK_POINTS = 48
MAX_HISTORY_POINTS = 240

_SYMBOL_RE = {
    "crypto": re.compile(r"^[A-Z0-9]{2,12}$"),
    "stock": re.compile(r"^[A-Z0-9^][A-Z0-9.=^_-]{0,19}$"),
}

RANGE_LABELS = {"1d": "1 jour", "5d": "5 jours", "1mo": "1 mois", "1y": "1 an"}

CRYPTO_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "BNB": "BNB",
    "XRP": "XRP",
    "ADA": "Cardano",
    "DOGE": "Dogecoin",
    "LTC": "Litecoin",
    "DOT": "Polkadot",
    "AVAX": "Avalanche",
    "LINK": "Chainlink",
    "POL": "Polygon",
    "MATIC": "Polygon",
    "TRX": "TRON",
    "ATOM": "Cosmos",
    "XMR": "Monero",
    "XLM": "Stellar",
    "BCH": "Bitcoin Cash",
    "ETC": "Ethereum Classic",
    "UNI": "Uniswap",
    "NEAR": "NEAR",
    "ALGO": "Algorand",
    "FIL": "Filecoin",
    "APT": "Aptos",
    "ARB": "Arbitrum",
    "OP": "Optimism",
    "SUI": "Sui",
    "TON": "Toncoin",
    "SHIB": "Shiba Inu",
    "PEPE": "Pepe",
    "AAVE": "Aave",
    "USDT": "Tether",
    "USDC": "USD Coin",
}


@dataclass(frozen=True)
class Instrument:
    kind: str
    symbol: str

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.symbol}"

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "symbol": self.symbol}


@dataclass(frozen=True)
class Quote:
    """Une cotation. Les champs que le fournisseur ne donne pas restent à None (jamais inventés)."""

    price: float
    currency: str | None
    source: str
    name: str | None = None
    change: float | None = None  # sur 24 h pour une crypto, depuis la clôture de la veille sinon
    change_pct: float | None = None
    high: float | None = None
    low: float | None = None
    state: str | None = None  # « open », « closed », « pre » ou « post » (valeurs seulement)
    traded_at: float | None = None  # heure de la dernière transaction (secondes Unix)
    spark: tuple[float, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class HistoryPoint:
    at: int  # secondes Unix
    close: float

    def to_list(self) -> list[float]:
        return [self.at, self.close]


def normalize_symbol(kind: str, value: object) -> str:
    if kind not in KINDS:
        raise ValidationFailure("Type de valeur inconnu : « crypto » ou « action » attendu.")
    if not isinstance(value, str):
        raise ValidationFailure("Le symbole doit être un texte.")
    symbol = value.strip().upper()
    if kind == "crypto" and "-" in symbol:
        symbol = symbol.split("-", 1)[
            0
        ]  # « BTC-EUR » : on garde la crypto, la devise est choisie à part
    if not symbol:
        raise ValidationFailure("Saisis un symbole (par exemple BTC ou MSTR).")
    if not _SYMBOL_RE[kind].fullmatch(symbol):
        example = "BTC ou ETH" if kind == "crypto" else "MSTR, CW8.PA ou ^FCHI"
        raise ValidationFailure(
            f"« {value.strip()} » n'est pas un symbole valide (exemples : {example})."
        )
    return symbol


def parse_instrument(raw: object) -> Instrument:
    """Un élément de la liste de suivi, tel que l'écran ou le fichier d'état le donne."""
    if not isinstance(raw, Mapping) or set(raw) - {"kind", "symbol"}:
        raise ValidationFailure("Une valeur suivie est un type et un symbole.")
    kind = raw.get("kind")
    if not isinstance(kind, str):
        raise ValidationFailure("Type de valeur inconnu : « crypto » ou « action » attendu.")
    return Instrument(kind, normalize_symbol(kind, raw.get("symbol")))


def parse_watchlist(raw: object) -> list[Instrument]:
    if not isinstance(raw, list) or len(raw) > MAX_WATCHLIST:
        raise ValidationFailure(
            f"La liste de suivi est invalide (au plus {MAX_WATCHLIST} valeurs)."
        )
    items = [parse_instrument(entry) for entry in raw]
    if len({item.id for item in items}) != len(items):
        raise ValidationFailure("La liste de suivi contient deux fois la même valeur.")
    return items


def parse_currency(value: object) -> str:
    if value not in CURRENCIES:
        raise ValidationFailure("Devise inconnue : EUR ou USD.")
    return str(value)


def parse_refresh(value: object) -> int:
    if isinstance(value, bool) or value not in REFRESH_CHOICES:
        raise ValidationFailure("Fréquence inconnue : 15 s, 30 s, 1 min ou 5 min.")
    return int(value)  # type: ignore[call-overload]


@dataclass(frozen=True)
class Candidate:
    """Un résultat de recherche : une valeur qu'on peut proposer d'ajouter."""

    kind: str
    symbol: str
    name: str
    exchange: str | None = None
    label: str | None = None  # « Action », « ETF », « Indice »…

    def to_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "name": self.name,
            "exchange": self.exchange,
            "label": self.label,
        }


def crypto_name(symbol: str) -> str:
    return CRYPTO_NAMES.get(symbol, symbol)


def fold(text: str) -> str:
    """Minuscules sans accents : « Éthereum » et « ethereum » se valent."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def search_crypto_names(query: str) -> list[Candidate]:
    """Les cryptos connues du dock dont le symbole ou le nom commence par la recherche (sans réseau)."""
    wanted = fold(query.strip())
    hits: list[Candidate] = []
    for symbol, name in CRYPTO_NAMES.items():
        if symbol == "MATIC":
            continue  # « POL » le remplace
        if fold(symbol).startswith(wanted) or any(fold(w).startswith(wanted) for w in name.split()):
            hits.append(Candidate("crypto", symbol, name, None, "Crypto"))
    hits.sort(key=lambda c: (fold(c.symbol) != wanted, fold(c.name) != wanted))
    return hits


def downsample(values: list[Any], limit: int) -> list[Any]:
    """Au plus `limit` éléments, le premier et le dernier toujours conservés, régulièrement espacés."""
    count = len(values)
    if count <= limit or limit < 2:
        return list(values)
    step = (count - 1) / (limit - 1)
    return [values[round(i * step)] for i in range(limit)]


def rounded(value: float | None, digits: int = 6) -> float | None:
    """Le prix tel qu'on l'affiche : 6 décimales au plus (les bruits binaires n'ont rien à faire à l'écran)."""
    return None if value is None else round(value, digits)
