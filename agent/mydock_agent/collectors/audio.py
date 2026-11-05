"""Thème « audio » : le son du PC (sorties, micro, volume de chaque application), par `pactl`.

`pactl` parle au serveur de son de la session : PipeWire par sa couche de compatibilité
PulseAudio (installée par défaut sur Ubuntu), ou PulseAudio lui-même. Rien n'est interrogé en
boucle : `pactl subscribe` signale chaque changement (volume réglé au clavier, casque branché,
application qui démarre) et l'agent ne relit que la partie concernée, au plus tous les 50 ms.

Trois précautions :
  - tous les appels se font en `LC_ALL=C` : pactl traduit ses événements et la disponibilité des
    prises selon la langue de la session, l'agent a besoin de mots stables ;
  - le JSON de pactl (16 et 17) n'écrit aucun caractère accentué : il met « (null) » à la place du
    texte entier (« Écouteurs de Marie »). Ce texte n'est jamais affiché : l'agent cherche un autre
    nom (voir `readable` et `label_from_name`) ;
  - aucune commande ne vient du dock : trois verbes (volume, muet, sortie par défaut) appliqués à un
    périphérique ou une application qui figure dans l'état courant, sans shell.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from mydock_agent.collectors.music import PREFERRED_PLAYER, clean_text
from mydock_agent.hub import Provider, Publish
from mydock_common.actions import ActionContext, ActionError, ActionRegistry

log = logging.getLogger(__name__)

# En %. Le dock n'amplifie pas ; un volume plus fort réglé ailleurs reste affiché tel quel.
MAX_VOLUME = 100
NORM = 65536  # 100 % pour PulseAudio et PipeWire
COALESCE_SECONDS = 0.05  # regroupe les salves d'événements (un seul relevé par salve)
NUDGE_SECONDS = 0.3  # nouveau relevé après un changement de sortie par défaut (voir `_nudge`)
RETRY_SECONDS = 5.0
COMMAND_TIMEOUT = 5.0
STOP_TIMEOUT = 2.0
MAX_APPS = 20
NAME_LIMIT = 60
LABEL_LIMIT = 80

# Un nom de périphérique est transmis à pactl : on n'en accepte aucun qui puisse passer pour une option.
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:+@=,][A-Za-z0-9_.:+@=,-]{0,199}$")
EVENT_RE = re.compile(r"^Event '([a-z]+)' on ([a-z-]+) #(\d+)\s*$")
ALSA_PLUGIN = re.compile(r"^ALSA plug-in \[(.+)\]$")
# Le JSON de pactl (16 et 17) refuse tout caractère hors ASCII : un nom accentué (« Écouteurs de
# Marie », une application, un titre) est remplacé par ce texte, quelle que soit la langue. On ne
# l'affiche jamais : on cherche un autre nom, puis on se rabat sur ce que le nom technique laisse deviner.
NULL_TEXT = "(null)"
BLUEZ_NAME = re.compile(r"^bluez_[a-z]+\.([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})")
USB_NAME = re.compile(r"^alsa_(?:output|input)\.usb-(.+?)-\d\d\.")
HDMI_NUMBER = re.compile(r"(?:HDMI|DisplayPort)[^\d]*(\d+)", re.IGNORECASE)
# « HD Pro Webcam C920 Analog Stereo » : la fin décrit le profil de la carte, pas l'appareil.
PROFILE_SUFFIX = re.compile(
    r"\s+(?:(?:Analog|Digital)\s+(?:Stereo|Mono|Surround\s+\d\.\d)|Multichannel)"
    r"(?:\s+\([^)]*\))?\s*$",
    re.IGNORECASE,
)

HEADPHONE_FORMS = frozenset({"headphone", "headset", "hands-free", "handset"})
SPEAKER_FORMS = frozenset({"speaker", "computer", "internal", "portable", "tv", "hifi", "car"})
KIND_ORDER = {"speakers": 0, "headphones": 1, "bluetooth": 2, "hdmi": 3, "usb": 4, "microphone": 0}

# Noms d'applications que les programmes déclarent en minuscules (leur nom de binaire).
DISPLAY_NAMES = {
    "spotify": "Spotify",
    "firefox": "Firefox",
    "chromium": "Chromium",
    "brave": "Brave",
    "discord": "Discord",
    "steam": "Steam",
    "telegram-desktop": "Telegram",
}

# Flux qui ne sont pas « une application à régler » : sons d'événements du bureau, indicateurs de
# niveau des panneaux de réglage.
IGNORED_ROLES = frozenset({"event"})
IGNORED_MEDIA = frozenset({"peak detect", "volume meter", "level meter"})
IGNORED_APP_IDS = frozenset({"org.pulseaudio.pavucontrol"})
IGNORED_APP_NAMES = frozenset({"pavucontrol", "pulseaudio volume control"})

#: Ce qui change quand pactl annonce un événement sur... (les clients et modules sont sans effet ici).
FACILITY_PARTS = {
    "sink": "sinks",
    "source": "sources",
    "sink-input": "inputs",
    "source-output": "outputs",
    "server": "info",
}
ALL_PARTS = frozenset({"info", "sinks", "sources", "inputs", "outputs"})

Kind = Literal["sink", "source", "app"]


# --- volumes -----------------------------------------------------------------------------------


def channel_values(volume: Any) -> list[int]:
    """Volume brut de chaque canal (65536 = 100 %), tel que le donne `pactl -f json`."""
    if not isinstance(volume, dict):
        return []
    values: list[int] = []
    for channel in volume.values():
        value = channel.get("value") if isinstance(channel, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            values.append(value)
    return values


def percent_of(raw: int) -> int:
    return (raw * 100 + NORM // 2) // NORM


def volume_percent(volume: Any) -> int | None:
    """Volume affiché : celui du canal le plus fort, comme le font GNOME et pavucontrol."""
    values = channel_values(volume)
    return percent_of(max(values)) if values else None


def scaled_volumes(current: list[int], percent: int) -> list[int]:
    """Volumes bruts à appliquer pour atteindre `percent`, en gardant l'équilibre gauche/droite.

    Un seul pourcentage donné à pactl mettrait tous les canaux au même niveau : un équilibre réglé
    dans les paramètres du bureau serait effacé en silence. On met donc le canal le plus fort au
    niveau demandé et les autres dans la même proportion qu'avant.
    """
    target = (percent * NORM + 50) // 100
    peak = max(current, default=0)
    if peak == 0:
        return [target] * max(1, len(current))
    return [round(value * target / peak) for value in current]


# --- périphériques -----------------------------------------------------------------------------


def is_monitor(source: dict[str, Any]) -> bool:
    """Un « moniteur » écoute une sortie (enregistrer ce que joue le PC) : ce n'est pas un micro."""
    props = source.get("properties") or {}
    return (
        props.get("device.class") == "monitor"
        or str(source.get("name", "")).endswith(".monitor")
        or isinstance(source.get("monitor_of_sink"), int)
    )


