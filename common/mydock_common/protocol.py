"""Protocole WebSocket de MyDock, version 1.

Trois acteurs parlent le même vocabulaire de messages JSON :

    navigateur (kiosque)  <-->  serveur dock (Pi)  <-->  agent (PC)

Chaque message est un objet JSON dont la clé ``t`` donne le type.

Client -> serveur
    {"t": "sub",   "topics": ["music", ...]}      s'abonner (un instantané est renvoyé)
    {"t": "unsub", "topics": ["music", ...]}      se désabonner
    {"t": "act",   "id": "c1", "a": "diag.ping", "p": {...}, "confirm": "<jeton>"}
    {"t": "ping",  "n": 1}

Serveur -> client
    {"t": "hello", "proto": 1, "agent": {...}}    juste après l'authentification
    {"t": "snap",  "topic": "host", "rev": 3, "data": {...}}   état complet d'un thème
    {"t": "ack",   "id": "c1", "ok": true,  "r": {...}, "ms": 2.1}
    {"t": "ack",   "id": "c1", "ok": false, "e": {"code": "...", "msg": "..."}}
    {"t": "pong",  "n": 1, "ts": 1700000000000}
    {"t": "err",   "code": "...", "msg": "..."}

Principe : l'état d'un thème n'est envoyé que lorsqu'il change, et le client garde le
dernier instantané reçu. Une action n'existe que si l'agent l'a enregistrée dans sa liste
blanche ; ses paramètres sont validés côté agent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 256 * 1024
MAX_TOPICS_PER_MESSAGE = 16

# Thèmes publiés par l'agent (le PC). « system » (mesures, historique de 60 s) est suivi en
# permanence par le dock ; « procs » (processus) n'est échantillonné que page Système ouverte.
AGENT_TOPICS: tuple[str, ...] = (
    "host",
    "system",
    "procs",
    "music",
    "audio",
    "apps",
    "steam",
    "office",
    "power",
)
# Thèmes locaux du serveur dock, poussés d'office aux navigateurs.
# « player » : ce que joue l'écran du dock (Freetv, Kiosque) et l'état de VLC.
DOCK_TOPICS: tuple[str, ...] = ("link", "dock", "ui", "player", "retro", "pads")

ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){1,3}$")
_TOPIC_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class ErrorCode(StrEnum):
    BAD_MESSAGE = "bad_message"
    UNKNOWN_TYPE = "unknown_type"
    UNKNOWN_TOPIC = "unknown_topic"
    UNKNOWN_ACTION = "unknown_action"
    BAD_PARAMS = "bad_params"
    CONFIRM_REQUIRED = "confirm_required"
    CONFIRM_INVALID = "confirm_invalid"
    PC_OFFLINE = "pc_offline"
    DOCK_OFFLINE = "dock_offline"
    TIMEOUT = "timeout"
    FAILED = "failed"
    TOO_LARGE = "too_large"
    UNAVAILABLE = "unavailable"
    PROTOCOL_MISMATCH = "protocol_mismatch"


class ProtocolError(Exception):
    """Message mal formé ou non conforme au protocole."""

    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Act:
    """Demande d'action venue d'un client, déjà vérifiée côté forme."""

    id: str
    name: str
    params: dict[str, Any]
    confirm: str | None = None


def dumps(message: dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False)


def loads(raw: str | bytes) -> dict[str, Any]:
    """Décode un message reçu ; lève ProtocolError s'il n'est pas un objet typé."""
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError(ErrorCode.TOO_LARGE, "Message trop volumineux")
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ProtocolError(ErrorCode.BAD_MESSAGE, "JSON invalide") from None
    if not isinstance(obj, dict) or not isinstance(obj.get("t"), str):
        raise ProtocolError(ErrorCode.BAD_MESSAGE, "Message sans type")
    return obj


def parse_topics(msg: dict[str, Any]) -> list[str]:
    topics = msg.get("topics")
    if (
        not isinstance(topics, list)
        or not topics
        or len(topics) > MAX_TOPICS_PER_MESSAGE
        or not all(isinstance(t, str) and _TOPIC_RE.fullmatch(t) for t in topics)
    ):
        raise ProtocolError(ErrorCode.BAD_MESSAGE, "Liste de thèmes invalide")
    return list(dict.fromkeys(topics))  # sans doublon, ordre conservé


def parse_act(msg: dict[str, Any]) -> Act:
    act_id = msg.get("id")
    if not isinstance(act_id, str) or not _ID_RE.fullmatch(act_id):
        raise ProtocolError(ErrorCode.BAD_MESSAGE, "Identifiant d'action invalide")
    name = msg.get("a")
    if not isinstance(name, str) or not ACTION_NAME_RE.fullmatch(name):
        raise ProtocolError(ErrorCode.BAD_MESSAGE, "Nom d'action invalide")
    params = msg.get("p")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ProtocolError(ErrorCode.BAD_PARAMS, "Les paramètres doivent être un objet")
    confirm = msg.get("confirm")
    if confirm is not None and (not isinstance(confirm, str) or len(confirm) > 128):
        raise ProtocolError(ErrorCode.BAD_MESSAGE, "Jeton de confirmation invalide")
    return Act(act_id, name, params, confirm)


def hello(agent: dict[str, Any]) -> dict[str, Any]:
    return {"t": "hello", "proto": PROTOCOL_VERSION, "agent": agent}


def snap(topic: str, rev: int, data: dict[str, Any]) -> dict[str, Any]:
    return {"t": "snap", "topic": topic, "rev": rev, "data": data}


def ack_ok(act_id: str, result: Any = None, *, ms: float | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"t": "ack", "id": act_id, "ok": True}
    if result is not None:
        message["r"] = result
    if ms is not None:
        message["ms"] = round(ms, 2)
    return message


def ack_error(
    act_id: str,
    code: ErrorCode | str,
    message: str,
    *,
    ms: float | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "t": "ack",
        "id": act_id,
        "ok": False,
        "e": {"code": str(code), "msg": message},
    }
    if ms is not None:
        reply["ms"] = round(ms, 2)
    if extra:
        reply.update(extra)
    return reply


def err(code: ErrorCode | str, message: str) -> dict[str, Any]:
    return {"t": "err", "code": str(code), "msg": message}


def pong(n: Any, ts_ms: int) -> dict[str, Any]:
    return {"t": "pong", "n": n, "ts": ts_ms}
