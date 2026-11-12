"""Le lecteur du dock : ce qui joue sur l'écran du Pi (Freetv, Kiosque), et la télécommande qui le pilote.

VLC ne tourne pas dans ce service : celui-ci est isolé (pas d'écran, pas de session graphique). C'est « l'assistant de
lecture » (player_session.py), lancé par le compositeur dans la session de l'écran, qui démarre VLC en plein écran et le
commande. Il se connecte ici par `/ws/player` (en local seulement) et :

  * reçoit des commandes (lire, pause, avancer, volume, arrêter, passer de la vidéo au dock) ;
  * rend compte de ce qui se passe (VLC joue, position, durée, VLC s'est arrêté, pourquoi).

Ce module garde l'état de lecture, que l'interface lit par le thème « player », et décide de ce qui est permis : l'écran
n'envoie jamais d'adresse de flux, seulement l'identifiant d'une chaîne ou un partage et un chemin que le dock a vus.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

#: Une lecture qui ne démarre pas au bout de ce temps est déclarée en échec (VLC a pu se bloquer sur une chaîne muette).
START_TIMEOUT = 40.0
DEFAULT_VOLUME = 70
STATES = ("idle", "starting", "playing", "paused", "error")
ACTIVE_STATES = ("starting", "playing", "paused")


class PlayerError(Exception):
    """La lecture ne peut pas démarrer ; le message est affichable à l'écran."""


class HelperConn:
    """L'assistant de lecture connecté : une file de commandes à lui envoyer."""

    def __init__(self) -> None:
        self.outbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=64)
        self.info: dict[str, Any] = {}

    def send(self, message: dict[str, Any]) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self.outbox.put_nowait(message)

    def close(self) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self.outbox.put_nowait(None)


