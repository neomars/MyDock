# ruff: noqa: F821
"""Alimentation : droits lus chez logind, ordres, refus expliqués, confirmations, et un VRAI bus D-Bus.

Les tests « bus réel » lancent un dbus-daemon privé avec un faux logind qui parle exactement le
protocole du vrai (mêmes noms de méthodes, mêmes signatures, mêmes erreurs) : c'est la meilleure
preuve possible sans éteindre le PC de développement.
"""

import asyncio
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from dbus_fast import DBusError, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property, method

from mydock_agent.collectors import power
from mydock_agent.collectors.music import BusError, DbusFastBus
from mydock_agent.collectors.power import (
    Blocker,
    Control,
    PowerError,
    PowerProvider,
    PowerState,
    SystemPower,
    explain_failure,
    judge,
    parse_blockers,
    quoted,
    register_power_actions,
)
from mydock_agent.demo.power import DemoPower
from mydock_agent.testing import eventually
from mydock_common.actions import ActionContext, ActionRegistry
from mydock_common.protocol import Act

ACCESS_DENIED = "org.freedesktop.DBus.Error.AccessDenied"
AUTH_REQUIRED = "org.freedesktop.DBus.Error.InteractiveAuthorizationRequired"


def blocker(who="Mises à jour", why="Installation en cours", scope=("shutdown",)):
    return Blocker(who, why, tuple(scope))


# --- fonctions pures ----------------------------------------------------------------------------


def test_logind_answers_become_a_right_with_a_short_reason():
    assert judge("yes", []) == Control(True)
    assert judge("challenge", []) == Control(False, "Mot de passe exigé par le système")
    assert judge("no", []) == Control(False, "Refusé par le système")
    assert judge("na", []) == Control(False, "Indisponible sur ce PC")
    assert judge("?", []) == Control(False, "Réponse inattendue du système")
    assert judge("challenge", [blocker()]) == Control(False, "Bloqué par « Mises à jour »")
    assert judge("yes", [blocker()]).ok is True  # logind a le dernier mot


def test_responsible_programs_are_named_without_repeating_or_running_on():
    assert quoted([blocker("A")]) == "« A »"
    assert quoted([blocker("A"), blocker("B")]) == "« A » et « B »"
    assert quoted([blocker("A"), blocker("A")]) == "« A »"
    assert quoted([blocker("A"), blocker("B"), blocker("C")]) == "« A », « B » et 1 autre"
    assert quoted([blocker(x) for x in "ABCD"]) == "« A », « B » et 2 autres"


def test_only_blocking_locks_on_shutdown_or_sleep_are_reported():
    items = [
        ["shutdown", "Mises à jour", "Installation", "block", 0, 10],
        ["sleep:idle", "Lecteur vidéo", "Film en cours", "block", 1000, 11],
        ["shutdown", "GNOME", "Enregistre la session", "delay", 1000, 12],  # retarde, ne bloque pas
        [
            "idle:handle-power-key",
            "Autre",
            "Rien",
            "block",
            1000,
            13,
        ],  # ne touche pas à l'extinction
        ["sleep", "x" * 200, "y\x00z\n" * 100, "block", 1000, 14],
        "pas une liste",
        ["shutdown"],
    ]
    found = parse_blockers(items)
    assert [(b.who, b.scope) for b in found[:2]] == [
        ("Mises à jour", ("shutdown",)),
        ("Lecteur vidéo", ("sleep",)),
    ]
    assert len(found) == 3
    assert len(found[2].who) <= 60
    assert "\x00" not in found[2].why
    assert len(found[2].why) <= 100
    assert parse_blockers(None) == ()
    assert len(parse_blockers([["shutdown", "a", "b", "block", 0, 1]] * 20)) == power.MAX_BLOCKERS


