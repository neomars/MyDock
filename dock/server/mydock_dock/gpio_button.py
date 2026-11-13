"""Le bouton physique du dock : un interrupteur entre une broche GPIO et la masse, lu par libgpiod.

Câblage par défaut : GPIO17 (broche physique 11) et la masse voisine (broche 9, ou 6, 14, 20…). La résistance de rappel
interne est activée : le bouton ferme le circuit vers la masse, la broche passe de 1 à 0 (front descendant). Aucune
résistance à ajouter. Si la broche change dans les Réglages, le dock la dit à l'assistant (`configure`).

C'est l'**assistant de session** qui le lit, pas le serveur du dock : il tourne dans la session de l'écran, il n'est pas
bloqué par un Chromium qui ne répond plus, et il vit même quand le dock redémarre. Il faut seulement que son
utilisateur soit dans le groupe `gpio` (le paquet l'y met).

libgpiod (le paquet `python3-libgpiod`), jamais RPi.GPIO : celui-ci ne fonctionne plus sur le Pi 5 et n'est plus maintenu.
Les deux versions de l'interface Python sont comprises (v2 : Debian Trixie ; v1 : Bookworm et Ubuntu 24.04).
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Protocol

log = logging.getLogger("mydock.player")

DEFAULT_PIN = 17
CONSUMER = "mydock-button"
DEBOUNCE = 0.03  # secondes, au niveau du noyau (v2)
MIN_INTERVAL = (
    0.35  # secondes entre deux appuis pris en compte (anti-rebond logiciel, et v1 sans anti-rebond)
)

#: La broche physique du connecteur de 40 broches pour chaque GPIO (BCM) qu'on peut utiliser.
PHYSICAL_PIN = {
    2: 3, 3: 5, 4: 7, 14: 8, 15: 10, 17: 11, 18: 12, 27: 13, 22: 15, 23: 16, 24: 18, 10: 19, 9: 21,
    25: 22, 11: 23, 8: 24, 7: 26, 5: 29, 6: 31, 12: 32, 13: 33, 19: 35, 16: 36, 26: 37, 20: 38, 21: 40,
}  # fmt: skip


def describe_pin(pin: int) -> str:
    physical = PHYSICAL_PIN.get(pin)
    return f"GPIO{pin}" + (f" (broche {physical})" if physical else "")


class GpioError(Exception):
    """Pourquoi le bouton n'est pas utilisable, en une phrase pour l'écran et le diagnostic."""


class Backend(Protocol):
    """Ce que le bouton demande à libgpiod : ouvrir une ligne en front descendant, lire les fronts, fermer."""

    def open(self, pin: int) -> int:
        """Ouvre la ligne ; rend le descripteur à surveiller. Lève `GpioError`."""

    def read(self) -> int:
        """Lit les fronts en attente ; rend leur nombre."""

    def close(self) -> None: ...


def _error_text(pin: int, exc: BaseException) -> str:
    text = str(exc)
    low = text.lower()
    who = describe_pin(pin)
    if isinstance(exc, PermissionError) or "permission" in low or "denied" in low:
        return f"{who} : accès refusé — l'utilisateur de l'écran doit être dans le groupe gpio (sudo usermod -aG gpio <utilisateur>, puis nouvelle session)."
    if "busy" in low or "in use" in low or "resource" in low:
        return f"{who} : déjà utilisée par un autre programme."
    return f"{who} : {text or exc.__class__.__name__}"


class LibgpiodBackend:
    """libgpiod, en interface v2 (Trixie) ou v1 (Bookworm, Ubuntu 24.04)."""

    def __init__(self, module: Any = None) -> None:
        if module is None:
            try:
                import gpiod as module  # type: ignore[import-not-found,no-redef]
            except ImportError:
                module = None
        self._gpiod = module
        self._request: Any = None  # v2 : la demande de lignes
        self._line: Any = None  # v1 : la ligne
        self._chip: Any = None

    @property
    def available(self) -> bool:
        return self._gpiod is not None

    @property
    def is_v2(self) -> bool:
        return hasattr(self._gpiod, "request_lines")

    def _chips(self) -> list[str]:
        return sorted(glob.glob("/dev/gpiochip*"))

    def _label(self, path: str) -> str:
        gpiod = self._gpiod
        if self.is_v2:
            with gpiod.Chip(path) as chip:
                return str(chip.get_info().label)
        chip = gpiod.Chip(path)
        try:
            return str(chip.label())
        finally:
            chip.close()

    def find_chip(self) -> str:
        """La puce du connecteur de 40 broches : « pinctrl-… » (Pi 4 : bcm2711 ; Pi 5 : rp1 ; Pi 3 : bcm2835)."""
        paths = self._chips()
        if not paths:
            raise GpioError("aucune puce GPIO (/dev/gpiochip*) : le noyau n'en propose pas ici.")
        labels: list[str] = []
        for path in paths:
            try:
                label = self._label(path)
            except (OSError, RuntimeError, ValueError) as exc:
                if isinstance(exc, PermissionError):
                    raise
                continue
            labels.append(f"{path}={label}")
            if label.startswith("pinctrl-"):
                return path
        raise GpioError(f"aucune puce « pinctrl-… » parmi {', '.join(labels) or 'les puces GPIO'}.")

    def open(self, pin: int) -> int:
        gpiod = self._gpiod
        if gpiod is None:
            raise GpioError(
                f"{describe_pin(pin)} : libgpiod est absente (sudo apt install python3-libgpiod)."
            )
        try:
            path = self.find_chip()
            if self.is_v2:
                from gpiod.line import Bias, Direction, Edge  # type: ignore[import-not-found]

                self._request = gpiod.request_lines(
                    path,
                    consumer=CONSUMER,
                    config={
                        pin: gpiod.LineSettings(
                            direction=Direction.INPUT,
                            bias=Bias.PULL_UP,
                            edge_detection=Edge.FALLING,
                            debounce_period=timedelta(seconds=DEBOUNCE),
                        )
                    },
                )
                return int(self._request.fd)
            self._chip = gpiod.Chip(path)
            self._line = self._chip.get_line(pin)
            self._line.request(
                consumer=CONSUMER,
                type=gpiod.LINE_REQ_EV_FALLING_EDGE,
                flags=getattr(gpiod, "LINE_REQ_FLAG_BIAS_PULL_UP", 0),
            )
            return int(self._line.event_get_fd())
        except GpioError:
            self.close()
            raise
        except (
            Exception
        ) as exc:  # libgpiod lève OSError, PermissionError, TypeError, ValueError selon la version
            self.close()
            raise GpioError(_error_text(pin, exc)) from exc

    def read(self) -> int:
        if self._request is not None:
            events = self._request.read_edge_events()
            return len(events)
        if self._line is not None:
            self._line.event_read()
            return 1
        return 0

    def close(self) -> None:
        for holder, method in (
            (self._request, "release"),
            (self._line, "release"),
            (self._chip, "close"),
        ):
            if holder is not None:
                with contextlib.suppress(Exception):
                    getattr(holder, method)()
        self._request = self._line = self._chip = None


class GpioButton:
    """Le bouton : appelle `on_press` à chaque appui (anti-rebond compris), tant que la ligne est ouverte."""

    def __init__(
        self,
        on_press: Callable[[], Any],
        *,
        backend: Callable[[], Backend] = LibgpiodBackend,
        min_interval: float = MIN_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        on_status: Callable[[str | None], Any] = lambda _status: None,
    ) -> None:
        self._on_press = on_press
        self._backend_factory = backend
        self._min_interval = min_interval
        self._clock = clock
        self._on_status = on_status
        self._backend: Backend | None = None
        self._fd: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last = float("-inf")
        self.pin: int | None = None
        self.status: str | None = None
        self.ready = False
        self.presses = 0

    def configure(self, loop: asyncio.AbstractEventLoop, pin: int) -> bool:
        """(Re)ouvre la ligne `pin` ; rend vrai si le bouton est prêt, sinon `status` dit pourquoi."""
        self.stop()
        self._loop, self.pin = loop, pin
        backend = self._backend_factory()
        try:
            fd = backend.open(pin)
            loop.add_reader(fd, self._readable)
        except GpioError as exc:
            backend.close()
            self._set(str(exc), ready=False)
            return False
        except (OSError, ValueError, RuntimeError) as exc:
            backend.close()
            self._set(_error_text(pin, exc), ready=False)
            return False
        self._backend, self._fd = backend, fd
        self._set(f"{describe_pin(pin)} : prêt.", ready=True)
        return True

    def _set(self, status: str, *, ready: bool) -> None:
        changed = status != self.status
        self.status, self.ready = status, ready
        if changed:
            log.info("Bouton physique : %s", status)
            self._on_status(status)

    def _readable(self) -> None:
        backend = self._backend
        if backend is None:
            return
        try:
            edges = backend.read()
        except (OSError, RuntimeError):
            self.stop()
            self._set(
                f"{describe_pin(self.pin or 0)} : lecture impossible, bouton arrêté.", ready=False
            )
            return
        now = self._clock()
        if edges <= 0 or now - self._last < self._min_interval:
            return
        self._last = now
        self.presses += 1
        self._on_press()

    def stop(self) -> None:
        loop, fd, backend = self._loop, self._fd, self._backend
        self._fd = self._backend = None
        if loop is not None and fd is not None:
            with contextlib.suppress(Exception):
                loop.remove_reader(fd)
        if backend is not None:
            backend.close()
        self.ready = False
