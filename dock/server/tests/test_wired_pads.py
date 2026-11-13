"""Les manettes filaires : une manette USB branchée est « connectée » sans rien associer, et apparaît dans le thème « pads »."""

import asyncio

import pytest

from mydock_dock.demo_retro import demo_usb_source
from mydock_dock.inputs import (
    BTN_SELECT,
    BTN_SOUTH,
    BTN_START,
    BTN_TOUCH,
    BUS_USB,
    Device,
    parse_devices,
)
from mydock_dock.testing import eventually, running_dock, snapshot_of
from mydock_dock.wired_pads import WiredPads, usb_pads

from .test_inputs import bitmap


def proc_block(name, handlers, *, bus="0003", key=(), absolute=(), prop=None):
    lines = [
        f"I: Bus={bus} Vendor=054c Product=09cc Version=8111",
        f'N: Name="{name}"',
        f"H: Handlers={handlers}",
        "B: EV=1b",
    ]
    if prop is not None:
        lines.append(f"B: PROP={prop}")
    if key:
        lines.append(f"B: KEY={bitmap(*key)}")
    if absolute:
        lines.append(f"B: ABS={bitmap(*absolute)}")
    return "\n".join(lines)


PAD_KEYS = [BTN_SOUTH, 0x131, BTN_SELECT, BTN_START]

PROC = "\n\n".join(
    [
        proc_block(
            "Wireless Controller", "event6 js0", key=PAD_KEYS, absolute=[0, 1]
        ),  # une DualShock 4 en USB
        proc_block(
            "Wireless Controller Touchpad",
            "event7",
            key=[BTN_TOUCH, 0x110],
            absolute=[0, 1, 0x35, 0x36],
            prop=5,
        ),
        proc_block("Wireless Controller Motion Sensors", "event8", absolute=[0, 1, 2]),
        proc_block(
            "Xbox Wireless Controller", "event9 js1", bus="0005", key=PAD_KEYS, absolute=[0, 1]
        ),  # Bluetooth
        proc_block(
            "ILITEK Multi-Touch-V5100",
            "event3",
            key=[BTN_TOUCH],
            absolute=[0, 1, 0x35, 0x36, 0x39],
            prop=2,
        ),
        proc_block("Logitech USB Keyboard", "event4 kbd", key=[30, 31, 32, 57]),
        proc_block("Controller générique", "event10 js2", key=PAD_KEYS, absolute=[0, 1]),
    ]
)


# --- la reconnaissance ------------------------------------------------------------------------------------------


def test_the_bus_is_read_from_the_proc_file():
    devices = {d.name: d for d in parse_devices(PROC)}
    assert devices["Wireless Controller"].bus == BUS_USB
    assert devices["Xbox Wireless Controller"].bus == 0x05
    assert devices["Wireless Controller"].kind == "pad"


def test_a_pad_touchpad_is_not_a_touchscreen():
    """Le pavé tactile d'une manette Sony n'est pas l'écran du dock : le toucher n'y quitterait pas le jeu."""
    kinds = {d.name: d.kind for d in parse_devices(PROC)}
    assert "Wireless Controller Touchpad" not in kinds
    assert (
        kinds["ILITEK Multi-Touch-V5100"] == "touch"
    )  # le vrai écran (propriété « direct ») reste reconnu


def test_a_touchpad_without_the_pointer_property_is_still_known_by_its_name():
    text = proc_block(
        "Pad Touchpad", "event7", key=[BTN_TOUCH], absolute=[0, 1, 0x35, 0x36], prop=0
    )
    assert parse_devices(text) == []


def test_only_usb_pads_are_listed_as_wired():
    pads = usb_pads(parse_devices(PROC))
    assert [p["name"] for p in pads] == ["Controller générique", "Wireless Controller"]
    assert all(
        p["wired"] and p["connected"] and not p["paired"] and p["battery"] is None for p in pads
    )
    assert {p["id"] for p in pads} == {"usb:event10", "usb:event6"}  # jamais une adresse Bluetooth
    # La manette Bluetooth, l'écran tactile, le clavier et les capteurs n'y sont pas.
    assert "Xbox Wireless Controller" not in [p["name"] for p in pads]


def test_two_identical_pads_stay_two_entries():
    text = "\n\n".join(
        [
            proc_block("Pad", "event6 js0", key=PAD_KEYS, absolute=[0, 1]),
            proc_block("Pad", "event7 js1", key=PAD_KEYS, absolute=[0, 1]),
        ]
    )
    assert [p["id"] for p in usb_pads(parse_devices(text))] == ["usb:event6", "usb:event7"]