def _number(value: object, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value != value or not low <= value <= high:  # NaN, hors limites
        return None
    return float(value)


class PlayerHub:
    def __init__(
        self, on_change: Callable[[], None], *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._on_change = on_change
        self._clock = clock
        self.helper: HelperConn | None = None
        self.state = "idle"
        self.media: dict[str, Any] | None = None
        self.position: float | None = None
        self.length: float | None = None
        self.volume = DEFAULT_VOLUME
        self.front = "dock"
        self.seekable = (
            True  # faux quand le fichier arrive par un tube (smbclient) : pas d'avance rapide
        )
        self.error: str | None = None
        self.job = 0
        self._started_at = 0.0
        self._watchdog: asyncio.Task[None] | None = None

    # --- ce que voit l'écran -----------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def playing(self) -> bool:
        return self.state == "playing"

    def snapshot(self) -> dict[str, Any]:
        helper = self.helper.info if self.helper else None
        media = self.media or {}
        return {
            "available": self.helper is not None,
            "state": self.state,
            "kind": media.get("kind"),
            "title": media.get("title"),
            "subtitle": media.get("subtitle"),
            "live": bool(media.get("live")),
            "position": self.position,
            "length": self.length,
            "volume": self.volume,
            "front": self.front,
            "seekable": self.seekable,
            "error": self.error,
            "helper": {k: helper.get(k) for k in ("vlc", "live555", "ffmpeg", "touch", "demo")}
            if helper
            else None,
        }

    def _changed(self) -> None:
        self._on_change()

    # --- l'assistant de lecture ----------------------------------------------------------------------

    def attach(self, conn: HelperConn) -> None:
        """Un assistant se connecte ; un ancien est remplacé (il a dû redémarrer)."""
        old, self.helper = self.helper, conn
        if old is not None:
            old.close()
        if self.active:  # l'ancien assistant gardait la lecture : le nouveau ne la connaît pas
            self._fail("L'assistant de lecture a redémarré : la lecture s'est arrêtée.")
        self._changed()

    def detach(self, conn: HelperConn) -> None:
        if self.helper is not conn:
            return
        self.helper = None
        if self.active:
            self._fail("L'assistant de lecture s'est arrêté : la lecture est interrompue.")
        self._changed()

    def on_message(self, conn: HelperConn, message: dict[str, Any]) -> None:
        """Un message de l'assistant (jamais cru sur parole : types et bornes vérifiés)."""
        if self.helper is not conn:
            return
        kind = message.get("t")
        if kind == "hello":
            conn.info = {
                "vlc": bool(message.get("vlc")),
                "live555": bool(message.get("live555")),
                "ffmpeg": bool(message.get("ffmpeg")),
                "touch": bool(message.get("touch")),
                "demo": bool(message.get("demo")),
            }
            self._changed()
        elif kind == "state":
            if message.get("job") != self.job:
                return  # le rapport d'une lecture déjà remplacée
            self._update(message)
        elif kind == "ended":
            if message.get("job") != self.job:
                return
            self._ended(message)

    def _update(self, message: dict[str, Any]) -> None:
        state = message.get("state")
        if state in ("starting", "playing", "paused"):
            self.state = state
            self.error = None
        position = _number(message.get("position"), 0, 10**7)
        length = _number(message.get("length"), 0, 10**7)
        self.position = position
        self.length = length if length else None
        volume = _number(message.get("volume"), 0, 100)
        if volume is not None:
            self.volume = round(volume)
        if message.get("front") in ("video", "dock"):
            self.front = message["front"]
        self.seekable = message.get("seekable") is not False
        if self.state == "playing":
            self._cancel_watchdog()
        self._changed()

    def _ended(self, message: dict[str, Any]) -> None:
        reason = message.get("error")
        self._cancel_watchdog()
        self.front = "dock"
        if isinstance(reason, str) and reason:
            self.state, self.error = "error", reason[:420]
        else:
            self.state, self.error, self.media = "idle", None, None
            self.position = self.length = None
        self._changed()

    def _fail(self, text: str) -> None:
        self._cancel_watchdog()
        self.state, self.error, self.front = "error", text, "dock"
        self.position = self.length = None

    # --- ce que demande l'écran -------------------------------------------------------------------

    def _require_helper(self) -> HelperConn:
        if self.helper is None:
            raise PlayerError(
                "L'écran du dock n'a pas d'assistant de lecture (il se lance avec l'écran) : "
                "redémarre le dock ou le Pi, ou lance « mydock-dock doctor » pour voir ce qui manque."
            )
        return self.helper

    def play(
        self, media: dict[str, Any], url: str, *, secrets: dict[str, str] | None = None
    ) -> None:
        """Lance `url` sur l'écran du dock. `media` : de quoi l'afficher (kind, title, subtitle, live)."""
        helper = self._require_helper()
        if not helper.info.get("vlc", False):
            raise PlayerError(
                "VLC est absent du Pi : sudo apt install vlc-bin vlc-plugin-base vlc-plugin-video-output."
            )
        if not helper.info.get("touch", False):
            raise PlayerError(
                "Il manque swayidle, ou wlrctl (ou xdotool), pour revenir au dock d'un toucher : "
                "sudo apt install swayidle wlrctl (la vidéo plein écran garderait l'écran)."
            )
        self.job += 1
        self.media = {k: media.get(k) for k in ("kind", "title", "subtitle", "live")}
        self.state, self.error = "starting", None
        self.position = self.length = None
        self.seekable = True
        self.front = "video"
        self._started_at = self._clock()
        helper.send(
            {
                "t": "cmd",
                "name": "play",
                "job": self.job,
                "url": url,
                "live": bool(media.get("live")),
                "volume": self.volume,
                # Où est le fichier d'un partage (jamais le mot de passe : voir `secrets`) : de quoi le lire par smbclient.
                **({"smb": media["smb"]} if isinstance(media.get("smb"), dict) else {}),
                **(secrets or {}),
            }
        )
        self._arm_watchdog(self.job)
        self._changed()

    def command(self, name: str, **params: Any) -> None:
        """Une commande de télécommande (pause, resume, toggle, seek, volume, stop, front)."""
        helper = self._require_helper()
        if name == "volume":
            value = _number(params.get("value"), 0, 100)
            if value is None:
                raise PlayerError("Volume invalide.")
            self.volume = round(value)
            params = {"value": self.volume}
        elif name == "seek":
            delta = _number(params.get("delta"), -86_400, 86_400)
            if delta is None:
                raise PlayerError("Déplacement invalide.")
            params = {"delta": delta}
        elif name == "front":
            if params.get("to") not in ("video", "dock"):
                raise PlayerError("Destination invalide.")
            if self.active and params["to"] == "video":
                self.front = "video"
            elif params["to"] == "dock":
                self.front = "dock"
        elif name == "stop":
            self._cancel_watchdog()
            if self.state in ("starting", "playing", "paused"):
                self.state, self.error, self.media = "idle", None, None
                self.position = self.length = None
                self.front = "dock"
        elif name not in ("pause", "resume", "toggle"):
            raise PlayerError("Commande inconnue.")
        helper.send({"t": "cmd", "name": name, "job": self.job, **params})
        self._changed()

    def reset_error(self) -> None:
        if self.state == "error":
            self.state, self.error, self.media = "idle", None, None
            self._changed()

    # --- veille sur un démarrage qui n'aboutit pas ---------------------------------------------------

    def _arm_watchdog(self, job: int) -> None:
        self._cancel_watchdog()
        with contextlib.suppress(RuntimeError):  # pas de boucle d'événements : les tests synchrones
            self._watchdog = asyncio.get_running_loop().create_task(self._watch(job))

    def _cancel_watchdog(self) -> None:
        if self._watchdog is not None and self._watchdog is not asyncio.current_task():
            self._watchdog.cancel()
        self._watchdog = None

    async def _watch(self, job: int) -> None:
        await asyncio.sleep(START_TIMEOUT)
        if self.job == job and self.state == "starting":
            if self.helper is not None:
                self.helper.send({"t": "cmd", "name": "stop", "job": job})
            self._fail("La lecture n'a pas démarré (la chaîne ou le fichier ne répond pas).")
            self._changed()

    def shutdown(self) -> None:
        self._cancel_watchdog()
        if self.helper is not None:
            self.helper.close()