def test_refusals_are_explained_from_the_error_name_never_from_its_text():
    assert explain_failure("poweroff", AUTH_REQUIRED, []).startswith(
        "Le système exige un mot de passe"
    )
    assert explain_failure("reboot", ACCESS_DENIED, []).startswith(
        "Le système exige un mot de passe"
    )
    assert explain_failure("suspend", "org.freedesktop.login1.OperationInProgress", []) == (
        "Une extinction, un redémarrage ou une veille est déjà en cours."
    )
    assert explain_failure("suspend", "org.freedesktop.login1.SleepVerbNotSupported", []) == (
        "Ce PC ne sait pas se mettre en veille."
    )
    assert "logind" in explain_failure("poweroff", "org.freedesktop.DBus.Error.ServiceUnknown", [])
    assert "logind" in explain_failure("poweroff", "", [])
    assert explain_failure("poweroff", "org.exemple.Error.Bizarre", []) == (
        "Le système a refusé l'opération (Bizarre)."
    )
    assert explain_failure("poweroff", AUTH_REQUIRED, [blocker("Mises à jour")]) == (
        "« Mises à jour » empêche l'extinction."
    )
    assert explain_failure("suspend", "", [blocker("A"), blocker("B")]) == (
        "« A » et « B » empêchent la mise en veille."
    )
    assert explain_failure("reboot", "", [blocker("A")]) == "« A » empêche le redémarrage."


def test_a_state_serializes_with_the_four_controls_in_order():
    state = PowerState(
        "mars-pc", {name: Control(name != "suspend", None) for name in power.CONTROLS}
    )
    data = state.to_dict()
    assert list(data["controls"]) == ["lock", "suspend", "reboot", "poweroff"]
    assert data["controls"]["suspend"] == {"ok": False, "why": None}
    assert "unavailable" not in data
    down = PowerState("mars-pc", state.controls, (blocker(),), unavailable="Panne")
    assert down.to_dict()["unavailable"] == "Panne"
    assert down.to_dict()["blockers"] == [
        {"who": "Mises à jour", "why": "Installation en cours", "scope": ["shutdown"]}
    ]


# --- un faux bus, pour lire et ordonner sans D-Bus ------------------------------------------------


class FakeBus:
    """Répond aux appels D-Bus d'après un dictionnaire {méthode: valeur | exception | fonction}."""

    def __init__(self, replies, log, *, connect_error=None):
        self.replies = replies
        self.log = log
        self.connect_error = connect_error

    async def connect(self):
        if self.connect_error:
            raise self.connect_error

    async def disconnect(self):
        self.log.append(("disconnect",))

    async def call(self, destination, path, interface, member, signature="", body=None):
        self.log.append((destination, path, interface, member, signature, body or []))
        reply = self.replies.get(member, BusError(f"{member} : absent", name=""))
        if callable(reply):
            reply = reply(body or [])
        if isinstance(reply, Exception):
            raise reply
        return reply


def logind_replies(**override):
    replies = {
        "ListInhibitors": [[]],
        "CanSuspend": ["yes"],
        "CanReboot": ["yes"],
        "CanPowerOff": ["yes"],
        "GetUser": ["/org/freedesktop/login1/user/_1000"],
        "Get": [["2", "/org/freedesktop/login1/session/_32"]],
        "LockSession": [],
        "Suspend": [],
        "Reboot": [],
        "PowerOff": [],
    }
    replies.update(override)
    return replies


def fake_system(*, system=None, session=None, system_down=None, session_down=None):
    """Un SystemPower branché sur de faux bus ; `log` note chaque appel, dans l'ordre."""
    log: list[tuple] = []
    power_ = SystemPower(
        lambda: FakeBus(
            system if system is not None else logind_replies(), log, connect_error=system_down
        ),
        lambda: FakeBus(session or {}, log, connect_error=session_down),
        uid=1000,
        hostname=lambda: "mars-pc",
    )
    return SimpleNamespace(power=power_, log=log)


def calls(log, member):
    return [entry for entry in log if len(entry) > 3 and entry[3] == member]


async def test_a_pc_where_everything_is_allowed_shows_four_available_controls():
    system = fake_system()
    state = await system.power.probe()
    assert state.hostname == "mars-pc"
    assert all(c.ok for c in state.controls.values())
    assert list(state.controls) == ["lock", "suspend", "reboot", "poweroff"]
    assert state.blockers == ()
    assert state.unavailable is None
    # le nom de l'utilisateur de l'agent : logind le connaît par son numéro, jamais par un nom saisi
    [(_, _, _, _, signature, body)] = calls(system.log, "GetUser")
    assert (signature, body) == ("u", [1000])


