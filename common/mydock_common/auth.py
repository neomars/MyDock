"""Jeton partagé entre l'agent et le dock.

Le jeton est généré à l'installation (256 bits), stocké dans un fichier en droits 600
et présenté dans l'en-tête ``Authorization: Bearer <jeton>`` de la poignée de main
WebSocket. Il n'est jamais écrit dans les journaux ni envoyé au navigateur.
"""

from __future__ import annotations

import hmac
import re
import secrets

TOKEN_BYTES = 32
MIN_TOKEN_LENGTH = 24
MAX_TOKEN_LENGTH = 256
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]+$")


def generate_token() -> str:
    """Jeton aléatoire de 43 caractères (alphabet URL-safe)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def is_valid_token_format(token: object) -> bool:
    return (
        isinstance(token, str)
        and MIN_TOKEN_LENGTH <= len(token) <= MAX_TOKEN_LENGTH
        and _TOKEN_RE.fullmatch(token) is not None
    )


def tokens_equal(expected: str, presented: str | None) -> bool:
    """Comparaison à temps constant ; faux si rien n'est présenté."""
    if not expected or not presented:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), presented.encode("utf-8"))


def bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def parse_bearer(header_value: str | None) -> str | None:
    """Extrait le jeton d'un en-tête ``Authorization: Bearer ...``."""
    if not header_value:
        return None
    scheme, _, value = header_value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    value = value.strip()
    return value or None
