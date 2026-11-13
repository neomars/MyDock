"""Les manettes filaires (USB) : branchées, elles sont là — rien à associer, rien à chercher.

Une manette USB n'a pas besoin de BlueZ : le noyau en fait un périphérique d'entrée (`/dev/input/event*`), MAME la lit par SDL, et
l'assistant de l'écran la prend en compte pour « Select + Start tenus ». Ce module sert seulement à **la montrer** : l'en-tête de la
liste des jeux dit « Manette connectée » et l'écran « Manettes » la liste, comme celles du Bluetooth (sans batterie ni « Oublier »).

Elles se reconnaissent dans `/proc/bus/input/devices` (lisible par tous, donc par le serveur du dock malgré son bac à sable) : un
périphérique de manette dont le bus est l'USB. Une manette Bluetooth y figure aussi (bus Bluetooth) : on ne la compte pas deux fois,
BlueZ la connaît déjà. Un adaptateur USB sans fil (comme celui de certaines 8BitDo) se comporte comme une manette filaire pour le
noyau : il est montré de même.

Le fichier est relu toutes les deux secondes (quelques centaines d'octets) ; on ne prévient l'écran que si la liste a changé.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mydock_dock.inputs import BUS_USB, DEVICES_FILE, Device, parse_devices

log = logging.getLogger(__name__)

INTERVAL = 2.0


def usb_pads(devices: list[Device]) -> list[dict[str, Any]]:
    """Les manettes USB d'une liste de périphériques, au format du thème « pads » (jamais de batterie : on ne la connaît pas)."""
    found = []
    for device in devices:
        if device.kind != "pad" or device.bus != BUS_USB:
            continue
        found.append(
            {
                "id": f"usb:{device.path.rsplit('/', 1)[-1]}",  # « usb:event6 » : jamais une adresse Bluetooth
                "name": device.name[:60] or "Manette USB",
                "paired": False,
                "connected": True,
                "battery": None,
                "wired": True,
            }
        )
    return sorted(found, key=lambda d: (d["name"].casefold(), d["id"]))


def read_proc_devices(path: str = DEVICES_FILE) -> list[Device]:
    try:
        return parse_devices(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []


class WiredPads:
    """Suit les manettes USB branchées et prévient quand la liste change."""

    def __init__(
        self,
        on_change: Callable[[], None] = lambda: None,
        *,
        source: Callable[[], list[Device]] = read_proc_devices,
        interval: float = INTERVAL,
    ) -> None:
        self._on_change, self._source, self._interval = on_change, source, interval
        self._pads: list[dict[str, Any]] = []
        self._task: asyncio.Task[None] | None = None

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(pad) for pad in self._pads]

    def refresh(self) -> bool:
        """Relit la liste ; vrai si elle a changé (et alors l'écran est prévenu)."""
        try:
            pads = usb_pads(self._source())
        except Exception:  # une lecture ratée ne doit jamais arrêter le suivi
            log.debug("Manettes USB : lecture impossible", exc_info=True)
            return False
        if pads == self._pads:
            return False
        for pad in pads:
            if pad not in self._pads:
                log.info("Manette USB branchée : %s", pad["name"])
        for pad in self._pads:
            if pad not in pads:
                log.info("Manette USB débranchée : %s", pad["name"])
        self._pads = pads
        self._on_change()
        return True

    async def start(self) -> None:
        self.refresh()
        if self._task is None:
            self._task = asyncio.create_task(self._poll(), name="wired-pads")

    async def _poll(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            self.refresh()

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