async def test_a_program_that_blocks_the_shutdown_is_named_and_the_sleep_stays_possible():
    inhibitors = [["shutdown", "Mises à jour", "Installation", "block", 0, 7]]
    system = fake_system(
        system=logind_replies(
            ListInhibitors=[inhibitors], CanReboot=["challenge"], CanPowerOff=["challenge"]
        )
    )
    state = await system.power.probe()
    assert state.controls["poweroff"] == Control(False, "Bloqué par « Mises à jour »")
    assert state.controls["reboot"] == Control(False, "Bloqué par « Mises à jour »")
    assert state.controls["suspend"].ok is True  # le verrou ne concerne que l'extinction
    assert [b.who for b in state.blockers] == ["Mises à jour"]


async def test_each_control_gets_its_own_reason():
    system = fake_system(
        system=logind_replies(
            CanSuspend=["na"], CanReboot=["no"], CanPowerOff=BusError("CanPowerOff : x")
        )
    )
    state = await system.power.probe()
    assert state.controls["suspend"] == Control(False, "Indisponible sur ce PC")
    assert state.controls["reboot"] == Control(False, "Refusé par le système")
    assert state.controls["poweroff"] == Control(False, "Réponse inattendue du système")


async def test_without_logind_only_the_gnome_lock_screen_can_still_be_offered():
    present = {"NameHasOwner": [True]}
    system = fake_system(system_down=BusError("Bus système D-Bus injoignable"), session=present)
    state = await system.power.probe()
    assert state.unavailable == "Le service de session du PC (logind) ne répond pas."
    assert state.controls["lock"] == Control(True)
    for name in ("suspend", "reboot", "poweroff"):
        assert state.controls[name] == Control(False, "logind injoignable")
    [(_, _, _, _, signature, body)] = calls(system.log, "NameHasOwner")
    assert (signature, body) == ("s", ["org.gnome.ScreenSaver"])

    nothing = fake_system(
        system_down=BusError("Bus système D-Bus injoignable"),
        session_down=BusError("Bus de session D-Bus injoignable"),
    )
    assert (await nothing.power.probe()).controls["lock"] == Control(
        False, "Aucune session graphique"
    )


async def test_locking_needs_a_graphical_session_or_the_gnome_lock_screen():
    nobody = logind_replies(GetUser=BusError("GetUser : org.freedesktop.login1.NoSuchUser"))
    with_screensaver = fake_system(system=nobody, session={"NameHasOwner": [True]})
    assert (await with_screensaver.power.probe()).controls["lock"].ok is True
    without = fake_system(system=nobody, session={"NameHasOwner": [False]})
    assert (await without.power.probe()).controls["lock"] == Control(
        False, "Aucune session graphique"
    )
    headless = fake_system(
        system=logind_replies(Get=[["", "/"]]), session={"NameHasOwner": [False]}
    )
    assert (await headless.power.probe()).controls[
        "lock"
    ].ok is False  # identifiant vide : pas d'écran


# --- les ordres --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("control", "member"), [("suspend", "Suspend"), ("reboot", "Reboot"), ("poweroff", "PowerOff")]
)
async def test_cutting_the_power_calls_logind_without_ever_asking_for_a_password(control, member):
    system = fake_system()
    await system.power.perform(control)
    [(destination, path, interface, called, signature, body)] = calls(system.log, member)
    assert (destination, path, interface) == (
        "org.freedesktop.login1",
        "/org/freedesktop/login1",
        "org.freedesktop.login1.Manager",
    )
    assert (called, signature, body) == (member, "b", [False])  # interactive = false
    assert system.log[-1] == ("disconnect",)


