"""Les manettes de jeu Bluetooth, par BlueZ (l'API D-Bus `org.bluez`, jamais `bluetoothctl` en sous-processus).

Le serveur du dock parle à BlueZ : il n'a besoin ni de l'écran ni d'une session, seulement du bus système (le groupe
`bluetooth` suffit : la politique D-Bus de BlueZ lui en donne le droit). Ce module :

  * suit en direct les manettes (`PropertiesChanged`, `InterfacesAdded`, `InterfacesRemoved`) : associée ou non,
    connectée ou non, batterie (`org.bluez.Battery1`, ou à défaut l'alimentation que le noyau expose pour les manettes
    PlayStation et Nintendo) ;
  * cherche pendant trente secondes et ne montre que les **manettes** (apparence gamepad ou joystick, classe « périphérique
    de jeu », ou service HID quand rien ne dit que c'est un clavier ou une souris) ;
  * associe une manette « Just Works » : un agent `NoInputNoOutput` qui n'existe que le temps de l'association, puis
    `Trusted = true` et `Connect()` : elle se reconnecte seule à l'allumage les fois suivantes ;
  * oublie une manette (`RemoveDevice`).

Il ne change jamais ce que la personne a réglé ailleurs : il n'enregistre pas d'agent par défaut (BlueZ utilise celui de
l'appelant de `Pair()`, donc l'agent du bureau garde sa place), n'allume l'adaptateur et ne lève le blocage `rfkill` que
sur un geste explicite sur l'écran (chercher, associer), et ne l'éteint jamais.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import struct
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BLUEZ = "org.bluez"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
BATTERY = "org.bluez.Battery1"
AGENT_MANAGER = "org.bluez.AgentManager1"
AGENT = "org.bluez.Agent1"
AGENT_PATH = "/org/mydock/agent"
MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
HID_UUIDS = frozenset(
    {"00001124-0000-1000-8000-00805f9b34fb", "00001812-0000-1000-8000-00805f9b34fb"}
)
GAMEPAD_APPEARANCES = frozenset({0x03C3, 0x03C4})  # joystick, gamepad
OTHER_HID_APPEARANCES = frozenset({0x03C1, 0x03C2, 0x03C5, 0x03C6, 0x03C7, 0x03C8, 0x03C9})
SCAN_SECONDS = 30.0
PAIR_TIMEOUT = 45.0
CONNECT_TIMEOUT = 20.0
CALL_TIMEOUT = 10.0
RESULT_SECONDS = 10.0  # combien de temps l'écran garde « associée » ou l'échec
NOISY = frozenset({"RSSI", "ManufacturerData", "ServiceData", "TxPower", "AdvertisingData"})
RFKILL_DEVICE = "/dev/rfkill"
RFKILL_SYSFS = "/sys/class/rfkill"
POWER_SUPPLY = "/sys/class/power_supply"

#: Ce que BlueZ répond quand une association échoue, dit à la personne qui tient la manette.
PAIR_ERRORS = {
    "org.bluez.Error.AuthenticationFailed": "La manette a refusé l'association : remets-la en mode appairage et réessaie.",
    "org.bluez.Error.AuthenticationRejected": "La manette a refusé l'association : remets-la en mode appairage et réessaie.",
    "org.bluez.Error.AuthenticationCanceled": "L'association a été annulée.",
    "org.bluez.Error.AuthenticationTimeout": "La manette n'a pas répondu : remets-la en mode appairage et réessaie.",
    "org.bluez.Error.ConnectionAttemptFailed": "La manette est introuvable : est-elle bien en mode appairage, à portée ?",
    "org.bluez.Error.Failed": "L'association a échoué : remets la manette en mode appairage et réessaie.",
    "org.bluez.Error.InProgress": "Une autre association est en cours : patiente un instant.",
    "org.bluez.Error.AlreadyExists": "Cette manette est déjà associée.",
}


class BluezError(Exception):
    """Une erreur que BlueZ (ou le bus) répond : son nom D-Bus et son texte."""

    def __init__(self, name: str, text: str = "") -> None:
        super().__init__(f"{name} : {text}" if text else name)
        self.name, self.text = name, text


class PadsError(Exception):
    """Une action sur les manettes refusée ; le message est affichable à l'écran."""


