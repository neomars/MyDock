"""Les manettes Bluetooth : ce qui est une manette, et BlueZ simulé sur un vrai dbus-daemon privé.

Le faux `org.bluez` parle comme le vrai (ObjectManager, Adapter1, Device1, Battery1, AgentManager1, signaux) et, à
l'association, appelle l'agent de la personne qui a demandé, comme le fait BlueZ."""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
from dbus_fast import DBusError, Message, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property, method

from mydock_dock.bluetooth import (
    AGENT_PATH,
    PadsError,
    PadsManager,
    battery_from_kernel,
    is_gamepad,
    rfkill_blocked,
    valid_mac,
)

GAMEPAD = 0x2508
KEYBOARD = 0x2540
HEADSET = 0x240404
HID = "00001124-0000-1000-8000-00805f9b34fb"
PAD = "AA:BB:CC:DD:EE:01"
PAD2 = "AA:BB:CC:DD:EE:02"
ADAPTER_PATH = "/org/bluez/hci0"


def path_of(mac: str) -> str:
    return f"{ADAPTER_PATH}/dev_{mac.replace(':', '_')}"


# --- ce qui est une manette -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("props", "expected"),
    [
        ({"Class": GAMEPAD}, True),  # DualShock 4, manette Switch
        ({"Class": 0x2504}, True),  # joystick
        ({"Class": KEYBOARD}, False),
        ({"Class": 0x2580}, False),  # une souris
        ({"Class": 0x25C0}, False),  # clavier + souris
        ({"Class": HEADSET}, False),
        ({"Appearance": 0x03C4}, True),  # Xbox Series en Bluetooth basse consommation
        ({"Appearance": 0x03C3}, True),
        ({"Appearance": 0x03C1, "UUIDs": [HID]}, False),  # un clavier qui parle HID
        ({"Appearance": 0x03C2, "UUIDs": [HID]}, False),
        ({"UUIDs": [HID]}, True),  # rien d'autre ne dit ce que c'est : on le montre
        ({"UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"]}, False),  # du son
        ({"Class": 0, "Appearance": 0, "UUIDs": [HID]}, True),  # « inconnu » pour BlueZ
        ({}, False),
    ],
)
def test_only_game_controllers_are_taken_for_game_controllers(props, expected):
    assert is_gamepad(props) is expected


def test_a_mac_address_must_have_the_exact_form():
    assert valid_mac("aa:bb:cc:dd:ee:01") == PAD
    for bad in ("AA:BB:CC:DD:EE", "AA-BB-CC-DD-EE-01", "../../x", "", None, 5, "AA:BB:CC:DD:EE:0G"):
        assert valid_mac(bad) is None


def test_the_battery_of_a_playstation_pad_comes_from_the_kernel_power_supply(tmp_path):
    base = tmp_path / "power_supply"
    (base / "ps-controller-battery-aa:bb:cc:dd:ee:01").mkdir(parents=True)
    (base / "ps-controller-battery-aa:bb:cc:dd:ee:01" / "capacity").write_text("64\n")
    (base / "BAT0").mkdir()
    (base / "BAT0" / "capacity").write_text("90\n")
    assert battery_from_kernel(PAD, str(base)) == 64
    assert battery_from_kernel(PAD2, str(base)) is None
    assert battery_from_kernel(PAD, str(tmp_path / "absent")) is None


def test_rfkill_is_read_from_sysfs(tmp_path):
    def entry(name, kind, soft, hard):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "type").write_text(kind + "\n")
        (folder / "soft").write_text(f"{soft}\n")
        (folder / "hard").write_text(f"{hard}\n")

    entry("rfkill0", "wlan", 1, 0)  # le Wi-Fi bloqué ne regarde pas le Bluetooth
    assert rfkill_blocked(str(tmp_path)) is None  # pas de Bluetooth du tout : on ne sait pas
    entry("rfkill1", "bluetooth", 0, 0)
    assert rfkill_blocked(str(tmp_path)) is False
    (tmp_path / "rfkill1" / "soft").write_text("1\n")
    assert rfkill_blocked(str(tmp_path)) is True


# --- un faux BlueZ --------------------------------------------------------------------------------------------------------

needs_dbus = pytest.mark.skipif(shutil.which("dbus-daemon") is None, reason="dbus-daemon absent")


