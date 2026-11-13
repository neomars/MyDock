"""Retrogame en démonstration : de faux jeux (avec de vraies images), de fausses manettes — sans MAME, sans Bluetooth.

Pour les captures d'écran, les essais de bout en bout et pour voir l'interface sans matériel : `mydock-dock run --demo-retro`
(la pile de développement le fait). La liste est celle de `DEMO_GAMES` ; les captures sont dessinées ici (un petit motif
par jeu, enregistré en PNG dans le dossier des miniatures du dock). Trois manettes : une connectée avec sa batterie, une
associée mais éteinte, une troisième que la « recherche » trouve au bout de deux secondes. L'association, l'oubli et la
recherche se déroulent comme avec BlueZ, en mémoire.
"""

from __future__ import annotations

import asyncio
import colorsys
import contextlib
import random
import struct
import time
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mydock_dock.bluetooth import PadsError, valid_mac
from mydock_dock.inputs import BUS_USB, Device
from mydock_dock.retrogame import Game, GameLibrary

DEMO_GAMES = (
    Game("pacman", "Pac-Man (Midway)", "1980", "Namco (Midway license)", "good"),
    Game("galaga", "Galaga (Namco rev. B)", "1981", "Namco", "good"),
    Game("dkong", "Donkey Kong (US set 1)", "1981", "Nintendo of America", "good"),
    Game("mspacman", "Ms. Pac-Man", "1981", "Midway", "good"),
    Game("invaders", "Space Invaders", "1978", "Taito", "good"),
    Game("asteroid", "Asteroids (rev 4)", "1979", "Atari", "good"),
    Game("frogger", "Frogger", "1981", "Konami", "good"),
    Game("defender", "Defender (Red label)", "1980", "Williams", "good"),
    Game("1942", "1942 (Revision B)", "1984", "Capcom", "good"),
    Game("bublbobl", "Bubble Bobble", "1986", "Taito Corporation", "good"),
    Game(
        "sf2", "Street Fighter II: The World Warrior (World 910522)", "1991", "Capcom", "imperfect"
    ),
    Game("mslug", "Metal Slug - Super Vehicle-001", "1996", "Nazca", "preliminary"),
)
#: Ceux-là n'ont pas de capture : l'interface montre alors une manette à la place.
WITHOUT_SNAP = frozenset({"defender", "mslug"})