def _ports(device: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in device.get("ports") or [] if isinstance(p, dict)]


def is_unplugged(device: dict[str, Any]) -> bool:
    """Toutes les prises du périphérique sont signalées « non disponibles » (HDMI sans écran...)."""
    ports = _ports(device)
    return bool(ports) and all(p.get("availability") == "not available" for p in ports)


def device_kind(device: dict[str, Any], *, source: bool) -> str:
    """Famille d'un périphérique, pour choisir son icône et son nom."""
    props = device.get("properties") or {}
    name = str(device.get("name") or "")
    active = next((p for p in _ports(device) if p.get("name") == device.get("active_port")), {})
    text = f"{name} {active.get('name', '')} {active.get('type', '')}".lower()
    form = str(props.get("device.form_factor") or "").lower()
    bus = str(props.get("device.bus") or "").lower()
    if bus == "bluetooth" or name.startswith("bluez"):
        return "bluetooth"
    if "hdmi" in text or "displayport" in text:
        return "hdmi"
    if form in HEADPHONE_FORMS or "headphone" in text or "headset" in text:
        return "headphones"
    if bus == "usb":
        return "usb"
    if source:
        return "line" if "line" in text else "microphone"
    if form in SPEAKER_FORMS or "speaker" in text:
        return "speakers"
    return "other"


