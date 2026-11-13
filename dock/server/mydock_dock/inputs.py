"""Ce que l'assistant lit directement sur les périphériques de saisie (/dev/input) pendant un jeu : l'écran tactile et les manettes.

Pendant un jeu, MAME est devant : le navigateur du dock ne voit plus rien, et le compositeur n'est pas fait pour dire
« quelqu'un a touché l'écran » à un programme tiers sans le déranger. On lit donc les évènements du noyau (evdev) :

  * **un toucher de l'écran tactile** quitte le jeu (le toucher qui a lancé le jeu, et celui qui le suit de près, ne
    comptent pas : voir `arm`) ;
  * **Select + Start tenus deux secondes** sur une manette quittent le jeu (même chemin que le bouton physique).

Seuls les écrans tactiles et les manettes sont ouverts, jamais un clavier : l'assistant ne lit pas ce qui se tape.
L'utilisateur du kiosque doit être dans le groupe `input` (le paquet l'y met ; c'est aussi ce qui laisse MAME lire les manettes).

Les périphériques sont reconnus d'après `/proc/bus/input/devices` (lisible par tous), puis ouverts en lecture seule ; une manette
Bluetooth qui se connecte pendant le jeu est reprise au passage suivant (toutes les deux secondes).
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
import re
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("mydock.player")

DEVICES_FILE = "/proc/bus/input/devices"
EV_KEY, EV_ABS = 0x01, 0x03
BTN_SOUTH, BTN_SELECT, BTN_START, BTN_TOUCH = 0x130, 0x13A, 0x13B, 0x14A
BUS_USB, BUS_BLUETOOTH = 0x03, 0x05  # « I: Bus=… » de /proc/bus/input/devices
INPUT_PROP_POINTER = 0  # un pavé tactile (jamais un écran tactile, qui est « direct »)
ABS_MT_TRACKING_ID = 0x39
EVENT = struct.Struct(
    "@llHHi"
)  # struct input_event : l'heure (2 longs), le type, le code, la valeur
COMBO_SECONDS = 2.0
RESCAN_SECONDS = 2.0
READ_EVENTS = 64
MAX_DEVICES = 12


@dataclass(frozen=True)
class Device:
    name: str
    path: str  # /dev/input/eventN
    kind: str  # « touch » ou « pad »
    bus: int = 0  # BUS_USB (manette filaire), BUS_BLUETOOTH…


def _bits(text: str) -> list[int]:
    words: list[int] = []
    for word in text.split():
        try:
            words.append(int(word, 16))
        except ValueError:
            return []
    return words


def has_bit(words: list[int], index: int, word_bits: int = struct.calcsize("P") * 8) -> bool:
    """Le bit `index` d'une liste de mots « B: KEY=… » de /proc/bus/input/devices (le mot de poids fort en premier)."""
    position = index // word_bits
    if position >= len(words):
        return False
    return bool(words[-(position + 1)] >> (index % word_bits) & 1)


def parse_devices(text: str, word_bits: int = struct.calcsize("P") * 8) -> list[Device]:
    """Les écrans tactiles et les manettes de `/proc/bus/input/devices`."""
    found: list[Device] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        name, handlers, bus = "", "", 0
        key: list[int] = []
        abs_bits: list[int] = []
        prop: list[int] = []
        for line in block.splitlines():
            if line.startswith("I: "):
                match = re.search(r"\bBus=([0-9a-fA-F]+)", line)
                bus = int(match.group(1), 16) if match else 0
            elif line.startswith("B: PROP="):
                prop = _bits(line.partition("=")[2])
            elif line.startswith("N: Name="):
                name = line.partition("=")[2].strip().strip('"')
            elif line.startswith("H: Handlers="):
                handlers = line.partition("=")[2]
            elif line.startswith("B: KEY="):
                key = _bits(line.partition("=")[2])
            elif line.startswith("B: ABS="):
                abs_bits = _bits(line.partition("=")[2])
        event = re.search(r"\bevent(\d+)\b", handlers)
        if event is None or not key:
            continue
        path = f"/dev/input/event{event.group(1)}"
        if has_bit(key, BTN_TOUCH, word_bits) and (
            has_bit(abs_bits, 0x00, word_bits) or has_bit(abs_bits, 0x35, word_bits)
        ):
            # BTN_TOUCH et une position : un écran tactile... ou le pavé tactile d'une manette Sony, qui n'est pas l'écran :
            # il se reconnaît à sa propriété « pointeur » (un écran est « direct ») et à son nom.
            if has_bit(prop, INPUT_PROP_POINTER, word_bits) or "touchpad" in name.lower():
                continue
            found.append(Device(name, path, "touch", bus))
        elif has_bit(key, BTN_SOUTH, word_bits) or (
            has_bit(key, BTN_SELECT, word_bits) and has_bit(key, BTN_START, word_bits)
        ):
            found.append(Device(name, path, "pad", bus))
    return found


@dataclass
class _Open:
    device: Device
    fd: int
    touching: bool = False
    held: set[int] = field(default_factory=set)
    buffer: bytes = b""