class FakeAdapter(ServiceInterface):
    def __init__(self, bluez):
        super().__init__("org.bluez.Adapter1")
        self.bluez = bluez

    @dbus_property(access=PropertyAccess.READWRITE)
    def Powered(self) -> "b":  # noqa: F821
        return self.bluez.adapter["Powered"]

    @Powered.setter
    def Powered(self, value: "b"):  # noqa: F821
        self.bluez.adapter["Powered"] = value
        self.bluez.changed(ADAPTER_PATH, "org.bluez.Adapter1", {"Powered": Variant("b", value)})

    @method()
    def SetDiscoveryFilter(self, properties: "a{sv}"):  # noqa: F722
        self.bluez.calls.append(("SetDiscoveryFilter", sorted(properties)))

    @method()
    def StartDiscovery(self):
        self.bluez.calls.append(("StartDiscovery",))
        self.bluez.adapter["Discovering"] = True
        self.bluez.changed(ADAPTER_PATH, "org.bluez.Adapter1", {"Discovering": Variant("b", True)})

    @method()
    def StopDiscovery(self):
        self.bluez.calls.append(("StopDiscovery",))
        self.bluez.adapter["Discovering"] = False
        self.bluez.changed(ADAPTER_PATH, "org.bluez.Adapter1", {"Discovering": Variant("b", False)})

    @method()
    def RemoveDevice(self, device: "o"):  # noqa: F821
        self.bluez.calls.append(("RemoveDevice", device))
        self.bluez.remove(device)


class FakeDevice(ServiceInterface):
    def __init__(self, bluez, path):
        super().__init__("org.bluez.Device1")
        self.bluez, self.path = bluez, path

    @dbus_property(access=PropertyAccess.READWRITE)
    def Trusted(self) -> "b":  # noqa: F821
        return self.bluez.device(self.path)["Trusted"].value

    @Trusted.setter
    def Trusted(self, value: "b"):  # noqa: F821
        self.bluez.calls.append(("Trusted", self.path, value))
        self.bluez.changed(self.path, "org.bluez.Device1", {"Trusted": Variant("b", value)})

    @method()
    async def Pair(self):
        self.bluez.calls.append(("Pair", self.path))
        if self.bluez.pair_error:
            raise DBusError(self.bluez.pair_error, "refusé")
        if self.bluez.agent_dest:  # comme BlueZ : il demande à l'agent de l'appelant
            reply = await self.bluez.bus.call(
                Message(
                    destination=self.bluez.agent_dest,
                    path=AGENT_PATH,
                    interface="org.bluez.Agent1",
                    member="RequestConfirmation",
                    signature="ou",
                    body=[self.bluez.ask_agent_about or self.path, 123456],
                )
            )
            self.bluez.agent_replies.append(reply.message_type.name)
            if reply.message_type.name == "ERROR":
                raise DBusError("org.bluez.Error.AuthenticationRejected", "agent")
        self.bluez.changed(self.path, "org.bluez.Device1", {"Paired": Variant("b", True)})

    @method()
    def Connect(self):
        self.bluez.calls.append(("Connect", self.path))
        if self.bluez.connect_error:
            raise DBusError("org.bluez.Error.Failed", "pas à portée")
        self.bluez.changed(self.path, "org.bluez.Device1", {"Connected": Variant("b", True)})


class FakeAgentManager(ServiceInterface):
    def __init__(self, bluez):
        super().__init__("org.bluez.AgentManager1")
        self.bluez = bluez

    @method()
    def RegisterAgent(self, agent: "o", capability: "s"):  # noqa: F821
        self.bluez.calls.append(("RegisterAgent", agent, capability))

    @method()
    def UnregisterAgent(self, agent: "o"):  # noqa: F821
        self.bluez.calls.append(("UnregisterAgent", agent))

    @method()
    def RequestDefaultAgent(self, agent: "o"):  # noqa: F821
        self.bluez.calls.append(("RequestDefaultAgent", agent))