def device_label(kind: str, description: str, *, source: bool) -> str:
    """Nom lisible en français ; pour le reste (USB, Bluetooth...) le nom que donne l'appareil."""
    if kind == "speakers":
        return "Haut-parleurs"
    if kind == "headphones":
        return "Micro du casque" if source else "Casque"
    if kind == "microphone":
        return "Micro intégré"
    if kind == "line":
        return "Entrée ligne"
    if kind == "hdmi":
        match = HDMI_NUMBER.search(description)
        return f"HDMI {match.group(1)}" if match else "HDMI"
    # Le profil de la carte (« Analog Stereo ») dit comment elle est branchée, pas ce qu'elle est.
    return PROFILE_SUFFIX.sub("", description).strip() or description


def _unique_labels(items: list[dict[str, Any]]) -> None:
    """Deux périphériques ne portent jamais le même nom à l'écran (on retombe sur leur description)."""
    for _ in range(2):
        seen: dict[str, int] = {}
        for item in items:
            seen[item["label"]] = seen.get(item["label"], 0) + 1
        clashing = {label for label, count in seen.items() if count > 1}
        if not clashing:
            return
        for item in items:
            if item["label"] in clashing and item["detail"]:
                item["label"], item["detail"] = item["detail"], None
    counts: dict[str, int] = {}
    for item in items:
        counts[item["label"]] = counts.get(item["label"], 0) + 1
        if counts[item["label"]] > 1:
            item["label"] = f"{item['label']} ({counts[item['label']]})"


def readable(value: Any, limit: int = LABEL_LIMIT) -> str | None:
    """Un texte à afficher, ou None s'il est vide ou perdu par pactl (voir NULL_TEXT)."""
    text = clean_text(value, limit)
    return None if text is None or text == NULL_TEXT else text


def label_from_name(name: str, *, source: bool) -> str:
    """Dernier recours : ce que le nom technique du périphérique laisse deviner de lui."""
    if bluez := BLUEZ_NAME.match(name):
        return "Bluetooth " + ":".join(bluez.group(1).split("_")[-3:]).upper()
    if usb := USB_NAME.match(name):
        return usb.group(1).replace("_", " ")
    return "Entrée audio" if source else "Sortie audio"


def device_description(device: dict[str, Any], *, source: bool) -> str:
    props = device.get("properties") or {}
    for text in (
        device.get("description"),
        props.get("device.product.name"),
        props.get("alsa.card_name"),
        props.get("node.nick"),
    ):
        if (found := readable(text)) is not None:
            return found
    return label_from_name(str(device.get("name")), source=source)


def device_item(device: dict[str, Any], *, source: bool) -> dict[str, Any] | None:
    name = device.get("name")
    if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
        return None
    description = device_description(device, source=source)
    kind = device_kind(device, source=source)
    label = device_label(kind, description, source=source)
    return {
        "id": name,
        "label": label,
        "detail": description if description != label else None,
        "kind": kind,
        "volume": volume_percent(device.get("volume")),
        "muted": bool(device.get("mute")),
    }


