"""L'agent s'associe au dock : un code lu sur l'écran du Pi, le jeton de ce PC, une seule demande.

C'est ce qui évite de recopier un jeton de 43 caractères d'une machine à l'autre : `mydock-agent
setup --pi ADRESSE` demande le code affiché sur le dock, le présente au portail du Pi
(`POST /api/pair`) avec le jeton que cet agent a lui-même généré, et le dock se configure seul
(adresse du PC, port, jeton). Le code ne prouve qu'une chose : qu'on est devant le dock.

Rien ici n'a besoin d'une bibliothèque de plus : la bibliothèque standard parle HTTP, et aucune
variable de proxy n'est lue (le Pi est sur le réseau local).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

PORTAL_PORT = 80
STATE_TIMEOUT = 3.0
PAIR_TIMEOUT = 20.0  # le dock vérifie qu'il joint l'agent avant de répondre
MAX_ANSWER_BYTES = 16 * 1024


class PairingError(Exception):
    """L'association a échoué ; le message est celui du dock ou du réseau, déjà en français."""

    def __init__(self, code: str, message: str, *, pi_ips: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.pi_ips = pi_ips or []


@dataclass(frozen=True)
class Paired:
    pi_ips: list[str] = field(default_factory=list)
    dock_hostname: str = ""
    dock_version: str = ""


def portal_url(host: str, port: int, path: str = "/") -> str:
    suffix = "" if port == 80 else f":{port}"
    shown = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{shown}{suffix}{path}"


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _read_json(response: Any) -> dict[str, Any] | None:
    try:
        data = json.loads(response.read(MAX_ANSWER_BYTES))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def fetch_state(
    host: str, port: int = PORTAL_PORT, *, timeout: float = STATE_TIMEOUT
) -> dict | None:
    """L'état public du portail, ou None si rien de MyDock ne répond à cette adresse."""
    try:
        with _opener().open(portal_url(host, port, "/api/portal"), timeout=timeout) as response:
            data = _read_json(response)
    except (OSError, urllib.error.URLError, ValueError):
        return None
    return data if data is not None and data.get("app") == "mydock" else None


def pair_with_dock(
    host: str,
    port: int = PORTAL_PORT,
    *,
    code: str,
    token: str,
    agent_port: int,
    name: str,
    timeout: float = PAIR_TIMEOUT,
) -> Paired:
    """Présente le code au portail du dock. Lève `PairingError` avec ce que le dock en dit."""
    body = json.dumps({"code": code, "token": token, "port": agent_port, "name": name}).encode()
    request = urllib.request.Request(
        portal_url(host, port, "/api/pair"),
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "mydock-agent"},
        method="POST",
    )
    try:
        with _opener().open(request, timeout=timeout) as response:
            data = _read_json(response) or {}
    except urllib.error.HTTPError as exc:
        data = _read_json(exc) or {}
        ips = data.get("pi_ips")
        raise PairingError(
            str(data.get("error") or f"http_{exc.code}"),
            str(data.get("message") or f"Le dock a répondu {exc.code}."),
            pi_ips=[str(i) for i in ips] if isinstance(ips, list) else [],
        ) from None
    except (OSError, urllib.error.URLError) as exc:
        reason = getattr(exc, "reason", exc)
        raise PairingError(
            "portal_unreachable",
            f"Le portail du dock ne répond pas ({portal_url(host, port)} : {reason}).",
        ) from None
    if data.get("ok") is not True:
        raise PairingError("bad_answer", "Réponse inattendue du dock.")
    ips = data.get("pi_ips")
    dock = data.get("dock") if isinstance(data.get("dock"), dict) else {}
    return Paired(
        pi_ips=[str(i) for i in ips] if isinstance(ips, list) else [],
        dock_hostname=str(dock.get("hostname", "")),
        dock_version=str(dock.get("version", "")),
    )