class FakeBluez:
    """Un BlueZ de poche : des objets à lire, des appels notés, des signaux émis comme le vrai."""

    def __init__(self, bus):
        self.bus = bus
        self.calls = []
        self.agent_dest = None
        self.ask_agent_about = None
        self.agent_replies = []
        self.pair_error = None
        self.connect_error = False
        self.adapter = {"Powered": False, "Discovering": False}
        self.objects = {
            ADAPTER_PATH: {
                "org.bluez.Adapter1": {
                    "Powered": Variant("b", False),
                    "Discovering": Variant("b", False),
                }
            }
        }
        bus.add_message_handler(self._managed_objects)
        bus.export(ADAPTER_PATH, FakeAdapter(self))
        bus.export("/org/bluez", FakeAgentManager(self))

    def _managed_objects(self, message):
        """Répond à GetManagedObjects avec les objets de BlueZ (dbus-fast n'y mettrait que les propriétés exportées)."""
        if (
            message.interface == "org.freedesktop.DBus.ObjectManager"
            and message.member == "GetManagedObjects"
        ):
            objects = {path: dict(ifaces) for path, ifaces in self.objects.items()}
            return Message.new_method_return(message, "a{oa{sa{sv}}}", [objects])
        return None

    def device(self, path):
        return self.objects[path]["org.bluez.Device1"]

    def add_device(
        self, mac, name, *, cls=GAMEPAD, paired=False, connected=False, battery=None, announce=False
    ):
        path = path_of(mac)
        props = {
            "Address": Variant("s", mac),
            "Alias": Variant("s", name),
            "Name": Variant("s", name),
            "Class": Variant("u", cls),
            "Paired": Variant("b", paired),
            "Trusted": Variant("b", paired),
            "Connected": Variant("b", connected),
            "UUIDs": Variant("as", [HID]),
        }
        ifaces = {"org.bluez.Device1": props}
        if battery is not None:
            ifaces["org.bluez.Battery1"] = {"Percentage": Variant("y", battery)}
        self.objects[path] = ifaces
        self.bus.export(path, FakeDevice(self, path))
        if announce:
            self.signal(
                "/",
                "org.freedesktop.DBus.ObjectManager",
                "InterfacesAdded",
                "oa{sa{sv}}",
                [path, ifaces],
            )
        return path

    def signal(self, path, interface, member, signature, body):
        self.bus.send(Message.new_signal(path, interface, member, signature, body))

    def changed(self, path, interface, props):
        self.objects[path][interface].update(props)
        self.signal(
            path,
            "org.freedesktop.DBus.Properties",
            "PropertiesChanged",
            "sa{sv}as",
            [interface, props, []],
        )

    def remove(self, path):
        ifaces = list(self.objects.pop(path, {}))
        self.signal(
            "/", "org.freedesktop.DBus.ObjectManager", "InterfacesRemoved", "oas", [path, ifaces]
        )

    def called(self, name):
        return [c for c in self.calls if c[0] == name]