def _devices(raw: list[dict[str, Any]], *, source: bool, keep: str | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for device in raw:
        if source and is_monitor(device):
            continue
        if not source and device.get("name") == "auto_null":
            continue  # la sortie « factice » de PipeWire quand aucune carte son n'est là
        if is_unplugged(device) and device.get("name") != keep:
            continue
        if (item := device_item(device, source=source)) is not None:
            items.append(item)
    items.sort(key=lambda d: (KIND_ORDER.get(d["kind"], 9), d["label"].lower(), d["id"]))
    _unique_labels(items)
    return items


# --- applications ------------------------------------------------------------------------------


def is_application(stream: dict[str, Any]) -> bool:
    props = stream.get("properties") or {}
    if str(props.get("media.role", "")).lower() in IGNORED_ROLES:
        return False
    if str(props.get("media.name", "")).lower() in IGNORED_MEDIA:
        return False
    if str(props.get("application.id", "")).lower() in IGNORED_APP_IDS:
        return False
    return str(props.get("application.name", "")).lower() not in IGNORED_APP_NAMES


def stream_name(stream: dict[str, Any]) -> str:
    props = stream.get("properties") or {}
    for key in ("application.name", "application.process.binary", "media.name", "node.name"):
        text = readable(props.get(key), NAME_LIMIT)
        if text:
            match = ALSA_PLUGIN.fullmatch(text)
            text = match.group(1) if match else text
            return DISPLAY_NAMES.get(text.lower(), text)
    return "Application"


def _index(item: dict[str, Any]) -> int | None:
    value = item.get("index")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass
class RawAudio:
    """Ce que pactl a répondu, tel quel : l'état publié s'en déduit (voir `build_snapshot`)."""

    default_sink: str | None = None
    default_source: str | None = None
    sinks: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    inputs: list[dict[str, Any]] = field(default_factory=list)  # flux de lecture (applications)
    outputs: list[dict[str, Any]] = field(default_factory=list)  # flux d'enregistrement


def build_snapshot(raw: RawAudio) -> dict[str, Any]:
    sinks = _devices(raw.sinks, source=False, keep=raw.default_sink)
    sources = _devices(raw.sources, source=True, keep=raw.default_source)
    sink_names = {i: s.get("name") for s in raw.sinks if (i := _index(s)) is not None}
    sources_by_index = {i: s for s in raw.sources if (i := _index(s)) is not None}

    apps: list[dict[str, Any]] = []
    for stream in raw.inputs:
        index = _index(stream)
        volume = volume_percent(stream.get("volume"))
        if index is None or volume is None or not is_application(stream):
            continue
        sink = sink_names.get(stream.get("sink"))
        apps.append(
            {
                "id": str(index),
                "name": stream_name(stream),
                "volume": volume,
                "muted": bool(stream.get("mute")),
                "playing": not stream.get("corked"),
                "sink": sink if isinstance(sink, str) else None,
            }
        )
    # Le lecteur préféré (Spotify) reste en tête ; les autres gardent leur ordre d'arrivée, pour
    # qu'une ligne ne saute pas sous le doigt quand une application se met en pause.
    apps.sort(key=lambda a: (a["name"].lower() != PREFERRED_PLAYER, int(a["id"])))

    recorders: list[dict[str, Any]] = []
    for stream in sorted(raw.outputs, key=lambda s: _index(s) or 0):
        index = _index(stream)
        source = sources_by_index.get(stream.get("source"))
        if index is None or stream.get("corked") or not is_application(stream):
            continue
        if source is None or is_monitor(source):  # elle écoute une sortie, pas un micro
            continue
        recorders.append(
            {"id": str(index), "name": stream_name(stream), "source": source.get("name")}
        )

    shown_sinks = {s["id"] for s in sinks}
    shown_sources = {s["id"] for s in sources}
    return {
        "max": MAX_VOLUME,
        "default_sink": raw.default_sink if raw.default_sink in shown_sinks else None,
        "default_source": raw.default_source if raw.default_source in shown_sources else None,
        "sinks": sinks,
        "sources": sources,
        "apps": apps[:MAX_APPS],
        "recorders": recorders[:MAX_APPS],
    }


# --- pactl -------------------------------------------------------------------------------------


class PactlError(Exception):
    """pactl a échoué (serveur de son absent, entité disparue, délai dépassé...)."""

    def __init__(self, message: str, *, gone: bool = False):
        super().__init__(message)
        self.gone = gone  # « No such entity » : l'élément visé n'existe plus


class PactlMissing(PactlError):
    """pactl n'est pas installé."""


class Pactl(Protocol):
    async def query(self, *args: str) -> Any:
        """`pactl -f json <args>` : la réponse décodée."""

    async def command(self, *args: str) -> None:
        """`pactl <args>` : une commande qui modifie l'état."""

    def subscribe(self) -> AbstractAsyncContextManager[AsyncIterator[str]]:
        """Les lignes de `pactl subscribe`, jusqu'à ce que le serveur de son s'arrête."""


def _first_line(data: bytes) -> str:
    text = data.decode("utf-8", "replace").strip().splitlines()
    return (text[0] if text else "")[:200]


class SubprocessPactl:
    """Le vrai pactl. Jamais de shell ; arguments séparés ; délai pour chaque appel."""

    def __init__(self, binary: str = "pactl", *, env: dict[str, str] | None = None):
        self._binary = binary
        self._env = {**(os.environ if env is None else env), "LC_ALL": "C"}
        self._env.pop("LANGUAGE", None)

    async def _spawn(self, *args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                self._binary, *args, stdin=asyncio.subprocess.DEVNULL, env=self._env, **kwargs
            )
        except FileNotFoundError:
            raise PactlMissing("pactl est introuvable.") from None
        except OSError as exc:
            raise PactlError(f"pactl ne démarre pas ({exc.__class__.__name__}).") from None

    async def _run(self, *args: str) -> bytes:
        process = await self._spawn(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), COMMAND_TIMEOUT)
        except TimeoutError:
            raise PactlError("pactl ne répond pas.") from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode != 0:
            message = _first_line(err) or f"pactl a échoué (code {process.returncode})."
            raise PactlError(message, gone="No such entity" in message)
        return out

    async def query(self, *args: str) -> Any:
        out = await self._run("-f", "json", *args)
        try:
            return json.loads(out)
        except ValueError:
            raise PactlError(
                "Réponse illisible de pactl (version trop ancienne ? la 16 ou plus est requise)."
            ) from None

    async def command(self, *args: str) -> None:
        await self._run(*args)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[AsyncIterator[str]]:
        process = await self._spawn(
            "subscribe", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )

        async def lines() -> AsyncIterator[str]:
            assert process.stdout is not None
            while line := await process.stdout.readline():
                yield line.decode("utf-8", "replace")

        try:
            yield lines()
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), STOP_TIMEOUT)
                except TimeoutError:
                    process.kill()
                    await process.wait()


