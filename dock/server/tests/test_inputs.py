"""La lecture directe de l'écran tactile et des manettes pendant un jeu : reconnaissance, toucher armé, Select + Start tenus."""

import asyncio
import contextlib
import errno
import os
import struct

import pytest

from mydock_dock import inputs
from mydock_dock.inputs import (
    ABS_MT_TRACKING_ID,
    BTN_SELECT,
    BTN_SOUTH,
    BTN_START,
    BTN_TOUCH,
    EV_ABS,
    EV_KEY,
    EVENT,
    InputWatcher,
    has_bit,
    parse_devices,
)

BITS = 64


def bitmap(*codes: int, bits: int = BITS) -> str:
    """Un « B: KEY=… » de /proc/bus/input/devices : les mots hexadécimaux, le plus fort d'abord."""
    top = max(codes) // bits if codes else 0
    words = [0] * (top + 1)
    for code in codes:
        words[code // bits] |= 1 << (code % bits)
    return " ".join(f"{word:x}" for word in reversed(words))


def block(name, handlers, key=(), absolute=()):
    lines = [
        "I: Bus=0003 Vendor=222a Product=0001 Version=0110",
        f'N: Name="{name}"',
        "P: Phys=usb-0000:01:00.0-1.2/input0",
        f"H: Handlers={handlers}",
        "B: PROP=0",
        "B: EV=1b",
    ]
    if key:
        lines.append(f"B: KEY={bitmap(*key)}")
    if absolute:
        lines.append(f"B: ABS={bitmap(*absolute)}")
    return "\n".join(lines)


DEVICES = "\n\n".join(
    [
        block(
            "ILITEK Multi-Touch-V5100",
            "mouse0 event3",
            key=[BTN_TOUCH],
            absolute=[0, 1, 0x35, 0x36, 0x39],
        ),
        block("Logitech USB Keyboard", "sysrq kbd leds event4", key=[30, 31, 32, 57]),
        block("Logitech USB Optical Mouse", "mouse1 event5", key=[0x110, 0x111], absolute=[]),
        block(
            "Xbox Wireless Controller",
            "js0 event6",
            key=[BTN_SOUTH, 0x131, BTN_SELECT, BTN_START],
            absolute=[0, 1],
        ),
        block("Pad sans A", "event7", key=[BTN_SELECT, BTN_START]),
        block("Pas de poignée", "", key=[BTN_TOUCH]),
    ]
)


def pack(kind, code, value):
    return EVENT.pack(0, 0, kind, code, value)


# --- la reconnaissance ----------------------------------------------------------------------------------------


def test_bit_tests_follow_the_kernel_layout():
    words = [int(w, 16) for w in bitmap(BTN_TOUCH, 3).split()]
    assert has_bit(words, BTN_TOUCH)
    assert has_bit(words, 3)
    assert not has_bit(words, 4)
    assert not has_bit(words, 5000)  # au-delà de la liste
    assert has_bit(
        [int(w, 16) for w in bitmap(BTN_TOUCH, bits=32).split()], BTN_TOUCH, 32
    )  # Pi en 32 bits


def test_only_touchscreens_and_gamepads_are_recognised():
    found = {d.path: (d.name, d.kind) for d in parse_devices(DEVICES)}
    assert found == {
        "/dev/input/event3": ("ILITEK Multi-Touch-V5100", "touch"),
        "/dev/input/event6": ("Xbox Wireless Controller", "pad"),
        "/dev/input/event7": ("Pad sans A", "pad"),  # Select et Start suffisent
    }


def test_a_garbled_proc_file_gives_nothing_instead_of_crashing():
    assert parse_devices("") == []
    assert parse_devices("N: Name=x\nH: Handlers=event1\nB: KEY=zz zz") == []


# --- l'observation ---------------------------------------------------------------------------------------------


class Rig:
    """Un faux /proc/bus/input/devices, de vrais descripteurs (des tubes) et des évènements écrits dedans."""

    def __init__(self, tmp_path):
        self.file = tmp_path / "devices"
        self.file.write_text(DEVICES)
        self.pipes: dict[str, tuple[int, int]] = {}
        self.denied: set[str] = set()
        self.touches: list[int] = []
        self.combos: list[int] = []
        self.now = 100.0
        self.watcher = InputWatcher(
            on_touch=lambda: self.touches.append(1),
            on_combo=lambda: self.combos.append(1),
            combo_seconds=0.15,
            rescan_seconds=0.05,
            devices_file=str(self.file),
            opener=self.open,
            clock=lambda: self.now,
        )

    def open(self, path):
        if path in self.denied:
            raise PermissionError(path)
        read, write = os.pipe()
        os.set_blocking(read, False)
        self.pipes[path] = (read, write)
        return read

    def send(self, path, *events):
        os.write(self.pipes[path][1], b"".join(pack(*e) for e in events))

    async def settle(self, seconds=0.03):
        await asyncio.sleep(seconds)

    def close(self):
        self.watcher.stop()
        for read, write in self.pipes.values():
            for fd in (read, write):
                with contextlib.suppress(OSError):
                    os.close(fd)


@pytest.fixture
async def rig(tmp_path):
    r = Rig(tmp_path)
    r.watcher.start(asyncio.get_running_loop())
    yield r
    r.close()


TOUCH = "/dev/input/event3"
PAD = "/dev/input/event6"


async def test_it_opens_only_the_touchscreen_and_the_pads(rig):
    assert sorted(rig.pipes) == [TOUCH, PAD, "/dev/input/event7"]
    assert rig.watcher.touch_seen
    assert rig.watcher.summary() == "1 écran tactile, 2 manette"


async def test_a_touch_counts_only_once_armed(rig):
    rig.send(TOUCH, (EV_KEY, BTN_TOUCH, 1), (EV_KEY, BTN_TOUCH, 0))
    await rig.settle()
    assert rig.touches == []  # pas armé : c'est le doigt qui vient de lancer le jeu
    rig.watcher.arm(1.5)
    rig.send(TOUCH, (EV_KEY, BTN_TOUCH, 1), (EV_KEY, BTN_TOUCH, 0))
    await rig.settle()
    assert rig.touches == []  # armé pour dans 1,5 s
    rig.now += 2.0
    rig.send(TOUCH, (EV_KEY, BTN_TOUCH, 1))
    await rig.settle()
    assert rig.touches == [1]


async def test_a_finger_that_stays_down_counts_once(rig):
    rig.watcher.arm(0)
    rig.send(TOUCH, (EV_KEY, BTN_TOUCH, 1), (EV_KEY, BTN_TOUCH, 1), (3, 0, 500), (0, 0, 0))
    await rig.settle()
    assert rig.touches == [1]
    rig.send(TOUCH, (EV_KEY, BTN_TOUCH, 0), (EV_KEY, BTN_TOUCH, 1))
    await rig.settle()
    assert rig.touches == [1, 1]  # levé puis reposé : un second toucher


async def test_multitouch_tracking_ids_count_as_touches(rig):
    rig.watcher.arm(0)
    rig.send(TOUCH, (EV_ABS, ABS_MT_TRACKING_ID, 7), (EV_ABS, ABS_MT_TRACKING_ID, -1))
    await rig.settle()
    assert rig.touches == [1]


async def test_a_half_written_event_waits_for_its_end(rig):
    rig.watcher.arm(0)
    data = pack(EV_KEY, BTN_TOUCH, 1)
    os.write(rig.pipes[TOUCH][1], data[:10])
    await rig.settle()
    assert rig.touches == []
    os.write(rig.pipes[TOUCH][1], data[10:])
    await rig.settle()
    assert rig.touches == [1]


async def test_select_and_start_held_quit(rig):
    rig.watcher.arm(0)
    rig.send(PAD, (EV_KEY, BTN_SELECT, 1), (EV_KEY, BTN_START, 1))
    await rig.settle(0.05)
    assert rig.combos == []
    await rig.settle(0.25)
    assert rig.combos == [1]
    await rig.settle(0.3)
    assert rig.combos == [1]  # pas de rafale tant que les touches restent tenues


async def test_releasing_early_cancels_the_combo(rig):
    rig.watcher.arm(0)
    rig.send(PAD, (EV_KEY, BTN_SELECT, 1), (EV_KEY, BTN_START, 1))
    await rig.settle(0.05)
    rig.send(PAD, (EV_KEY, BTN_START, 0))
    await rig.settle(0.3)
    assert rig.combos == []


async def test_one_button_alone_does_nothing_and_other_keys_are_ignored(rig):
    rig.watcher.arm(0)
    rig.send(PAD, (EV_KEY, BTN_SELECT, 1), (EV_KEY, BTN_SOUTH, 1), (EV_KEY, 0x131, 1))
    await rig.settle(0.3)
    assert rig.combos == []


async def test_the_combo_is_ignored_until_armed(rig):
    rig.send(PAD, (EV_KEY, BTN_SELECT, 1), (EV_KEY, BTN_START, 1))
    await rig.settle(0.3)
    assert rig.combos == []


async def test_a_pad_that_connects_later_is_picked_up(rig, tmp_path):
    rig.file.write_text(
        DEVICES
        + "\n\n"
        + block(
            "8BitDo Pro 2", "js1 event9", key=[BTN_SOUTH, BTN_SELECT, BTN_START], absolute=[0, 1]
        )
    )
    for _ in range(40):
        await rig.settle(0.03)
        if "/dev/input/event9" in rig.pipes:
            break
    assert "/dev/input/event9" in rig.pipes
    rig.watcher.arm(0)
    rig.send("/dev/input/event9", (EV_KEY, BTN_SELECT, 1), (EV_KEY, BTN_START, 1))
    await rig.settle(0.35)
    assert rig.combos == [1]


async def test_a_device_that_vanishes_is_closed(rig):
    rig.file.write_text("\n\n".join(b for b in DEVICES.split("\n\n") if "Xbox" not in b))
    os.close(rig.pipes[PAD][1])  # fin du flux : le périphérique n'est plus là
    await rig.settle(0.15)
    assert PAD not in rig.watcher._open
    rig.watcher.arm(0)
    rig.send(TOUCH, (EV_KEY, BTN_TOUCH, 1))
    await rig.settle()
    assert rig.touches == [1]  # le reste continue de marcher


async def test_a_vanished_touchscreen_is_noticed(rig):
    rig.file.write_text("\n\n".join(b for b in DEVICES.split("\n\n") if "ILITEK" not in b))
    os.close(rig.pipes[TOUCH][1])
    await rig.settle(0.15)
    assert not rig.watcher.touch_seen


async def test_unreadable_devices_are_reported_not_fatal(tmp_path):
    r = Rig(tmp_path)
    r.denied = {TOUCH}
    r.watcher.start(asyncio.get_running_loop())
    try:
        assert not r.watcher.touch_seen
        assert r.watcher.denied == [TOUCH]
        assert "groupe input" in r.watcher.summary()
        assert PAD in r.pipes  # les manettes, elles, sont lues
    finally:
        r.close()


async def test_a_stopped_watcher_forgets_what_it_could_not_open(tmp_path):
    r = Rig(tmp_path)
    r.denied = {TOUCH}
    r.watcher.start(asyncio.get_running_loop())
    try:
        assert r.watcher.denied == [TOUCH]
        r.watcher.stop()
        assert r.watcher.denied == []
        r.denied = set()  # entre deux jeux, le groupe a été ajouté
        r.watcher.start(asyncio.get_running_loop())
        assert r.watcher.touch_seen
    finally:
        r.close()


async def test_stop_closes_everything(rig):
    fds = [read for read, _ in rig.pipes.values()]
    rig.watcher.stop()
    for fd in fds:
        with pytest.raises(OSError, match=r"\[Errno 9\]"):
            os.fstat(fd)
    assert not rig.watcher.running
    assert rig.watcher._open == {}


async def test_a_missing_proc_file_is_just_no_device(tmp_path):
    r = Rig(tmp_path)
    r.file.unlink()
    r.watcher.start(asyncio.get_running_loop())
    try:
        assert r.pipes == {}
        assert not r.watcher.touch_seen
    finally:
        r.close()


def test_the_event_layout_matches_the_kernel_on_this_machine():
    assert EVENT.size == struct.calcsize("@llHHi")
    assert inputs.READ_EVENTS * EVENT.size > 0
    assert errno.ENODEV  # le noyau le rend quand une manette Bluetooth s'éteint


# --- l'appui long dans un coin de l'écran (Free TV : un toucher simple navigue dans la page) --------------------------------

RANGES = ((0, 4095), (0, 4095))
SYN = (0, 0, 0)
SLOT, TRACKING, POS_X, POS_Y = 0x2F, ABS_MT_TRACKING_ID, 0x35, 0x36


def down(x, y, slot=0, tracking=5):
    return [
        (EV_ABS, SLOT, slot),
        (EV_ABS, TRACKING, tracking),
        (EV_ABS, POS_X, x),
        (EV_ABS, POS_Y, y),
        SYN,
    ]


def move(x, y, slot=0):
    return [(EV_ABS, SLOT, slot), (EV_ABS, POS_X, x), (EV_ABS, POS_Y, y), SYN]


def lift(slot=0):
    return [(EV_ABS, SLOT, slot), (EV_ABS, TRACKING, -1), SYN]


class CornerRig(Rig):
    def __init__(self, tmp_path, ranges=RANGES):
        super().__init__(tmp_path)
        self.corners: list[int] = []
        self.read = 0

        def reader(fd):
            self.read += 1
            return ranges

        self.watcher = InputWatcher(
            on_touch=lambda: self.touches.append(1),
            on_combo=lambda: self.combos.append(1),
            on_corner=lambda: self.corners.append(1),
            combo_seconds=0.15,
            corner_seconds=0.1,
            rescan_seconds=0.05,
            devices_file=str(self.file),
            opener=self.open,
            clock=lambda: self.now,
            abs_reader=reader,
        )

    def press(self, events):
        self.send(TOUCH, *events)


@pytest.fixture
async def corner(tmp_path):
    r = CornerRig(tmp_path)
    r.watcher.start(asyncio.get_running_loop())
    r.watcher.arm(0)
    yield r
    r.close()


def test_the_ioctl_numbers_are_the_kernels():
    assert inputs.eviocgabs(0) == 0x80184540  # EVIOCGABS(ABS_X)
    assert inputs.eviocgabs(0x35) == 0x80184575  # EVIOCGABS(ABS_MT_POSITION_X)


def test_the_screen_bounds_are_read_with_the_kernel_ioctl(monkeypatch):
    axes: dict[int, tuple[int, int]] = {
        inputs.eviocgabs(0x35): (0, 1023),
        inputs.eviocgabs(0x36): (10, 599),
    }

    def fake_ioctl(fd, request, buffer):
        if request not in axes:
            raise OSError(errno.ENOTTY, "pas cet axe")
        low, high = axes[request]
        buffer[:] = struct.pack("@6i", 0, low, high, 0, 0, 0)

    monkeypatch.setattr(inputs.fcntl, "ioctl", fake_ioctl)
    assert inputs.read_abs_ranges(3) == ((0, 1023), (10, 599))
    axes.clear()  # un écran qui ne donne ni X ni Y multi-touch : on essaie les axes simples
    axes.update({inputs.eviocgabs(0): (0, 4095), inputs.eviocgabs(1): (0, 4095)})
    assert inputs.read_abs_ranges(3) == ((0, 4095), (0, 4095))
    axes.clear()
    assert inputs.read_abs_ranges(3) is None  # sans position, pas de geste
    axes.update({inputs.eviocgabs(0x35): (5, 5), inputs.eviocgabs(0x36): (0, 600)})
    assert inputs.read_abs_ranges(3) is None  # des bornes qui n'en sont pas


def test_the_pipe_of_a_test_has_no_screen_bounds():
    read, write = os.pipe()
    try:
        assert inputs.read_abs_ranges(read) is None
    finally:
        os.close(read)
        os.close(write)


@pytest.mark.parametrize(
    ("corner_name", "inside", "outside"),
    [
        ("top-left", [(0, 0), (500, 400)], [(700, 100), (100, 1000), (4095, 4095)]),
        ("top-right", [(4095, 0), (3600, 400)], [(0, 0), (3000, 100)]),
        ("bottom-left", [(0, 4095), (500, 3300)], [(0, 0), (800, 3000)]),
        ("bottom-right", [(4095, 4095), (3600, 3300)], [(0, 0), (3000, 3000)]),
    ],
)
def test_each_corner_is_a_finger_sized_zone_of_the_screen(corner_name, inside, outside):
    for x, y in inside:
        assert inputs.in_corner(x, y, RANGES, corner_name), (x, y)
    for x, y in outside:
        assert not inputs.in_corner(x, y, RANGES, corner_name), (x, y)
    assert not inputs.in_corner(0, 0, RANGES, "milieu")


def test_the_zone_follows_the_bounds_of_the_screen_not_zero():
    ranges = ((100, 1123), (50, 650))  # un écran de 1024 × 600 dont le repère ne part pas de 0
    assert inputs.in_corner(110, 60, ranges, "top-left")
    assert not inputs.in_corner(600, 60, ranges, "top-left")
    assert inputs.in_corner(1120, 640, ranges, "bottom-right")


def test_the_corners_of_the_watcher_are_those_of_dock_toml():
    from mydock_dock.config import FREETV_CORNERS

    assert inputs.CORNERS == FREETV_CORNERS
    assert inputs.DEFAULT_CORNER == "top-left"


async def test_a_finger_held_in_the_corner_brings_the_dock_back_once(corner):
    corner.press(down(100, 100))
    await corner.settle(0.04)
    assert corner.corners == []  # pas encore : il faut tenir
    await corner.settle(0.15)
    assert corner.corners == [1]
    await corner.settle(0.3)
    assert corner.corners == [1]  # un seul retour par appui, même si le doigt reste
    assert corner.touches == [
        1
    ]  # le toucher lui-même est compté comme d'habitude (Free TV l'ignore)
    corner.press(lift())
    corner.press(down(120, 90))
    await corner.settle(0.2)
    assert corner.corners == [1, 1]  # levé puis reposé : un nouvel appui


async def test_a_finger_lifted_early_or_slid_out_of_the_corner_does_nothing(corner):
    corner.press(down(100, 100))
    await corner.settle(0.04)
    corner.press(lift())
    await corner.settle(0.2)
    assert corner.corners == []
    corner.press(down(100, 100))
    await corner.settle(0.04)
    corner.press(move(2000, 2000))  # le doigt glisse vers le milieu de l'écran
    await corner.settle(0.2)
    assert corner.corners == []


async def test_a_touch_anywhere_else_never_brings_the_dock_back(corner):
    corner.press(down(2000, 2000))
    await corner.settle(0.25)
    corner.press(lift())
    corner.press(down(3900, 3900))  # le coin d'en face
    await corner.settle(0.25)
    assert corner.corners == []


async def test_another_finger_in_the_corner_does_not_count(corner):
    corner.press(down(2000, 2000))  # le premier doigt, au milieu
    corner.press(down(100, 100, slot=1, tracking=6))  # un second, dans le coin
    await corner.settle(0.25)
    assert corner.corners == []


async def test_a_single_touch_screen_works_too(corner):
    corner.press([(EV_KEY, BTN_TOUCH, 1), (3, 0, 100), (3, 1, 120), SYN])
    await corner.settle(0.25)
    assert corner.corners == [1]


async def test_the_gesture_is_ignored_until_armed(tmp_path):
    r = CornerRig(tmp_path)
    r.watcher.start(asyncio.get_running_loop())
    try:
        r.watcher.arm(1.5)  # le doigt qui vient d'ouvrir Free TV est encore sur l'écran
        r.press(down(100, 100))
        await r.settle(0.25)
        assert r.corners == []
        r.press(lift())
        r.now += 2.0
        r.press(down(100, 100))
        await r.settle(0.25)
        assert r.corners == [1]
    finally:
        r.close()


async def test_another_corner_can_be_chosen(corner):
    corner.watcher.corner = "bottom-right"
    corner.press(down(100, 100))
    await corner.settle(0.25)
    assert corner.corners == []
    corner.press(lift())
    corner.press(down(4000, 4000))
    await corner.settle(0.25)
    assert corner.corners == [1]


async def test_without_the_bounds_of_the_screen_there_is_no_gesture_and_it_is_said(tmp_path):
    r = CornerRig(tmp_path, ranges=None)
    r.watcher.start(asyncio.get_running_loop())
    try:
        r.watcher.arm(0)
        assert not r.watcher.corner_ready
        r.press(down(100, 100))
        await r.settle(0.25)
        assert r.corners == []
    finally:
        r.close()


async def test_the_bounds_are_only_read_when_a_corner_gesture_is_wanted(rig, tmp_path):
    assert rig.watcher.corner_ready is False  # le jeu n'en a pas besoin : rien n'a été lu
    r = CornerRig(tmp_path)
    r.watcher.start(asyncio.get_running_loop())
    try:
        assert r.read == 1  # un écran tactile, ses bornes lues une fois
        assert r.watcher.corner_ready
    finally:
        r.close()


async def test_stopping_cancels_a_press_in_progress(corner):
    corner.press(down(100, 100))
    await corner.settle(0.04)
    corner.watcher.stop()
    await corner.settle(0.2)
    assert corner.corners == []
