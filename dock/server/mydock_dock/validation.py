"""Validation des valeurs saisies à l'écran (adresse du PC, port, adresse MAC)."""

from __future__ import annotations

import ipaddress
import re

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOST_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")
_MAC_RE = re.compile(r"^[0-9a-fA-F]{2}([:-])(?:[0-9a-fA-F]{2}\1){4}[0-9a-fA-F]{2}$")
_BARE_MAC_RE = re.compile(r"^[0-9a-fA-F]{12}$")


class ValidationFailure(ValueError):
    """Valeur refusée ; le message est écrit pour être lu à l'écran."""


def validate_host(value: object) -> str:
    """Nom d'hôte (ex. ubuntu.local) ou adresse IPv4. Pas de schéma, de port ni d'IPv6."""
    if not isinstance(value, str):
        raise ValidationFailure("L'adresse du PC doit être un texte.")
    host = value.strip().lower()
    if host.endswith("."):  # « ubuntu.local. » (forme complète) : un seul point final est toléré
        host = host[:-1]
    if not host:
        raise ValidationFailure("L'adresse du PC est vide.")
    if ":" in host or "/" in host or " " in host:
        raise ValidationFailure(
            "Saisis seulement le nom ou l'adresse IPv4 du PC (sans http://, sans port, sans IPv6)."
        )
    if len(host) > 253 or not _HOST_RE.fullmatch(host):
        raise ValidationFailure(f"« {value} » n'est pas un nom d'hôte valide.")
    if all(label.isdigit() for label in host.split(".")):
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            raise ValidationFailure(f"« {value} » n'est pas une adresse IPv4 valide.") from None
    return host


def validate_port(value: object, label: str = "Le port") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValidationFailure(f"{label} doit être un nombre entre 1 et 65535.")
    return value


def normalize_mac(value: object) -> str:
    """Adresse MAC en minuscules séparées par « : » ; accepte « - » ou l'absence de séparateur."""
    if not isinstance(value, str):
        raise ValidationFailure("L'adresse MAC doit être un texte.")
    text = value.strip()
    if _BARE_MAC_RE.fullmatch(text):
        text = ":".join(text[i : i + 2] for i in range(0, 12, 2))
    elif _MAC_RE.fullmatch(text):
        text = text.replace("-", ":")
    else:
        raise ValidationFailure(
            "Adresse MAC invalide : attendu 6 paires hexadécimales, par exemple 02:00:5e:10:00:01."
        )
    mac = text.lower()
    if mac in {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}:
        raise ValidationFailure("Cette adresse MAC n'est pas utilisable.")
    return mac