def parse_event(line: str) -> tuple[str, str, int] | None:
    """« Event 'change' on sink #45 » -> ('change', 'sink', 45) ; None pour tout autre texte."""
    match = EVENT_RE.match(line)
    return (match.group(1), match.group(2), int(match.group(3))) if match else None


# --- le fournisseur ----------------------------------------------------------------------------


class AudioBackend(Protocol):
    """Ce que les actions `audio.*` attendent d'un fournisseur (réel ou de démonstration)."""

    async def set_volume(self, kind: Kind, ident: str, percent: int) -> None: ...
    async def set_mute(self, kind: Kind, ident: str, muted: bool) -> None: ...
    async def set_default(self, kind: Literal["sink", "source"], ident: str) -> None: ...


class AudioProvider(Provider):
    topic = "audio"
    lazy = True  # démarré par le premier abonné : pas de `pactl subscribe` tant que personne ne regarde

    def __init__(self, pactl: Pactl | None = None):
        self._pactl: Pactl = pactl or SubprocessPactl()
        self._raw = RawAudio()
        self._loaded = False
        self._write_lock = asyncio.Lock()
        self._wanted: dict[tuple[str, str], int] = {}
        self._events: asyncio.Queue[tuple[str, str, int] | None] | None = (
            None  # ceux de la session en cours
        )

    # --- cycle de vie ---

    async def run(self, publish: Publish) -> None:
        try:
            while True:
                try:
                    await self._session(publish)
                except PactlError as exc:
                    log.warning("Son : %s", exc)
                    await publish({"unavailable": _unavailable_message(exc)})
                self._raw, self._loaded = RawAudio(), False
                await asyncio.sleep(RETRY_SECONDS)
        finally:
            self._raw, self._loaded = RawAudio(), False

    async def _session(self, publish: Publish) -> None:
        events: asyncio.Queue[tuple[str, str, int] | None] = asyncio.Queue()
        self._events = events
        async with self._pactl.subscribe() as lines:
            pump = asyncio.create_task(self._pump(lines, events))
            try:
                await self.read_all()  # après l'abonnement : aucun changement n'est manqué
                await publish(self.snapshot())
                while True:
                    dirty = self._parts(await self._next(events))
                    await asyncio.sleep(COALESCE_SECONDS)  # laisse arriver la suite de la salve
                    while not events.empty():
                        dirty |= self._parts(await self._next(events))
                    if dirty:
                        await self._refresh(dirty)
                        await publish(self.snapshot())
            finally:
                self._events = None
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)

    @staticmethod
    async def _pump(
        lines: AsyncIterator[str], events: asyncio.Queue[tuple[str, str, int] | None]
    ) -> None:
        try:
            async for line in lines:
                if (event := parse_event(line)) is not None:
                    events.put_nowait(event)
        finally:
            events.put_nowait(None)

    @staticmethod
    async def _next(events: asyncio.Queue[tuple[str, str, int] | None]) -> tuple[str, str, int]:
        event = await events.get()
        if event is None:
            raise PactlError("Le serveur de son s'est arrêté.")
        return event

    @staticmethod
    def _parts(event: tuple[str, str, int]) -> set[str]:
        part = FACILITY_PARTS.get(event[1])
        return {part} if part else set()

    # --- état ---

    def snapshot(self) -> dict[str, Any]:
        return build_snapshot(self._raw)

    async def _fetch(self, part: str) -> Any:
        if part == "info":
            return await self._pactl.query("info")
        listing = {
            "sinks": "sinks",
            "sources": "sources",
            "inputs": "sink-inputs",
            "outputs": "source-outputs",
        }[part]
        data = await self._pactl.query("list", listing)
        if not isinstance(data, list):
            raise PactlError("Réponse inattendue de pactl.")
        return [item for item in data if isinstance(item, dict)]

    async def read_all(self) -> None:
        """Relit tout (démarrage, diagnostic, première action quand le fournisseur dort)."""
        await self._refresh(ALL_PARTS)

    async def _refresh(self, parts: set[str] | frozenset[str]) -> None:
        ordered = sorted(parts)
        results = await asyncio.gather(*(self._fetch(part) for part in ordered))
        for part, value in zip(ordered, results, strict=True):
            if part == "info":
                info = value if isinstance(value, dict) else {}
                self._raw.default_sink = info.get("default_sink_name") or None
                self._raw.default_source = info.get("default_source_name") or None
            else:
                setattr(self._raw, part, value)
        if parts >= ALL_PARTS:
            self._loaded = True

    # --- actions ---

    def _lookup(self, kind: Kind, ident: str) -> dict[str, Any] | None:
        """L'élément brut visé, à condition qu'il figure dans l'état publié (ce que l'écran montre)."""
        shown = self.snapshot()
        if kind == "app":
            if ident not in {a["id"] for a in shown["apps"]}:
                return None
            pool, key = self._raw.inputs, "index"
        else:
            if ident not in {d["id"] for d in shown["sinks" if kind == "sink" else "sources"]}:
                return None
            pool, key = (self._raw.sinks if kind == "sink" else self._raw.sources), "name"
        return next((item for item in pool if str(item.get(key)) == ident), None)

    async def _target(self, kind: Kind, ident: str) -> dict[str, Any]:
        """L'élément visé, d'après l'état courant ; relu une fois s'il est inconnu (état ancien)."""
        try:
            if not self._loaded:
                await self.read_all()
            found = self._lookup(kind, ident)
            if found is None:
                await self._refresh({_PART_OF_KIND[kind]})
                found = self._lookup(kind, ident)
        except PactlError as exc:
            raise ActionError("unavailable", _describe(exc)) from None
        if found is None:
            raise ActionError("not_found", _GONE[kind])
        return found

    async def _apply(self, *args: str) -> None:
        try:
            await self._pactl.command(*args)
        except PactlError as exc:
            if exc.gone:
                raise ActionError("not_found", "Cet élément audio n'est plus là.") from None
            raise ActionError(
                "failed", f"Le serveur de son a refusé la commande ({exc})."
            ) from None

    async def set_volume(self, kind: Kind, ident: str, percent: int) -> None:
        key = (kind, ident)
        self._wanted[key] = percent  # une salve de réglages : seule la dernière valeur compte
        async with self._write_lock:  # file d'attente dans l'ordre d'arrivée
            newer = self._wanted.get(key)
            if newer is not None and newer != percent:
                return  # remplacée par un réglage plus récent, qui s'applique juste après
            self._wanted.pop(key, None)
            target = await self._target(kind, ident)
            values = scaled_volumes(channel_values(target.get("volume")), percent)
            if len(set(values)) == 1:
                values = values[:1]  # un seul nombre vaut pour tous les canaux
            await self._apply(_SET_VOLUME[kind], _address(kind, target), *map(str, values))

    async def set_mute(self, kind: Kind, ident: str, muted: bool) -> None:
        async with self._write_lock:
            target = await self._target(kind, ident)
            await self._apply(_SET_MUTE[kind], _address(kind, target), "1" if muted else "0")

    async def set_default(self, kind: Literal["sink", "source"], ident: str) -> None:
        async with self._write_lock:
            target = await self._target(kind, ident)
            verb = "set-default-sink" if kind == "sink" else "set-default-source"
            await self._apply(verb, _address(kind, target))
        self._nudge("server")

    def _nudge(self, facility: str) -> None:
        """Redemande un relevé un instant plus tard : PipeWire annonce parfois le changement de sortie par défaut
        avant de le refléter, et l'écran garderait l'ancienne sortie jusqu'au prochain événement."""
        events = self._events
        if events is not None:
            asyncio.get_running_loop().call_later(
                NUDGE_SECONDS, events.put_nowait, ("change", facility, 0)
            )