def test_a_long_name_is_cut():
    text = proc_block("N" * 200, "event6", key=PAD_KEYS, absolute=[0, 1])
    assert len(usb_pads(parse_devices(text))[0]["name"]) == 60


# --- le suivi ---------------------------------------------------------------------------------------------------


class Plug:
    """Une manette qu'on branche ou débranche."""

    def __init__(self):
        self.devices: list[Device] = []

    def __call__(self):
        return list(self.devices)

    def plug(self, name="Pad USB", n=6):
        self.devices.append(Device(name, f"/dev/input/event{n}", "pad", BUS_USB))


def test_a_pad_plugged_in_is_noticed_once_and_unplugging_too():
    source, changes = Plug(), []
    wired = WiredPads(lambda: changes.append(1), source=source)
    assert wired.refresh() is False  # rien de branché
    source.plug()
    assert wired.refresh() is True
    assert wired.refresh() is False  # inchangé : l'écran n'est pas prévenu pour rien
    assert [p["name"] for p in wired.snapshot()] == ["Pad USB"]
    source.devices.clear()
    assert wired.refresh() is True
    assert wired.snapshot() == []
    assert len(changes) == 2


def test_a_failing_read_keeps_the_last_list_and_never_raises():
    source = Plug()
    source.plug()
    wired = WiredPads(source=source)
    wired.refresh()

    def broken():
        raise OSError("lecture impossible")

    wired._source = broken
    assert wired.refresh() is False
    assert len(wired.snapshot()) == 1


def test_the_snapshot_cannot_be_modified_from_outside():
    source = Plug()
    source.plug()
    wired = WiredPads(source=source)
    wired.refresh()
    wired.snapshot()[0]["name"] = "autre"
    assert wired.snapshot()[0]["name"] == "Pad USB"


async def test_the_polling_picks_up_a_hot_plug_and_stops_cleanly():
    source, changes = Plug(), []
    wired = WiredPads(lambda: changes.append(1), source=source, interval=0.02)
    await wired.start()
    assert not changes
    source.plug()
    for _ in range(100):
        if changes:
            break
        await asyncio.sleep(0.02)
    assert changes == [1]
    await wired.stop()
    await wired.stop()  # sans effet la seconde fois
    source.plug("Autre", 7)
    await asyncio.sleep(0.1)
    assert changes == [1]  # arrêté : plus de sondage


# --- la démonstration ------------------------------------------------------------------------------------------


def test_the_demo_source_reads_names_from_a_file(tmp_path):
    source = demo_usb_source(tmp_path)
    assert source() == []
    (tmp_path / "demo-usb-pads.txt").write_text("Manette USB arcade\n\n  Pad 2  \n")
    devices = source()
    assert [(d.name, d.kind, d.bus) for d in devices] == [
        ("Manette USB arcade", "pad", BUS_USB),
        ("Pad 2", "pad", BUS_USB),
    ]
    assert len({d.path for d in devices}) == 2


# --- dans le thème « pads » du dock ----------------------------------------------------------------------------------


@pytest.fixture
async def dock(tmp_path):
    async with running_dock(tmp_path, portal=False) as running:
        yield running


async def test_a_usb_pad_appears_first_in_the_pads_topic_with_the_wired_mark(dock):
    source = Plug()
    dock.server.wired._source = source
    async with dock.connect() as browser:
        before = await snapshot_of(browser, "pads")
        assert not [d for d in before["data"]["devices"] if d.get("wired")]
        source.plug("Manette USB arcade")
        dock.server.wired.refresh()
        await eventually(
            lambda: any(d.get("wired") for d in dock.server.payload("pads")["devices"]), timeout=3
        )
        devices = dock.server.payload("pads")["devices"]
        assert devices[0] == {
            "id": "usb:event6", "name": "Manette USB arcade", "paired": False, "connected": True, "battery": None, "wired": True,
        }  # fmt: skip


async def test_unplugging_removes_it_from_the_topic(dock):
    source = Plug()
    source.plug()
    dock.server.wired._source = source
    dock.server.wired.refresh()
    assert dock.server.payload("pads")["devices"][0]["wired"] is True
    source.devices.clear()
    dock.server.wired.refresh()
    assert not [d for d in dock.server.payload("pads")["devices"] if d.get("wired")]


async def test_a_wired_pad_can_be_neither_paired_nor_forgotten(dock):
    source = Plug()
    source.plug()
    dock.server.wired._source = source
    dock.server.wired.refresh()
    async with dock.connect() as browser:
        from mydock_dock.testing import call

        for action in ("dock.pads.pair", "dock.pads.forget"):
            ack = await call(browser, "x", action, {"id": "usb:event6"})
            assert not ack["ok"], action