@pytest.fixture
def address(tmp_path):
    process = subprocess.Popen(
        [
            "dbus-daemon",
            "--session",
            "--nofork",
            "--nopidfile",
            "--print-address=1",
            f"--address=unix:path={tmp_path / 'bus'}",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    value = process.stdout.readline().strip()
    assert value.startswith("unix:"), value
    yield value
    process.terminate()
    process.wait(timeout=5)


@pytest.fixture
async def bluez(address):
    bus = await MessageBus(bus_address=address).connect()
    fake = FakeBluez(bus)
    await bus.request_name("org.bluez")
    yield fake
    bus.disconnect()


class Watch:
    def __init__(self):
        self.count = 0

    def __call__(self):
        self.count += 1


async def pads_for(address, bluez, **kw):
    watch = Watch()
    manager = PadsManager(
        watch, bus_address=address, blocked=lambda: False, kernel_battery=lambda mac: None, **kw
    )
    await manager.start()
    manager.watch = watch
    bluez.agent_dest = manager._bus.unique_name
    return manager


async def settle(predicate, timeout=3.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


# --- l'état des manettes, en direct ----------------------------------------------------------------------------------------


@needs_dbus
async def test_only_paired_game_controllers_are_listed_with_their_battery(address, bluez):
    bluez.add_device(PAD, "Xbox Wireless Controller", paired=True, connected=True, battery=80)
    bluez.add_device(PAD2, "Wireless Controller", paired=True)
    bluez.add_device("AA:BB:CC:DD:EE:03", "Clavier", cls=KEYBOARD, paired=True)
    bluez.add_device("AA:BB:CC:DD:EE:04", "Casque", cls=HEADSET, paired=True)
    bluez.add_device(
        "AA:BB:CC:DD:EE:05", "Pro Controller"
    )  # pas associée : pas montrée tant qu'on ne cherche pas
    manager = await pads_for(address, bluez)
    try:
        snapshot = manager.snapshot()
        assert snapshot["available"] is True
        assert snapshot["reason"] is None
        assert [d["name"] for d in snapshot["devices"]] == [
            "Xbox Wireless Controller",
            "Wireless Controller",
        ]
        first, second = snapshot["devices"]
        assert (first["id"], first["connected"], first["battery"]) == (PAD, True, 80)
        assert (second["paired"], second["connected"], second["battery"]) == (True, False, None)
        assert [d["name"] for d in manager.connected()] == ["Xbox Wireless Controller"]
    finally:
        await manager.stop()


@needs_dbus
async def test_a_connection_and_a_battery_change_are_followed_live(address, bluez):
    path = bluez.add_device(PAD, "Xbox Wireless Controller", paired=True, battery=50)
    manager = await pads_for(address, bluez)
    try:
        before = manager.watch.count
        bluez.changed(path, "org.bluez.Device1", {"Connected": Variant("b", True)})
        await settle(lambda: manager.snapshot()["devices"][0]["connected"])
        assert manager.watch.count > before  # l'écran a été prévenu
        bluez.changed(path, "org.bluez.Battery1", {"Percentage": Variant("y", 35)})
        await settle(lambda: manager.snapshot()["devices"][0]["battery"] == 35)
        # un signal de bruit (la force du signal) ne réveille pas l'écran pour rien
        count = manager.watch.count
        bluez.changed(path, "org.bluez.Device1", {"RSSI": Variant("n", -60)})
        await asyncio.sleep(0.2)
        assert manager.watch.count == count
    finally:
        await manager.stop()


@needs_dbus
async def test_a_controller_that_appears_or_is_removed_is_followed_live(address, bluez):
    path = bluez.add_device(PAD, "8BitDo", paired=True)
    manager = await pads_for(address, bluez)
    try:
        bluez.add_device(PAD2, "DualSense", paired=True, announce=True)
        await settle(lambda: len(manager.snapshot()["devices"]) == 2)
        bluez.remove(path)
        await settle(lambda: [d["name"] for d in manager.snapshot()["devices"]] == ["DualSense"])
    finally:
        await manager.stop()


@needs_dbus
async def test_without_bluez_the_screen_is_told_why_and_nothing_crashes(address):
    manager = PadsManager(bus_address=address)  # le bus est là, mais personne n'y est « org.bluez »
    await manager.start()
    try:
        snapshot = manager.snapshot()
        assert snapshot["available"] is False
        assert "BlueZ n'est pas lancé" in snapshot["reason"]
        with pytest.raises(PadsError):
            await manager.scan()
    finally:
        await manager.stop()


@needs_dbus
async def test_without_a_bus_at_all_the_screen_is_told_too(tmp_path):
    manager = PadsManager(bus_address=f"unix:path={tmp_path / 'absent'}")
    await manager.start()
    assert manager.snapshot()["available"] is False
    assert "D-Bus" in manager.snapshot()["reason"]


# --- la recherche ---------------------------------------------------------------------------------------------------------


@needs_dbus
async def test_a_search_turns_the_adapter_on_lists_only_new_controllers_and_stops_by_itself(
    address, bluez
):
    manager = await pads_for(address, bluez, scan_seconds=0.6)
    try:
        assert manager.snapshot()["powered"] is False
        await manager.scan()
        assert bluez.adapter["Powered"] is True  # allumé sur geste explicite
        assert bluez.called("StartDiscovery")
        assert bluez.called("SetDiscoveryFilter")
        bluez.add_device(PAD, "Pro Controller", announce=True)
        bluez.add_device(PAD2, "Clavier", cls=KEYBOARD, announce=True)
        bluez.add_device("AA:BB:CC:DD:EE:03", "Casque", cls=HEADSET, announce=True)
        await settle(
            lambda: [d["name"] for d in manager.snapshot()["devices"]] == ["Pro Controller"]
        )
        assert manager.snapshot()["scanning"] is True
        assert manager.snapshot()["scan_until"] is not None
        await settle(lambda: not manager.snapshot()["scanning"], 3)  # trente secondes, ici 0,6
        assert bluez.called("StopDiscovery")
        # ce qui a été trouvé reste proposé jusqu'à la recherche suivante
        assert [d["name"] for d in manager.snapshot()["devices"]] == ["Pro Controller"]
    finally:
        await manager.stop()


@needs_dbus
async def test_a_blocked_bluetooth_is_unblocked_on_a_gesture_or_the_command_is_given(
    address, bluez
):
    blocked = {"now": True}
    manager = PadsManager(
        bus_address=address,
        blocked=lambda: blocked["now"],
        unblock=lambda: blocked.update(now=False) or True,
    )
    await manager.start()
    try:
        await manager.scan()
        assert blocked["now"] is False
        assert bluez.adapter["Powered"] is True
    finally:
        await manager.stop()
    stuck = PadsManager(bus_address=address, blocked=lambda: True, unblock=lambda: False)
    await stuck.start()
    try:
        with pytest.raises(PadsError, match=r"sudo rfkill unblock bluetooth"):
            await stuck.scan()
    finally:
        await stuck.stop()


# --- l'association --------------------------------------------------------------------------------------------------------


@needs_dbus
async def test_a_controller_is_paired_just_works_made_trusted_and_connected(address, bluez):
    path = bluez.add_device(PAD, "Pro Controller")
    manager = await pads_for(address, bluez)
    try:
        await manager.scan()
        await settle(lambda: manager.snapshot()["devices"])
        await manager.pair(PAD)
        # l'agent est « NoInputNoOutput », n'existe que le temps de l'association, et n'est jamais l'agent par défaut
        assert bluez.called("RegisterAgent") == [("RegisterAgent", AGENT_PATH, "NoInputNoOutput")]
        assert bluez.called("UnregisterAgent") == [("UnregisterAgent", AGENT_PATH)]
        assert bluez.called("RequestDefaultAgent") == []
        assert bluez.agent_replies == ["METHOD_RETURN"]  # l'agent a accepté la manette en cours
        order = [c[0] for c in bluez.calls if c[0] in ("Pair", "Trusted", "Connect")]
        assert order == ["Pair", "Trusted", "Connect"]
        assert ("Trusted", path, True) in bluez.calls  # elle se reconnectera seule
        await settle(lambda: manager.snapshot()["devices"][0]["connected"])
        device = manager.snapshot()["devices"][0]
        assert device["paired"] is True
        assert manager.snapshot()["pairing"]["state"] == "done"
        assert "connectée" in manager.snapshot()["pairing"]["message"]
        assert (
            bluez.adapter["Discovering"] is False
        )  # la recherche est arrêtée pendant l'association
    finally:
        await manager.stop()


@needs_dbus
async def test_the_agent_refuses_any_other_device_than_the_one_being_paired(address, bluez):
    bluez.add_device(PAD, "Pro Controller")
    bluez.ask_agent_about = path_of(PAD2)  # BlueZ demande l'accord pour une autre manette
    manager = await pads_for(address, bluez)
    try:
        await manager.scan()
        await settle(lambda: manager.snapshot()["devices"])
        await manager.pair(PAD)
        assert bluez.agent_replies == ["ERROR"]
        assert manager.snapshot()["pairing"]["state"] == "failed"
    finally:
        await manager.stop()


@needs_dbus
async def test_a_refused_pairing_is_explained_and_leaves_nothing_half_done(address, bluez):
    path = bluez.add_device(PAD, "Pro Controller")
    bluez.pair_error = "org.bluez.Error.AuthenticationFailed"
    manager = await pads_for(address, bluez)
    try:
        await manager.scan()
        await settle(lambda: manager.snapshot()["devices"])
        await manager.pair(PAD)
        pairing = manager.snapshot()["pairing"]
        assert pairing["state"] == "failed"
        assert "mode appairage" in pairing["message"]
        assert ("RemoveDevice", path) in bluez.calls  # pas d'appareil à moitié associé
        assert bluez.called("UnregisterAgent")  # l'agent ne reste pas
        assert bluez.called("Trusted") == []
    finally:
        await manager.stop()


@needs_dbus
async def test_a_controller_that_cannot_connect_yet_is_still_paired_and_trusted(address, bluez):
    bluez.add_device(PAD, "Pro Controller")
    bluez.connect_error = True
    manager = await pads_for(address, bluez)
    try:
        await manager.scan()
        await settle(lambda: manager.snapshot()["devices"])
        await manager.pair(PAD)
        pairing = manager.snapshot()["pairing"]
        assert pairing["state"] == "done"
        assert "bouton" in pairing["message"]
        assert manager.snapshot()["devices"][0]["paired"] is True
    finally:
        await manager.stop()


@needs_dbus
async def test_an_address_bluez_never_showed_is_refused(address, bluez):
    bluez.add_device("AA:BB:CC:DD:EE:03", "Clavier", cls=KEYBOARD, paired=True)
    manager = await pads_for(address, bluez)
    try:
        for hostile in ("AA:BB:CC:DD:EE:99", "../x", "AA:BB:CC:DD:EE:03", None):
            with pytest.raises(PadsError):
                await manager.pair(hostile)
            with pytest.raises(PadsError):
                await manager.forget(hostile)
    finally:
        await manager.stop()


@needs_dbus
async def test_a_controller_can_be_forgotten(address, bluez):
    path = bluez.add_device(PAD, "Xbox Wireless Controller", paired=True, connected=True)
    manager = await pads_for(address, bluez)
    try:
        await manager.forget(PAD)
        assert ("RemoveDevice", path) in bluez.calls
        await settle(lambda: manager.snapshot()["devices"] == [])
    finally:
        await manager.stop()


def test_the_error_names_of_bluez_are_all_told_in_french():
    from mydock_dock.bluetooth import PAIR_ERRORS

    assert all(
        text and text[0].isupper() and text.endswith((".", "?")) for text in PAIR_ERRORS.values()
    )
    assert Path(__file__).exists()