# --- ce qui est une manette ----------------------------------------------------------------------------------------------


def is_gamepad(props: dict[str, Any]) -> bool:
    """Cet appareil est-il une manette de jeu ? (pas un clavier, une souris, un casque…)"""
    appearance = props.get("Appearance") or None  # 0 : BlueZ ne le sait pas
    if isinstance(appearance, int):
        if appearance in GAMEPAD_APPEARANCES:
            return True
        if appearance in OTHER_HID_APPEARANCES:
            return False
    device_class = props.get("Class")
    if isinstance(device_class, int) and device_class:
        major, minor = (device_class >> 8) & 0x1F, (device_class >> 2) & 0x3F
        if major == 5:  # un périphérique d'entrée : manette ou joystick, sinon clavier ou souris
            return minor & 0x30 == 0 and minor & 0x0F in (1, 2)
        return False  # un casque, un téléphone…
    uuids = {str(u).lower() for u in props.get("UUIDs") or ()}
    # Rien ne dit ce que c'est, mais il parle HID : on le montre (le pire, c'est une manette de plus à ignorer).
    return not isinstance(appearance, int) and bool(uuids & HID_UUIDS)


def valid_mac(value: object) -> str | None:
    text = value.strip().upper() if isinstance(value, str) else ""
    return text if MAC_RE.fullmatch(text) else None


def battery_from_kernel(mac: str, base: str = POWER_SUPPLY) -> int | None:
    """La charge d'une manette que BlueZ ne dit pas (PlayStation, Nintendo) : l'alimentation du noyau qui porte son adresse."""
    needle = mac.lower()
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return None
    for name in entries:
        if needle in name.lower() or needle.replace(":", "_") in name.lower():
            try:
                value = int(Path(base, name, "capacity").read_text().strip())
            except (OSError, ValueError):
                continue
            if 0 <= value <= 100:
                return value
    return None


# --- rfkill -------------------------------------------------------------------------------------------------------------------


def rfkill_blocked(base: str = RFKILL_SYSFS) -> bool | None:
    """Le Bluetooth est-il bloqué (logiciel ou matériel) ? `None` si on ne sait pas."""
    blocked: bool | None = None
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    for name in names:
        folder = Path(base, name)
        try:
            if (folder / "type").read_text().strip() != "bluetooth":
                continue
            soft = (folder / "soft").read_text().strip() == "1"
            hard = (folder / "hard").read_text().strip() == "1"
        except OSError:
            continue
        blocked = bool(blocked) or soft or hard
    return blocked


def rfkill_unblock(device: str = RFKILL_DEVICE) -> bool:
    """Lève le blocage logiciel du Bluetooth (le droit d'écrire dans /dev/rfkill est celui du système : sinon, rien)."""
    # struct rfkill_event : idx (u32), type (u8), op (u8), soft (u8), hard (u8) ; type 2 = Bluetooth, op 3 = tous
    event = struct.pack("=IBBBB", 0, 2, 3, 0, 0)
    try:
        fd = os.open(device, os.O_WRONLY)
    except OSError:
        return False
    try:
        os.write(fd, event)
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


# --- l'agent d'association ---------------------------------------------------------------------------------------------------


def _build_agent(allowed: Callable[[], str | None]) -> Any:
    """L'agent `NoInputNoOutput` : il accepte l'association de la manette en cours et refuse tout le reste."""
    from dbus_fast import DBusError
    from dbus_fast.service import ServiceInterface, method

    def check(device: str) -> None:
        if allowed() != device:
            raise DBusError("org.bluez.Error.Rejected", "Association non demandée")

    class Agent(ServiceInterface):
        # Les annotations sont des signatures D-Bus (« o » : un chemin, « s » : un texte…), entre guillemets comme
        # dbus-fast l'exige : ce ne sont pas des noms de types.
        def __init__(self):
            super().__init__(AGENT)

        @method()
        def Release(self):
            return None

        @method()
        def RequestPinCode(self, device: "o") -> "s":  # noqa: F821, UP037
            check(device)
            return "0000"  # les vieilles manettes à code fixe

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: F821, UP037
            check(device)

        @method()
        def RequestPasskey(self, device: "o") -> "u":  # noqa: F821, UP037
            check(device)
            return 0

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: F821, UP037
            check(device)

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821, UP037
            check(device)

        @method()
        def RequestAuthorization(self, device: "o"):  # noqa: F821, UP037
            check(device)

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821, UP037
            check(device)

        @method()
        def Cancel(self):
            return None

    return Agent()