_PART_OF_KIND = {"sink": "sinks", "source": "sources", "app": "inputs"}
_GONE = {
    "sink": "Cette sortie n'est plus là.",
    "source": "Ce micro n'est plus là.",
    "app": "Cette application n'est plus là.",
}
_SET_VOLUME = {
    "sink": "set-sink-volume",
    "source": "set-source-volume",
    "app": "set-sink-input-volume",
}
_SET_MUTE = {"sink": "set-sink-mute", "source": "set-source-mute", "app": "set-sink-input-mute"}


def _address(kind: Kind, target: dict[str, Any]) -> str:
    """Ce qu'on donne à pactl : le nom d'un périphérique, le numéro d'un flux."""
    return str(target["index"]) if kind == "app" else str(target["name"])


def _describe(exc: Exception) -> str:
    if isinstance(exc, PactlMissing):
        return "pactl est introuvable : installe le paquet pulseaudio-utils (sudo apt install pulseaudio-utils)."
    return f"Le serveur de son ne répond pas ({str(exc).rstrip('. ')})."


def _unavailable_message(exc: Exception) -> str:
    return f"{_describe(exc)} Nouvelle tentative dans {int(RETRY_SECONDS)} s."


# --- actions -----------------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


DeviceId = Annotated[str, StringConstraints(pattern=SAFE_NAME.pattern)]