# --- les captures ------------------------------------------------------------------------------------------------------


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + row for row in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def draw_snap(name: str, width: int = 224, height: int = 168) -> bytes:
    """Une « capture » : un fond dégradé, un sprite symétrique façon arcade, des lignes de balayage."""
    rng = random.Random(name)
    hue = rng.random()
    top = [int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.65, 0.34)]
    bottom = [int(c * 255) for c in colorsys.hsv_to_rgb((hue + 0.08) % 1, 0.7, 0.12)]
    ink = [int(c * 255) for c in colorsys.hsv_to_rgb((hue + 0.5) % 1, 0.75, 0.98)]
    cell = 14
    sprite = []
    for _ in range(7):
        half = [rng.random() < 0.55 for _ in range(4)]
        sprite.append(half + half[2::-1])  # 7 colonnes, symétriques
    left, topmost = (width - 7 * cell) // 2, (height - 7 * cell) // 2
    rows = []
    for y in range(height):
        mix = y / (height - 1)
        base = [round(top[i] + (bottom[i] - top[i]) * mix) for i in range(3)]
        row = bytearray()
        for x in range(width):
            col, line = (x - left) // cell, (y - topmost) // cell
            lit = (
                0 <= col < 7 and 0 <= line < 7 and x >= left and y >= topmost and sprite[line][col]
            )
            pixel = ink if lit else base
            if y % 3 == 2:  # lignes de balayage
                pixel = [c * 3 // 4 for c in pixel]
            row.extend(pixel)
        rows.append(bytes(row))
    return _png(width, height, rows)


# --- la liste -------------------------------------------------------------------------------------------------------------


class DemoGameLibrary(GameLibrary):
    """La liste de démonstration : elle ne lit aucun dossier de ROMs et ne lance pas MAME."""

    def __init__(
        self,
        roms_dir: Callable[[], Path],
        retro_dir: Path,
        *,
        on_change: Callable[[], None] = lambda: None,
    ) -> None:
        super().__init__(roms_dir, retro_dir, on_change=on_change, mame=lambda: "/usr/games/mame")
        with contextlib.suppress(OSError):
            roms_dir().mkdir(parents=True, exist_ok=True)
        self._write_snaps()

    def _write_snaps(self) -> None:
        with contextlib.suppress(OSError):
            self.snap_dir.mkdir(parents=True, exist_ok=True)
            for game in DEMO_GAMES:
                path = self.snap_dir / f"{game.name}.png"
                if game.name not in WITHOUT_SNAP and not path.exists():
                    path.write_bytes(draw_snap(game.name))

    def games(self) -> list[Game]:
        return list(DEMO_GAMES)

    def is_stale(self) -> bool:
        return False

    def refresh(self, *, force: bool = False) -> None:
        return None


# --- les manettes ----------------------------------------------------------------------------------------------------------

XBOX = "AA:BB:CC:00:00:01"
DUALSENSE = "AA:BB:CC:00:00:02"
EIGHTBITDO = "AA:BB:CC:00:00:03"


class DemoPads:
    """Les manettes de démonstration, avec la même surface que `PadsManager`."""

    def __init__(
        self,
        on_change: Callable[[], None] = lambda: None,
        *,
        scan_seconds: float = 30.0,
        find_after: float = 2.0,
        step_seconds: float = 0.8,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._on_change = on_change
        self._scan_seconds, self._find_after, self._step, self._wall = (
            scan_seconds, find_after, step_seconds, wall,
        )  # fmt: skip
        self._devices: dict[str, dict[str, Any]] = {
            XBOX: {"name": "Xbox Wireless Controller", "paired": True, "connected": True, "battery": 78},
            DUALSENSE: {"name": "DualSense Wireless Controller", "paired": True, "connected": False, "battery": None},
        }  # fmt: skip
        self._hidden: dict[str, dict[str, Any]] = {
            EIGHTBITDO: {
                "name": "8BitDo Pro 2",
                "paired": False,
                "connected": False,
                "battery": None,
            }
        }
        self._scan_until = 0.0
        self._pairing: dict[str, str] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    # --- cycle de vie --------------------------------------------------------------------------------------

    async def start(self) -> None:
        self._on_change()

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def reload(self) -> None:
        self._on_change()

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # --- ce que voit l'écran ----------------------------------------------------------------------------------

    def _scanning(self) -> bool:
        return self._wall() < self._scan_until

    def snapshot(self) -> dict[str, Any]:
        devices = [
            {"id": mac, **device}
            for mac, device in self._devices.items()
            if device["paired"] or self._scanning()
        ]
        devices.sort(key=lambda d: (not d["connected"], not d["paired"], d["name"].casefold()))
        return {
            "available": True,
            "reason": None,
            "powered": True,
            "blocked": False,
            "scanning": self._scanning(),
            "scan_until": int(self._scan_until * 1000) if self._scanning() else None,
            "pairing": dict(self._pairing) if self._pairing else None,
            "devices": devices,
        }

    def connected(self) -> list[dict[str, Any]]:
        return [d for d in self.snapshot()["devices"] if d["connected"]]

    # --- la recherche ---------------------------------------------------------------------------------------

    async def scan(self) -> None:
        self._scan_until = self._wall() + self._scan_seconds
        self._on_change()
        self._spawn(self._discover())
        self._spawn(self._end_scan(self._scan_seconds))

    async def _discover(self) -> None:
        await asyncio.sleep(self._find_after)
        for mac, device in list(self._hidden.items()):
            self._devices[mac] = device
            del self._hidden[mac]
        self._on_change()

    async def _end_scan(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self.stop_scan()

    async def stop_scan(self) -> None:
        if self._scan_until:
            self._scan_until = 0.0
            self._on_change()

    # --- association et oubli -----------------------------------------------------------------------------------

    def _known(self, mac: object) -> tuple[str, str]:
        address = valid_mac(mac)
        if address is None or address not in self._devices:
            raise PadsError(
                "Cette manette n'est pas (ou plus) dans la liste : relance la recherche."
            )
        return address, f"/demo/{address}"

    def _set_pairing(self, mac: str, state: str, message: str) -> None:
        self._pairing = {"id": mac, "state": state, "message": message}
        self._on_change()

    async def pair(self, mac: object) -> None:
        address, _path = self._known(mac)
        await self.stop_scan()
        self._set_pairing(address, "pairing", "Association en cours…")
        await asyncio.sleep(self._step)
        self._set_pairing(address, "connecting", "Connexion de la manette…")
        await asyncio.sleep(self._step)
        self._devices[address].update(paired=True, connected=True, battery=64)
        self._set_pairing(address, "done", "Manette associée et connectée.")
        self._spawn(self._clear(address))

    async def _clear(self, mac: str) -> None:
        await asyncio.sleep(6.0)
        if self._pairing and self._pairing["id"] == mac:
            self._pairing = None
            self._on_change()

    async def forget(self, mac: object) -> None:
        address, _path = self._known(mac)
        device = self._devices.pop(address)
        if address == EIGHTBITDO:  # elle se retrouvera à la prochaine recherche
            self._hidden[address] = {**device, "paired": False, "connected": False, "battery": None}
        self._on_change()


# --- les manettes USB de la démonstration -------------------------------------------------------------------------------------


def demo_usb_source(state_dir: Path) -> Callable[[], list[Device]]:
    """Une manette USB « branchée » : un nom par ligne dans `<état>/demo-usb-pads.txt` (le créer, c'est brancher ; l'effacer, débrancher)."""
    path = state_dir / "demo-usb-pads.txt"

    def source() -> list[Device]:
        try:
            names = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except OSError:
            return []
        return [
            Device(name[:60], f"/dev/input/event{90 + n}", "pad", BUS_USB)
            for n, name in enumerate(names[:4])
        ]

    return source