async def test_a_refusal_becomes_a_clear_french_message_and_the_right_code():
    refused = fake_system(
        system=logind_replies(PowerOff=BusError("PowerOff : x", name=AUTH_REQUIRED))
    )
    with pytest.raises(PowerError) as info:
        await refused.power.perform("poweroff")
    assert info.value.code == "forbidden"
    assert "mot de passe administrateur" in str(info.value)

    other = fake_system(
        system=logind_replies(Suspend=BusError("Suspend : x", name=power.SLEEP_UNSUPPORTED))
    )
    with pytest.raises(PowerError) as info:
        await other.power.perform("suspend")
    assert (info.value.code, str(info.value)) == (
        "failed",
        "Ce PC ne sait pas se mettre en veille.",
    )


async def test_the_program_that_blocks_is_named_when_logind_refuses():
    inhibitors = [["shutdown", "Mises à jour", "Installation", "block", 0, 7]]
    system = fake_system(
        system=logind_replies(
            PowerOff=BusError("PowerOff : x", name=AUTH_REQUIRED), ListInhibitors=[inhibitors]
        )
    )
    with pytest.raises(PowerError, match="« Mises à jour » empêche l'extinction"):
        await system.power.perform("poweroff")
    # ... mais un verrou sans rapport avec l'extinction n'est pas accusé à tort
    unrelated = [["sleep", "Lecteur vidéo", "Film", "block", 1000, 8]]
    other = fake_system(
        system=logind_replies(
            PowerOff=BusError("PowerOff : x", name=AUTH_REQUIRED), ListInhibitors=[unrelated]
        )
    )
    with pytest.raises(PowerError, match="mot de passe administrateur"):
        await other.power.perform("poweroff")


async def test_an_unreachable_logind_is_reported_and_never_leaks_an_exception():
    system = fake_system(system_down=BusError("Bus système D-Bus injoignable (FileNotFoundError)."))
    with pytest.raises(PowerError) as info:
        await system.power.perform("reboot")
    assert info.value.code == "unavailable"
    assert "logind" in str(info.value)


async def test_locking_asks_logind_to_lock_the_graphical_session_of_the_user():
    system = fake_system()
    await system.power.perform("lock")
    [(_, path, interface, member, signature, body)] = calls(system.log, "LockSession")
    assert (path, interface, member, signature, body) == (
        "/org/freedesktop/login1",
        "org.freedesktop.login1.Manager",
        "LockSession",
        "s",
        ["2"],  # l'identifiant de la session, tel que la propriété « Display » le donne
    )
    assert not calls(system.log, "Lock")  # pas de repli quand logind a répondu


async def test_locking_falls_back_to_the_gnome_lock_screen():
    broken = logind_replies(LockSession=BusError("LockSession : x", name=ACCESS_DENIED))
    system = fake_system(system=broken, session={"Lock": []})
    await system.power.perform("lock")
    [(destination, path, interface, member, signature, body)] = calls(system.log, "Lock")
    assert (destination, path, interface, member, signature, body) == (
        "org.gnome.ScreenSaver",
        "/org/gnome/ScreenSaver",
        "org.gnome.ScreenSaver",
        "Lock",
        "",
        [],
    )


async def test_locking_without_any_way_to_do_it_says_why():
    nobody = logind_replies(
        GetUser=BusError("GetUser : x", name="org.freedesktop.login1.NoSuchUser")
    )
    system = fake_system(system=nobody, session={"Lock": BusError("Lock : x")})
    with pytest.raises(PowerError, match="Aucune session graphique"):
        await system.power.perform("lock")
    down = fake_system(
        system_down=BusError("Bus système D-Bus injoignable"),
        session_down=BusError("Bus de session D-Bus injoignable"),
    )
    with pytest.raises(PowerError, match="logind"):
        await down.power.perform("lock")


# --- les actions : liste blanche, confirmation, un seul ordre à la fois ----------------------------


class RecordingBackend:
    def __init__(self, error: PowerError | None = None, delay: float = 0.0):
        self.performed: list[str] = []
        self.error = error
        self.delay = delay

    async def probe(self):
        raise AssertionError("les actions ne lisent pas l'état")

    async def perform(self, control):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        self.performed.append(control)


def power_registry(backend, *, demo=False):
    registry = ActionRegistry()
    register_power_actions(registry, backend, hostname=lambda: "mars-pc", demo=demo)
    return registry