class VolumeParams(_Strict):
    kind: Literal["sink", "source", "app"]
    id: DeviceId
    percent: int = Field(ge=0, le=MAX_VOLUME, strict=True)


class MuteParams(_Strict):
    kind: Literal["sink", "source", "app"]
    id: DeviceId
    muted: bool = Field(strict=True)


class DefaultParams(_Strict):
    kind: Literal["sink", "source"]
    id: DeviceId


def register_audio_actions(registry: ActionRegistry, backend: AudioBackend) -> None:
    async def set_volume(_ctx: ActionContext, params: VolumeParams) -> dict[str, bool]:
        await backend.set_volume(params.kind, params.id, params.percent)
        return {"done": True}

    async def set_mute(_ctx: ActionContext, params: MuteParams) -> dict[str, bool]:
        await backend.set_mute(params.kind, params.id, params.muted)
        return {"done": True}

    async def set_default(_ctx: ActionContext, params: DefaultParams) -> dict[str, bool]:
        await backend.set_default(params.kind, params.id)
        return {"done": True}

    registry.register("audio.set_volume", set_volume, params=VolumeParams, timeout=8.0)
    registry.register("audio.set_mute", set_mute, params=MuteParams, timeout=8.0)
    registry.register("audio.set_default", set_default, params=DefaultParams, timeout=8.0)