# --- le gestionnaire -----------------------------------------------------------------------------------------------------------


class PadsManager:
    """L'état des manettes et ce qu'on peut leur faire. Sans BlueZ, il le dit et ne fait rien."""

    def __init__(
        self,
        on_change: Callable[[], None] = lambda: None,
        *,
        bus_address: str | None = None,
        wall: Callable[[], float] = time.time,
        scan_seconds: float = SCAN_SECONDS,
        unblock: Callable[[], bool] = rfkill_unblock,
        blocked: Callable[[], bool | None] = rfkill_blocked,
        kernel_battery: Callable[[str], int | None] = battery_from_kernel,
    ) -> None:
        self._on_change = on_change
        self._address, self._wall, self._scan_seconds = bus_address, wall, scan_seconds
        self._unblock, self._blocked, self._kernel_battery = unblock, blocked, kernel_battery
        self._bus: Any = None
        self._objects: dict[str, dict[str, dict[str, Any]]] = {}
        self._seen: set[str] = set()  # les manettes trouvées par la dernière recherche
        self._scan_until = 0.0
        self._scan_task: asyncio.Task[None] | None = None
        self._pairing: dict[str, str] | None = None
        self._pairing_path: str | None = None
        self._agent: Any = None
        self._clear_task: asyncio.Task[None] | None = None
        self._reason: str | None = "Le Bluetooth n'est pas encore lu."
        self._last: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    # --- cycle de vie ---------------------------------------------------------------------------------

    async def start(self) -> None:
        """Se connecte au bus et lit BlueZ ; sans bus ni BlueZ, l'état dit pourquoi (jamais une erreur)."""
        try:
            from dbus_fast import BusType, Message
            from dbus_fast.aio import MessageBus
        except ImportError:
            self._reason = "La bibliothèque D-Bus manque : sudo apt install python3-dbus-fast"
            self._emit()
            return
        try:
            self._bus = await asyncio.wait_for(
                MessageBus(bus_type=BusType.SYSTEM, bus_address=self._address).connect(),
                CALL_TIMEOUT,
            )
        except Exception as exc:  # pas de bus système (conteneur, droits)
            log.info("Bluetooth : pas de bus D-Bus (%s)", exc)
            self._bus = None
            self._reason = "Le bus D-Bus du système est inaccessible."
            self._emit()
            return
        self._bus.add_message_handler(self._on_message)
        for rule in (
            f"type='signal',sender='{BLUEZ}'",
            f"type='signal',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='{BLUEZ}'",
        ):
            with contextlib.suppress(Exception):
                await self._bus.call(
                    Message(
                        destination="org.freedesktop.DBus",
                        path="/org/freedesktop/DBus",
                        interface="org.freedesktop.DBus",
                        member="AddMatch",
                        signature="s",
                        body=[rule],
                    )
                )
        await self.reload()

    async def stop(self) -> None:
        for task in (self._scan_task, self._clear_task):
            if task is not None:
                task.cancel()
        if self._bus is not None:
            if self._scan_task is not None:
                with contextlib.suppress(Exception):
                    await self._stop_discovery()
            await self._unregister_agent()
            with contextlib.suppress(Exception):
                self._bus.disconnect()
            self._bus = None

    async def reload(self) -> None:
        """Relit tout ce que BlueZ expose (au démarrage, et quand `bluetoothd` redémarre)."""
        try:
            reply = await self._call("/", OBJECT_MANAGER, "GetManagedObjects")
        except BluezError as exc:
            self._objects = {}
            if exc.name in (
                "org.freedesktop.DBus.Error.ServiceUnknown",
                "org.freedesktop.DBus.Error.NameHasNoOwner",
            ):
                self._reason = "BlueZ n'est pas lancé : sudo apt install bluez, puis sudo systemctl enable --now bluetooth"
            else:
                self._reason = f"BlueZ ne répond pas ({exc.name.rsplit('.', 1)[-1]})."
            self._emit()
            return
        self._objects = {
            path: {iface: _plain(props) for iface, props in ifaces.items()}
            for path, ifaces in (reply[0] if reply else {}).items()
        }
        self._reason = None if self._adapter_path() else "Aucun adaptateur Bluetooth sur ce Pi."
        self._emit()

    # --- les appels D-Bus --------------------------------------------------------------------------------

    async def _call(
        self,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: list[Any] | None = None,
        *,
        timeout: float = CALL_TIMEOUT,
    ) -> list[Any]:
        from dbus_fast import Message, MessageType

        if self._bus is None:
            raise BluezError("org.freedesktop.DBus.Error.NoServer", "pas de bus")
        message = Message(
            destination=BLUEZ,
            path=path,
            interface=interface,
            member=member,
            signature=signature,
            body=body or [],
        )
        try:
            reply = await asyncio.wait_for(self._bus.call(message), timeout)
        except TimeoutError:
            raise BluezError("org.bluez.Error.Timeout", member) from None
        if reply.message_type == MessageType.ERROR:
            raise BluezError(reply.error_name or "erreur", str(reply.body[0]) if reply.body else "")
        return list(reply.body)

    async def _set(self, path: str, interface: str, name: str, signature: str, value: Any) -> None:
        from dbus_fast import Variant

        await self._call(
            path, PROPERTIES, "Set", "ssv", [interface, name, Variant(signature, value)]
        )

    # --- les signaux ---------------------------------------------------------------------------------

    def _on_message(self, message: Any) -> bool | None:
        from dbus_fast import MessageType

        if message.message_type != MessageType.SIGNAL:
            return None
        member, body = message.member, message.body
        if member == "NameOwnerChanged" and body and body[0] == BLUEZ:
            asyncio.get_running_loop().create_task(self.reload())
        elif member == "InterfacesAdded" and len(body) == 2:
            path, ifaces = body
            self._objects.setdefault(path, {}).update(
                {iface: _plain(props) for iface, props in ifaces.items()}
            )
            self._noticed(path)
            self._emit()
        elif member == "InterfacesRemoved" and len(body) == 2:
            path, ifaces = body
            known = self._objects.get(path, {})
            for iface in ifaces:
                known.pop(iface, None)
            if not known:
                self._objects.pop(path, None)
            self._emit()
        elif member == "PropertiesChanged" and len(body) == 3 and message.path:
            iface, changed, invalidated = body
            props = self._objects.setdefault(message.path, {}).setdefault(iface, {})
            props.update(_plain(changed))
            for name in invalidated:
                props.pop(name, None)
            if set(changed) - NOISY or invalidated:
                self._noticed(message.path)
                self._emit()
        return None

    def _noticed(self, path: str) -> None:
        """Une manette que la recherche en cours vient de voir : elle reste dans la liste jusqu'à la prochaine."""
        device = self._objects.get(path, {}).get(DEVICE)
        if device is not None and self._scanning() and is_gamepad(device):
            mac = valid_mac(device.get("Address"))
            if mac:
                self._seen.add(mac)

    # --- ce que voit l'écran ----------------------------------------------------------------------------

    def _adapter_path(self) -> str | None:
        paths = sorted(p for p, ifaces in self._objects.items() if ADAPTER in ifaces)
        return paths[0] if paths else None

    def _scanning(self) -> bool:
        return self._wall() < self._scan_until

    def _device_path(self, mac: str) -> str | None:
        for path, ifaces in self._objects.items():
            device = ifaces.get(DEVICE)
            if device is not None and valid_mac(device.get("Address")) == mac:
                return path
        return None

    def snapshot(self) -> dict[str, Any]:
        adapter_path = self._adapter_path()
        adapter = self._objects.get(adapter_path or "", {}).get(ADAPTER, {})
        devices = []
        for ifaces in self._objects.values():
            device = ifaces.get(DEVICE)
            if device is None or not is_gamepad(device):
                continue
            mac = valid_mac(device.get("Address"))
            paired = bool(device.get("Paired"))
            if mac is None or not (paired or mac in self._seen):
                continue
            battery = ifaces.get(BATTERY, {}).get("Percentage")
            if not isinstance(battery, int):
                battery = self._kernel_battery(mac) if device.get("Connected") else None
            devices.append(
                {
                    "id": mac,
                    "name": str(device.get("Alias") or device.get("Name") or mac)[:60],
                    "paired": paired,
                    "connected": bool(device.get("Connected")),
                    "battery": battery,
                }
            )
        devices.sort(key=lambda d: (not d["connected"], not d["paired"], d["name"].casefold()))
        return {
            "available": adapter_path is not None and self._bus is not None,
            "reason": self._reason,
            "powered": bool(adapter.get("Powered")),
            "blocked": self._blocked(),
            "scanning": self._scanning(),
            "scan_until": int(self._scan_until * 1000) if self._scanning() else None,
            "pairing": dict(self._pairing) if self._pairing else None,
            "devices": devices,
        }

    def connected(self) -> list[dict[str, Any]]:
        return [d for d in self.snapshot()["devices"] if d["connected"]]

    def _emit(self) -> None:
        snapshot = self.snapshot()
        if snapshot != self._last:
            self._last = snapshot
            self._on_change()

    # --- les gestes ------------------------------------------------------------------------------------

    async def _power_on(self) -> str:
        """Allume l'adaptateur si besoin (et lève le blocage logiciel) ; rend son chemin D-Bus."""
        adapter = self._adapter_path()
        if adapter is None:
            raise PadsError(self._reason or "Aucun adaptateur Bluetooth sur ce Pi.")
        if self._blocked():
            if not self._unblock():
                raise PadsError(
                    "Le Bluetooth est bloqué : sudo rfkill unblock bluetooth (le dock n'a pas le droit de le faire)."
                )
            await asyncio.sleep(0.5)
        if not self._objects[adapter].get(ADAPTER, {}).get("Powered"):
            try:
                await self._set(adapter, ADAPTER, "Powered", "b", True)
            except BluezError as exc:
                raise PadsError(
                    f"Le Bluetooth ne s'allume pas ({exc.name.rsplit('.', 1)[-1]})."
                ) from None
        return adapter

    async def scan(self) -> None:
        """Cherche des manettes pendant une trentaine de secondes."""
        from dbus_fast import Variant

        adapter = await self._power_on()
        self._seen.clear()
        try:
            await self._call(
                adapter,
                ADAPTER,
                "SetDiscoveryFilter",
                "a{sv}",
                [{"Transport": Variant("s", "auto")}],
            )
            if not self._objects[adapter].get(ADAPTER, {}).get("Discovering"):
                await self._call(adapter, ADAPTER, "StartDiscovery")
        except BluezError as exc:
            if exc.name != "org.bluez.Error.InProgress":
                raise PadsError(
                    f"La recherche n'a pas démarré ({exc.name.rsplit('.', 1)[-1]})."
                ) from None
        self._scan_until = self._wall() + self._scan_seconds
        for path in list(self._objects):
            self._noticed(path)  # ce que BlueZ avait déjà vu compte aussi
        if self._scan_task is not None:
            self._scan_task.cancel()
        self._scan_task = asyncio.create_task(self._end_scan(self._scan_seconds))
        self._emit()

    async def _end_scan(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self.stop_scan()

    async def stop_scan(self) -> None:
        self._scan_until = 0.0
        task, self._scan_task = self._scan_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        with contextlib.suppress(Exception):
            await self._stop_discovery()
        self._emit()

    async def _stop_discovery(self) -> None:
        adapter = self._adapter_path()
        if adapter is not None and self._objects.get(adapter, {}).get(ADAPTER, {}).get(
            "Discovering"
        ):
            with contextlib.suppress(BluezError):
                await self._call(adapter, ADAPTER, "StopDiscovery")

    def _known(self, mac: object) -> tuple[str, str]:
        address = valid_mac(mac)
        path = self._device_path(address) if address else None
        device = self._objects.get(path or "", {}).get(DEVICE)
        if address is None or path is None or device is None or not is_gamepad(device):
            raise PadsError("Cette manette n'est plus visible : relance la recherche.")
        return address, path

    async def pair(self, mac: object) -> None:
        """Associe une manette (« Just Works »), la rend de confiance et la connecte. Rend la main à la fin."""
        address, path = self._known(mac)
        async with self._lock:
            adapter = await self._power_on()
            await self.stop_scan()
            self._set_pairing(address, "pairing", "Association en cours…")
            try:
                if not self._objects.get(path, {}).get(DEVICE, {}).get("Paired"):
                    await self._register_agent(path)
                    await self._call(path, DEVICE, "Pair", timeout=PAIR_TIMEOUT)
                await self._set(path, DEVICE, "Trusted", "b", True)  # elle se reconnectera seule
                self._set_pairing(address, "connecting", "Connexion de la manette…")
                try:
                    await self._call(path, DEVICE, "Connect", timeout=CONNECT_TIMEOUT)
                except BluezError:
                    # Associée et de confiance : elle se connectera à son prochain allumage.
                    self._set_pairing(
                        address,
                        "done",
                        "Manette associée. Appuie sur son bouton central pour la connecter.",
                    )
                else:
                    self._set_pairing(address, "done", "Manette associée et connectée.")
            except BluezError as exc:
                with contextlib.suppress(
                    BluezError
                ):  # une association ratée ne laisse pas d'appareil à moitié fait
                    if not self._objects.get(path, {}).get(DEVICE, {}).get("Paired"):
                        await self._call(adapter, ADAPTER, "RemoveDevice", "o", [path])
                self._set_pairing(
                    address,
                    "failed",
                    PAIR_ERRORS.get(
                        exc.name, f"L'association a échoué ({exc.name.rsplit('.', 1)[-1]})."
                    ),
                )
            finally:
                await self._unregister_agent()

    def _set_pairing(self, mac: str, state: str, message: str) -> None:
        self._pairing = {"id": mac, "state": state, "message": message}
        if self._clear_task is not None:
            self._clear_task.cancel()
        if state in ("done", "failed"):
            self._clear_task = asyncio.create_task(self._clear_pairing(mac))
        self._emit()

    async def _clear_pairing(self, mac: str) -> None:
        await asyncio.sleep(RESULT_SECONDS)
        if self._pairing and self._pairing["id"] == mac:
            self._pairing = None
            self._emit()

    async def forget(self, mac: object) -> None:
        """Oublie une manette (l'écran a demandé confirmation)."""
        address, path = self._known(mac)
        adapter = self._adapter_path()
        if adapter is None:
            raise PadsError("Aucun adaptateur Bluetooth sur ce Pi.")
        try:
            await self._call(adapter, ADAPTER, "RemoveDevice", "o", [path])
        except BluezError as exc:
            if exc.name != "org.bluez.Error.DoesNotExist":
                raise PadsError(
                    f"La manette n'a pas pu être oubliée ({exc.name.rsplit('.', 1)[-1]})."
                ) from None
        self._seen.discard(address)
        self._objects.pop(path, None)
        self._emit()

    # --- l'agent ------------------------------------------------------------------------------------------

    async def _register_agent(self, device_path: str) -> None:
        """L'agent n'existe que pour l'association en cours, et n'accepte que cette manette."""
        self._pairing_path = device_path
        if self._agent is None:
            self._agent = _build_agent(lambda: self._pairing_path)
            self._bus.export(AGENT_PATH, self._agent)
        try:
            await self._call(
                "/org/bluez", AGENT_MANAGER, "RegisterAgent", "os", [AGENT_PATH, "NoInputNoOutput"]
            )
        except BluezError as exc:
            if exc.name != "org.bluez.Error.AlreadyExists":
                raise

    async def _unregister_agent(self) -> None:
        self._pairing_path = None
        if self._agent is None or self._bus is None:
            return
        with contextlib.suppress(Exception):
            await self._call("/org/bluez", AGENT_MANAGER, "UnregisterAgent", "o", [AGENT_PATH])
        with contextlib.suppress(Exception):
            self._bus.unexport(AGENT_PATH)
        self._agent = None


def _plain(values: dict[str, Any]) -> dict[str, Any]:
    """Les propriétés D-Bus sans leurs `Variant`."""
    return {name: getattr(value, "value", value) for name, value in values.items()}