async def act(registry, name, params=None, *, confirm=None, conn=1, act_id="a1"):
    return await registry.invoke(
        Act(act_id, name, params or {}, confirm), ActionContext(conn, "192.0.2.5")
    )


def test_four_actions_exist_and_only_the_last_three_are_sensitive():
    registry = power_registry(RecordingBackend())
    assert registry.names == ["power.lock", "power.poweroff", "power.reboot", "power.suspend"]
    assert [registry.is_sensitive(n) for n in registry.names] == [False, True, True, True]


async def test_locking_needs_no_confirmation():
    backend = RecordingBackend()
    reply = await act(power_registry(backend), "power.lock")
    assert reply["ok"] is True
    assert reply["r"] == {"requested": "lock"}
    assert backend.performed == ["lock"]


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("power.suspend", "Mettre « mars-pc » en veille"),
        ("power.reboot", "Redémarrer « mars-pc » (les programmes ouverts seront fermés)"),
        ("power.poweroff", "Éteindre « mars-pc » (les programmes ouverts seront fermés)"),
    ],
)
async def test_cutting_the_power_asks_the_screen_first_and_names_the_pc(name, label):
    backend = RecordingBackend()
    registry = power_registry(backend)
    asked = await act(registry, name)
    assert asked["e"]["code"] == "confirm_required"
    assert asked["confirm"]["label"] == label
    assert backend.performed == []  # rien n'a eu lieu

    done = await act(registry, name, confirm=asked["confirm"]["token"], act_id="a2")
    assert done["ok"] is True
    assert backend.performed == [name.removeprefix("power.")]


async def test_a_confirmation_is_good_for_one_action_one_connection_one_time():
    backend = RecordingBackend()
    registry = power_registry(backend)
    token = (await act(registry, "power.suspend"))["confirm"]["token"]
    elsewhere = await act(registry, "power.suspend", confirm=token, conn=2, act_id="a2")
    assert elsewhere["e"]["code"] == "confirm_invalid"  # une autre connexion ne peut pas la rejouer

    token = (await act(registry, "power.suspend", act_id="a3"))["confirm"]["token"]
    swapped = await act(registry, "power.poweroff", confirm=token, act_id="a4")
    assert swapped["e"]["code"] == "confirm_invalid"  # le jeton de la veille n'éteint pas le PC

    token = (await act(registry, "power.reboot", act_id="a5"))["confirm"]["token"]
    assert (await act(registry, "power.reboot", confirm=token, act_id="a6"))["ok"] is True
    again = await act(registry, "power.reboot", confirm=token, act_id="a7")
    assert again["e"]["code"] == "confirm_invalid"  # à usage unique
    assert backend.performed == ["reboot"]


async def test_no_parameter_is_accepted_so_nothing_can_be_smuggled_in():
    backend = RecordingBackend()
    registry = power_registry(backend)
    for name in registry.names:
        reply = await act(registry, name, {"command": "rm -rf /"})
        assert reply["e"]["code"] == "bad_params"
    assert backend.performed == []


async def test_a_refusal_reaches_the_screen_with_its_message_and_code():
    backend = RecordingBackend(PowerError("« Mises à jour » empêche l'extinction.", "forbidden"))
    registry = power_registry(backend)
    token = (await act(registry, "power.poweroff"))["confirm"]["token"]
    reply = await act(registry, "power.poweroff", confirm=token, act_id="a2")
    assert reply["ok"] is False
    assert reply["e"] == {"code": "forbidden", "msg": "« Mises à jour » empêche l'extinction."}


async def test_two_power_orders_never_run_at_the_same_time():
    backend = RecordingBackend(delay=0.3)
    registry = power_registry(backend)
    first = asyncio.create_task(act(registry, "power.lock"))
    await asyncio.sleep(0.05)
    second = await act(registry, "power.lock", act_id="a2")
    assert second["e"]["code"] == "busy"
    assert (await first)["ok"] is True
    assert backend.performed == ["lock"]
    assert (await act(registry, "power.lock", act_id="a3"))["ok"] is True  # libre de nouveau