class InputWatcher:
    """Surveille les écrans tactiles et les manettes : `on_touch()` à chaque toucher armé, `on_combo()` après Select+Start tenus."""

    def __init__(
        self,
        *,
        on_touch: Callable[[], Any],
        on_combo: Callable[[], Any],
        combo_seconds: float = COMBO_SECONDS,
        rescan_seconds: float = RESCAN_SECONDS,
        devices_file: str = DEVICES_FILE,
        opener: Callable[[str], int] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_touch, self._on_combo = on_touch, on_combo
        self._combo_seconds, self._rescan = combo_seconds, rescan_seconds
        self._devices_file = devices_file
        self._opener = opener or (
            lambda path: os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        )
        self._clock = clock
        self._open: dict[str, _Open] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._rescan_handle: asyncio.TimerHandle | None = None
        self._combo_handle: asyncio.TimerHandle | None = None
        self._armed_at = float("inf")
        self.denied: list[str] = []
        self.touch_seen = False
        self.running = False

    # --- le cycle -------------------------------------------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()
        self.running = True
        self._scan()

    def stop(self) -> None:
        self.running = False
        self._armed_at = float("inf")
        if self._rescan_handle is not None:
            self._rescan_handle.cancel()
            self._rescan_handle = None
        self._cancel_combo()
        for entry in list(self._open.values()):
            self._close(entry)
        self.touch_seen = False
        self.denied = []  # le prochain jeu réessaie : un groupe ajouté entre-temps est pris en compte

    def arm(self, after: float) -> None:
        """Les touchers ne comptent qu'à partir de `after` secondes d'ici (le doigt qui vient de lancer le jeu est encore là)."""
        self._armed_at = self._clock() + after

    def _close(self, entry: _Open) -> None:
        self._open.pop(entry.device.path, None)
        if self._loop is not None:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(entry.fd)
        with contextlib.suppress(OSError):
            os.close(entry.fd)
        self._cancel_combo()
        self.touch_seen = any(e.device.kind == "touch" for e in self._open.values())

    # --- les périphériques ------------------------------------------------------------------------------------

    def _scan(self) -> None:
        if not self.running or self._loop is None:
            return
        try:
            with open(self._devices_file, encoding="utf-8", errors="replace") as handle:
                devices = parse_devices(handle.read())
        except OSError:
            devices = []
        for device in devices[:MAX_DEVICES]:
            if device.path in self._open or device.path in self.denied:
                continue
            try:
                fd = self._opener(device.path)
            except PermissionError:
                self.denied.append(device.path)
                log.warning(
                    "Saisie : %s (%s) n'est pas lisible — l'utilisateur de l'écran doit être dans le groupe input.",
                    device.path, device.name,
                )  # fmt: skip
                continue
            except OSError:
                continue
            entry = _Open(device, fd)
            self._open[device.path] = entry
            self._loop.add_reader(fd, self._readable, entry)
            if device.kind == "touch":
                self.touch_seen = True
            log.info("Saisie : %s lu (%s, %s)", device.path, device.name, device.kind)
        self._rescan_handle = self._loop.call_later(self._rescan, self._scan)

    def _readable(self, entry: _Open) -> None:
        try:
            data = os.read(entry.fd, EVENT.size * READ_EVENTS)
        except BlockingIOError:
            return
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EINTR):
                self._close(entry)  # le périphérique a disparu (manette éteinte, écran débranché)
            return
        if not data:
            self._close(entry)
            return
        self.feed(entry, data)

    # --- les évènements ---------------------------------------------------------------------------------------

    def feed(self, entry: _Open, data: bytes) -> None:
        """Interprète des `input_event` bruts (rendu public pour les essais)."""
        buffer = entry.buffer + data
        usable = len(buffer) - len(buffer) % EVENT.size
        entry.buffer = buffer[usable:]
        for offset in range(0, usable, EVENT.size):
            _sec, _usec, kind, code, value = EVENT.unpack_from(buffer, offset)
            if entry.device.kind == "touch":
                self._touch_event(entry, kind, code, value)
            else:
                self._pad_event(entry, kind, code, value)

    def _touch_event(self, entry: _Open, kind: int, code: int, value: int) -> None:
        down: bool | None = None
        if kind == EV_KEY and code == BTN_TOUCH:
            down = value != 0
        elif kind == EV_ABS and code == ABS_MT_TRACKING_ID:
            down = value >= 0  # -1 : le doigt se lève
        if down is None:
            return
        if down and not entry.touching:
            entry.touching = True
            if self._clock() >= self._armed_at:
                self._on_touch()
        elif not down:
            entry.touching = False

    def _pad_event(self, entry: _Open, kind: int, code: int, value: int) -> None:
        if kind != EV_KEY or code not in (BTN_SELECT, BTN_START):
            return
        if value:
            entry.held.add(code)
        else:
            entry.held.discard(code)
        if {BTN_SELECT, BTN_START} <= entry.held:
            if (
                self._combo_handle is None
                and self._loop is not None
                and self._clock() >= self._armed_at
            ):
                self._combo_handle = self._loop.call_later(
                    self._combo_seconds, self._combo_done, entry
                )
        else:
            self._cancel_combo()

    def _combo_done(self, entry: _Open) -> None:
        self._combo_handle = None
        if {BTN_SELECT, BTN_START} <= entry.held:
            entry.held.clear()
            self._on_combo()

    def _cancel_combo(self) -> None:
        if self._combo_handle is not None:
            self._combo_handle.cancel()
            self._combo_handle = None

    # --- ce qu'on sait dire ---------------------------------------------------------------------------------

    def summary(self) -> str:
        kinds = [e.device.kind for e in self._open.values()]
        text = f"{kinds.count('touch')} écran tactile, {kinds.count('pad')} manette"
        if self.denied:
            text += f" ; illisibles (groupe input ?) : {', '.join(self.denied)}"
        return text