async def test_the_demo_labels_say_that_nothing_will_really_happen():
    registry = power_registry(RecordingBackend(), demo=True)
    asked = await act(registry, "power.poweroff")
    assert asked["confirm"]["label"].endswith("— démo, sans effet réel")


async def test_a_hostile_hostname_cannot_break_the_confirmation_text():
    registry = ActionRegistry()
    register_power_actions(
        registry, RecordingBackend(), hostname=lambda: "pc\x00\n" + "x" * 100, demo=False
    )
    label = (await act(registry, "power.poweroff"))["confirm"]["label"]
    assert "\x00" not in label
    assert "\n" not in label
    assert len(label) < 120


# --- le fournisseur -------------------------------------------------------------------------------


async def test_the_provider_publishes_the_state_and_follows_changes():
    states = [
        PowerState("mars-pc", {n: Control(True) for n in power.CONTROLS}),
        PowerState(
            "mars-pc",
            {
                n: Control(n != "poweroff", None if n != "poweroff" else "Refusé")
                for n in power.CONTROLS
            },
        ),
    ]

    class Scripted:
        def __init__(self):
            self.index = 0

        async def probe(self):
            state = states[min(self.index, len(states) - 1)]
            self.index += 1
            return state

        async def perform(self, control):
            raise AssertionError

    published: list[dict] = []

    async def publish(data):
        published.append(data)
        return True

    provider = PowerProvider(Scripted(), interval=0.02)
    assert (provider.topic, provider.lazy) == ("power", True)
    task = asyncio.create_task(provider.run(publish))
    try:
        await eventually(lambda: len(published) >= 3, timeout=3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert published[0]["controls"]["poweroff"] == {"ok": True, "why": None}
    assert published[-1]["controls"]["poweroff"] == {"ok": False, "why": "Refusé"}


# --- la démonstration -------------------------------------------------------------------------------


async def test_the_demo_allows_everything_by_default_and_really_does_nothing():
    demo = DemoPower("full")
    state = await demo.probe()
    assert state.hostname == "pc-demo"
    assert all(c.ok for c in state.controls.values())
    await demo.perform("poweroff")
    assert demo.performed == ["poweroff"]


async def test_the_limited_demo_blocks_the_shutdown_like_a_running_update_would():
    demo = DemoPower("limited")
    state = await demo.probe()
    assert state.controls["lock"].ok is True
    assert state.controls["suspend"] == Control(False, "Indisponible sur ce PC")
    assert state.controls["poweroff"] == Control(False, "Bloqué par « Mises à jour logicielles »")
    assert [b.who for b in state.blockers] == ["Mises à jour logicielles"]
    with pytest.raises(PowerError, match="empêche l'extinction"):
        await demo.perform("poweroff")
    with pytest.raises(PowerError):
        await demo.perform("suspend")
    await demo.perform("lock")
    assert demo.performed == ["lock"]


async def test_the_down_demo_looks_like_a_pc_without_logind():
    state = await DemoPower("down").probe()
    assert state.unavailable
    assert [state.controls[n].ok for n in power.CONTROLS] == [True, False, False, False]


async def test_the_demo_mode_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("MYDOCK_DEMO_POWER", "limited")
    assert (await DemoPower().probe()).controls["suspend"].ok is False
    monkeypatch.delenv("MYDOCK_DEMO_POWER")
    assert (await DemoPower().probe()).controls["suspend"].ok is True


# --- un vrai bus D-Bus et un faux logind -------------------------------------------------------------

needs_dbus = pytest.mark.skipif(shutil.which("dbus-daemon") is None, reason="dbus-daemon absent")
SESSION_PATH = "/org/freedesktop/login1/session/_32"
USER_PATH = "/org/freedesktop/login1/user/_1000"


class FakeManager(ServiceInterface):
    """org.freedesktop.login1.Manager, aux noms et aux signatures près du vrai."""

    def __init__(self):
        super().__init__("org.freedesktop.login1.Manager")
        self.can = {"CanSuspend": "yes", "CanReboot": "yes", "CanPowerOff": "yes"}
        self.errors: dict[str, DBusError] = {}
        self.calls: list[tuple] = []
        self.inhibitors: list[list] = []
        self.user_known = True

    def _maybe_fail(self, name):
        if name in self.errors:
            raise self.errors[name]

    @method()
    def CanSuspend(self) -> "s":
        return self.can["CanSuspend"]

    @method()
    def CanReboot(self) -> "s":
        return self.can["CanReboot"]

    @method()
    def CanPowerOff(self) -> "s":
        return self.can["CanPowerOff"]

    @method()
    def Suspend(self, interactive: "b"):
        self.calls.append(("Suspend", interactive))
        self._maybe_fail("Suspend")

    @method()
    def Reboot(self, interactive: "b"):
        self.calls.append(("Reboot", interactive))
        self._maybe_fail("Reboot")

    @method()
    def PowerOff(self, interactive: "b"):
        self.calls.append(("PowerOff", interactive))
        self._maybe_fail("PowerOff")

    @method()
    def GetUser(self, uid: "u") -> "o":
        self.calls.append(("GetUser", uid))
        if not self.user_known:
            raise DBusError("org.freedesktop.login1.NoSuchUser", "No such user")
        return USER_PATH

    @method()
    def LockSession(self, session_id: "s"):
        self.calls.append(("LockSession", session_id))
        self._maybe_fail("LockSession")

    @method()
    def ListInhibitors(self) -> "a(ssssuu)":
        return self.inhibitors


class FakeUser(ServiceInterface):
    def __init__(self):
        super().__init__("org.freedesktop.login1.User")

    @dbus_property(access=PropertyAccess.READ)
    def Display(self) -> "(so)":
        return ["2", SESSION_PATH]


class FakeScreenSaver(ServiceInterface):
    def __init__(self):
        super().__init__("org.gnome.ScreenSaver")
        self.locked = 0

    @method()
    def Lock(self):
        self.locked += 1


@pytest.fixture
def buses(tmp_path):
    """Deux dbus-daemon privés : un « système » (logind) et un « session » (écran de verrouillage)."""
    processes = []
    addresses = []
    for name in ("system", "session"):
        process = subprocess.Popen(
            [
                "dbus-daemon",
                "--session",
                "--nofork",
                "--nopidfile",
                "--print-address=1",
                f"--address=unix:path={tmp_path / name}",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        address = process.stdout.readline().strip()
        assert address.startswith("unix:"), address
        processes.append(process)
        addresses.append(address)
    yield SimpleNamespace(system=addresses[0], session=addresses[1])
    for process in processes:
        process.terminate()
        process.wait(timeout=5)


async def open_logind(address: str, **kwargs):
    manager = FakeManager()
    bus = await MessageBus(bus_address=address).connect()
    bus.export("/org/freedesktop/login1", manager)
    bus.export(USER_PATH, FakeUser())
    await bus.request_name("org.freedesktop.login1")
    return bus, manager


def real_power(buses) -> SystemPower:
    return SystemPower(
        lambda: DbusFastBus(buses.system),
        lambda: DbusFastBus(buses.session),
        uid=1000,
        hostname=lambda: "mars-pc",
    )


@needs_dbus
async def test_a_real_bus_gives_the_rights_the_system_gives(buses):
    bus, manager = await open_logind(buses.system)
    manager.can["CanSuspend"] = "na"
    manager.can["CanPowerOff"] = "challenge"
    manager.inhibitors = [["shutdown:sleep", "Mises à jour", "Installation", "block", 0, 7]]
    try:
        state = await real_power(buses).probe()
    finally:
        bus.disconnect()
    assert state.unavailable is None
    assert state.controls["lock"].ok is True  # la session « 2 » existe : Display la donne
    assert state.controls["suspend"] == Control(False, "Bloqué par « Mises à jour »")
    assert state.controls["reboot"].ok is True
    assert state.controls["poweroff"] == Control(False, "Bloqué par « Mises à jour »")
    assert [(b.who, b.why, b.scope) for b in state.blockers] == [
        ("Mises à jour", "Installation", ("shutdown", "sleep"))
    ]
    assert ("GetUser", 1000) in manager.calls


@needs_dbus
async def test_a_real_bus_receives_the_orders_without_interaction(buses):
    bus, manager = await open_logind(buses.system)
    try:
        backend = real_power(buses)
        await backend.perform("suspend")
        await backend.perform("reboot")
        await backend.perform("poweroff")
        await backend.perform("lock")
    finally:
        bus.disconnect()
    assert manager.calls == [
        ("Suspend", False),
        ("Reboot", False),
        ("PowerOff", False),
        ("GetUser", 1000),
        ("LockSession", "2"),
    ]


@needs_dbus
async def test_a_real_refusal_is_translated_from_its_error_name(buses):
    bus, manager = await open_logind(buses.system)
    manager.errors["PowerOff"] = DBusError(AUTH_REQUIRED, "Interactive authentication required.")
    manager.errors["Suspend"] = DBusError("org.freedesktop.login1.OperationInProgress", "En cours")
    manager.inhibitors = []
    try:
        backend = real_power(buses)
        with pytest.raises(PowerError) as info:
            await backend.perform("poweroff")
        assert (info.value.code, "mot de passe administrateur" in str(info.value)) == (
            "forbidden",
            True,
        )
        with pytest.raises(PowerError, match="déjà en cours"):
            await backend.perform("suspend")

        manager.inhibitors = [["shutdown", "Copie de fichiers", "En cours", "block", 1000, 9]]
        with pytest.raises(PowerError, match="« Copie de fichiers » empêche l'extinction"):
            await backend.perform("poweroff")
    finally:
        bus.disconnect()


@needs_dbus
async def test_a_real_gnome_lock_screen_is_the_fallback_when_logind_has_no_session(buses):
    logind, manager = await open_logind(buses.system)
    manager.user_known = False
    session = await MessageBus(bus_address=buses.session).connect()
    screensaver = FakeScreenSaver()
    session.export("/org/gnome/ScreenSaver", screensaver)
    await session.request_name("org.gnome.ScreenSaver")
    try:
        backend = real_power(buses)
        assert (await backend.probe()).controls["lock"].ok is True
        await backend.perform("lock")
    finally:
        logind.disconnect()
        session.disconnect()
    assert screensaver.locked == 1
    assert ("LockSession", "2") not in manager.calls


@needs_dbus
async def test_a_real_system_without_logind_reports_it_instead_of_failing(buses):
    backend = real_power(buses)  # personne n'a pris le nom « org.freedesktop.login1 »
    state = await backend.probe()
    assert state.unavailable is not None
    assert state.controls["poweroff"] == Control(False, "logind injoignable")
    with pytest.raises(PowerError, match="logind"):
        await backend.perform("poweroff")
    absent = SystemPower(
        lambda: DbusFastBus("unix:path=/inexistant/bus"), lambda: DbusFastBus("unix:path=/x/y")
    )
    with pytest.raises(PowerError, match="logind"):
        await absent.perform("reboot")


@needs_dbus
async def test_the_provider_reads_a_real_bus_and_only_while_it_is_asked_to(buses):
    bus, manager = await open_logind(buses.system)
    provider = PowerProvider(real_power(buses), interval=0.05)
    published: list[dict] = []

    async def publish(data):
        published.append(data)
        return True

    task = asyncio.create_task(provider.run(publish))
    try:
        await eventually(
            lambda: published and published[-1]["controls"]["poweroff"]["ok"], timeout=5
        )
        manager.can["CanPowerOff"] = "no"
        await eventually(lambda: not published[-1]["controls"]["poweroff"]["ok"], timeout=5)
        assert published[-1]["controls"]["poweroff"]["why"] == "Refusé par le système"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        bus.disconnect()


def test_dbus_variants_in_a_reply_are_unwrapped_by_the_bus_helper():
    """`Display` arrive de dbus-fast sous forme de Variant : le code ne doit pas en dépendre."""
    from mydock_agent.collectors.music import unwrap

    assert unwrap(Variant("(so)", ["2", SESSION_PATH])) == ["2", SESSION_PATH]


def test_without_a_backend_the_provider_talks_to_the_real_system():
    assert isinstance(PowerProvider().backend, SystemPower)
