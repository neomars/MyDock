"""Thème « power » : verrouiller, mettre en veille, redémarrer ou éteindre le PC.

Tout passe par logind, le service de session de systemd, que l'agent interroge par D-Bus (bus
système) : c'est ce que font `systemctl poweroff` et le menu du bureau, sans lancer de commande ni
de shell. L'agent ne peut faire que ce que le système autorise à l'utilisateur connecté, et il
demande toujours une réponse sans mot de passe (`interactive = false`) :

  * si le système exigerait un mot de passe administrateur (un autre utilisateur est connecté, un
    programme s'oppose à l'extinction…), le bouton est grisé avec la raison, et l'action échoue
    proprement si on la tente quand même ;
  * rien n'est jamais forcé : un programme qui bloque l'extinction (mise à jour en cours) est
    nommé à l'écran, le dock ne le contourne pas.

« Verrouiller » envoie à la session graphique le signal « Lock » de logind (GNOME, KDE, xss-lock…
y répondent), avec l'écran de verrouillage de GNOME en repli.

Le thème est « paresseux » : les droits ne sont relus que tant que la page Alimentation est ouverte.
Redémarrer, éteindre et mettre en veille sont des actions sensibles (confirmation à l'écran ET
jeton à usage unique) ; verrouiller ne l'est pas, on le défait en tapant son mot de passe.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from mydock_agent.collectors.music import DBUS_NAME, Bus, BusError, DbusFastBus, clean_text
from mydock_agent.hub import Provider, Publish
from mydock_common.actions import ActionContext, ActionError, ActionRegistry, NoParams

log = logging.getLogger(__name__)

LOGIND = "org.freedesktop.login1"
LOGIND_PATH = "/org/freedesktop/login1"
MANAGER_IFACE = "org.freedesktop.login1.Manager"
USER_IFACE = "org.freedesktop.login1.User"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
SCREENSAVER = "org.gnome.ScreenSaver"
SCREENSAVER_PATH = "/org/gnome/ScreenSaver"

REFRESH_SECONDS = 5.0
MAX_BLOCKERS = 5

CONTROLS = ("lock", "suspend", "reboot", "poweroff")
#: Les trois opérations qui coupent le PC : leur méthode logind, et celle qui dit si c'est permis.
DO = {"suspend": "Suspend", "reboot": "Reboot", "poweroff": "PowerOff"}
CAN = {"suspend": "CanSuspend", "reboot": "CanReboot", "poweroff": "CanPowerOff"}
#: Ce qu'un verrou « block » de logind empêche, selon l'opération.
INHIBITED_BY = {"suspend": "sleep", "reboot": "shutdown", "poweroff": "shutdown"}
BLOCKED = {"suspend": "la mise en veille", "reboot": "le redémarrage", "poweroff": "l'extinction"}

AUTH_ERRORS = frozenset(
    {
        "org.freedesktop.DBus.Error.InteractiveAuthorizationRequired",
        "org.freedesktop.DBus.Error.AccessDenied",
        "org.freedesktop.DBus.Error.AuthFailed",
    }
)
#: Logind n'a pas répondu du tout (le nom vide est celui d'une connexion ou d'un délai en échec).
UNREACHABLE_ERRORS = frozenset(
    {
        "",
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.NameHasNoOwner",
        "org.freedesktop.DBus.Error.NoReply",
        "org.freedesktop.DBus.Error.Disconnected",
    }
)
IN_PROGRESS = "org.freedesktop.login1.OperationInProgress"
SLEEP_UNSUPPORTED = "org.freedesktop.login1.SleepVerbNotSupported"
NO_LOGIND = "Le service de session du PC (logind) ne répond pas."


class PowerError(Exception):
    """L'opération n'a pas eu lieu ; le message est affichable à l'écran."""

    def __init__(self, message: str, code: str = "failed"):
        super().__init__(message)
        self.code = code


# --- ce que le système permet ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Control:
    ok: bool
    why: str | None = None  # pourquoi c'est grisé (court : il tient sous le bouton)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "why": self.why}


@dataclass(frozen=True, slots=True)
class Blocker:
    """Un programme qui s'oppose (verrou « block » de logind) à l'extinction ou à la veille."""

    who: str
    why: str
    scope: tuple[str, ...]  # « shutdown » et/ou « sleep »

    def to_dict(self) -> dict[str, Any]:
        return {"who": self.who, "why": self.why, "scope": list(self.scope)}


@dataclass(frozen=True, slots=True)
class PowerState:
    hostname: str
    controls: dict[str, Control]
    blockers: tuple[Blocker, ...] = ()
    unavailable: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "hostname": self.hostname,
            "controls": {name: self.controls[name].to_dict() for name in CONTROLS},
            "blockers": [b.to_dict() for b in self.blockers],
        }
        if self.unavailable:
            data["unavailable"] = self.unavailable
        return data


def quoted(blockers: Sequence[Blocker]) -> str:
    """« A », « A » et « B », « A », « B » et 2 autres : de quoi nommer les responsables."""
    names = []
    for blocker in blockers:
        label = f"« {blocker.who} »"
        if label not in names:
            names.append(label)
    if len(names) <= 2:
        return " et ".join(names)
    return f"{names[0]}, {names[1]} et {len(names) - 2} autre{'s' if len(names) > 3 else ''}"


def judge(answer: str, blockers: Sequence[Blocker]) -> Control:
    """La réponse de logind (« yes », « challenge », « no », « na ») en droit affichable."""
    if answer == "yes":
        return Control(True)
    if blockers:
        return Control(False, f"Bloqué par {quoted(blockers)}")
    reasons = {
        "challenge": "Mot de passe exigé par le système",
        "no": "Refusé par le système",
        "na": "Indisponible sur ce PC",
    }
    return Control(False, reasons.get(answer, "Réponse inattendue du système"))


def parse_blockers(items: Any) -> tuple[Blocker, ...]:
    """Les verrous « block » qui touchent l'extinction ou la veille, dans `ListInhibitors`.

    Les textes viennent de programmes quelconques de la session : nettoyés et tronqués.
    """
    found: list[Blocker] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, list) or len(item) < 4 or item[3] != "block":
            continue
        scope = tuple(s for s in ("shutdown", "sleep") if s in str(item[0]).split(":"))
        if scope:
            who = clean_text(item[1], 60) or "un programme"
            found.append(Blocker(who, clean_text(item[2], 100) or "", scope))
    return tuple(found[:MAX_BLOCKERS])


def explain_failure(control: str, name: str, blockers: Sequence[Blocker]) -> str:
    """Le message d'un refus de logind, d'après le nom de l'erreur D-Bus (jamais d'après son texte)."""
    if blockers:
        verb = "empêche" if len(blockers) == 1 else "empêchent"
        return f"{quoted(blockers)} {verb} {BLOCKED.get(control, 'cette opération')}."
    if name in AUTH_ERRORS:
        return (
            "Le système exige un mot de passe administrateur pour cette action : "
            "elle est refusée depuis le dock."
        )
    if name == IN_PROGRESS:
        return "Une extinction, un redémarrage ou une veille est déjà en cours."
    if name == SLEEP_UNSUPPORTED:
        return "Ce PC ne sait pas se mettre en veille."
    if name in UNREACHABLE_ERRORS:
        return NO_LOGIND
    return f"Le système a refusé l'opération ({name.rsplit('.', 1)[-1]})."


# --- le système ---------------------------------------------------------------------------------


class PowerBackend(Protocol):
    async def probe(self) -> PowerState: ...
    async def perform(self, control: str) -> None: ...


@asynccontextmanager
async def connected(factory: Callable[[], Bus]) -> AsyncIterator[Bus]:
    bus = factory()
    await bus.connect()
    try:
        yield bus
    finally:
        await bus.disconnect()


class SystemPower:
    """L'alimentation du vrai PC, par logind. `system_bus` et `session_bus` : à remplacer en test."""

    def __init__(
        self,
        system_bus: Callable[[], Bus] | None = None,
        session_bus: Callable[[], Bus] | None = None,
        *,
        uid: int | None = None,
        hostname: Callable[[], str] = socket.gethostname,
    ):
        self._system_bus = system_bus or (lambda: DbusFastBus(system=True))
        self._session_bus = session_bus or (lambda: DbusFastBus())
        self._uid = os.getuid() if uid is None else uid
        self._hostname = hostname

    # --- lecture ---

    async def _blockers(self, bus: Bus) -> tuple[Blocker, ...]:
        try:
            (items,) = await bus.call(LOGIND, LOGIND_PATH, MANAGER_IFACE, "ListInhibitors")
        except BusError:
            return ()
        return parse_blockers(items)

    async def _display_session(self, bus: Bus) -> str | None:
        """La session graphique de l'utilisateur de l'agent (celle qui porte l'écran verrouillable)."""
        try:
            (user,) = await bus.call(
                LOGIND, LOGIND_PATH, MANAGER_IFACE, "GetUser", "u", [self._uid]
            )
            (display,) = await bus.call(
                LOGIND, str(user), PROPS_IFACE, "Get", "ss", [USER_IFACE, "Display"]
            )
        except BusError:
            return None
        # `Display` est une structure (identifiant, chemin) ; sans session graphique l'identifiant est vide.
        session = display[0] if isinstance(display, list) and display else ""
        return str(session) or None

    async def _screensaver_present(self) -> bool:
        try:
            async with connected(self._session_bus) as bus:
                (present,) = await bus.call(
                    DBUS_NAME,
                    "/org/freedesktop/DBus",
                    DBUS_NAME,
                    "NameHasOwner",
                    "s",
                    [SCREENSAVER],
                )
        except BusError:
            return False
        return present is True

    async def _read(self) -> tuple[tuple[Blocker, ...], dict[str, str], str | None]:
        """Ce que logind répond ; `BusError` s'il ne répond pas du tout (bus injoignable, service absent)."""
        async with connected(self._system_bus) as bus:
            blockers = await self._blockers(bus)
            answers: dict[str, str] = {}
            silent = 0
            for control, method in CAN.items():
                try:
                    (answer,) = await bus.call(LOGIND, LOGIND_PATH, MANAGER_IFACE, method)
                except BusError as exc:
                    answer = "?"
                    silent += exc.name in UNREACHABLE_ERRORS
                answers[control] = str(answer)
            if silent == len(CAN):
                raise BusError("logind ne répond pas", name="org.freedesktop.DBus.Error.NoReply")
            return blockers, answers, await self._display_session(bus)

    async def probe(self) -> PowerState:
        hostname = clean_text(self._hostname(), 40) or "ce PC"
        try:
            blockers, answers, session = await self._read()
        except BusError:
            lock = (
                Control(True)
                if await self._screensaver_present()
                else Control(False, "Aucune session graphique")
            )
            down = Control(False, "logind injoignable")
            return PowerState(
                hostname,
                {"lock": lock, "suspend": down, "reboot": down, "poweroff": down},
                unavailable=NO_LOGIND,
            )
        if session or await self._screensaver_present():
            lock = Control(True)
        else:
            lock = Control(False, "Aucune session graphique")
        controls = {"lock": lock}
        for control in CAN:
            relevant = [b for b in blockers if INHIBITED_BY[control] in b.scope]
            controls[control] = judge(answers[control], relevant)
        return PowerState(hostname, controls, blockers)

    # --- ordres ---

    async def perform(self, control: str) -> None:
        if control == "lock":
            await self._lock()
            return
        try:
            async with connected(self._system_bus) as bus:
                try:
                    await bus.call(LOGIND, LOGIND_PATH, MANAGER_IFACE, DO[control], "b", [False])
                except BusError as exc:
                    blockers = [
                        b for b in await self._blockers(bus) if INHIBITED_BY[control] in b.scope
                    ]
                    code = "forbidden" if exc.name in AUTH_ERRORS else "failed"
                    raise PowerError(explain_failure(control, exc.name, blockers), code) from None
        except BusError as exc:  # logind n'a pas pu être joint du tout
            raise PowerError(explain_failure(control, exc.name, ()), "unavailable") from None

    async def _lock(self) -> None:
        problem = ""
        try:
            async with connected(self._system_bus) as bus:
                session = await self._display_session(bus)
                if session:
                    await bus.call(
                        LOGIND, LOGIND_PATH, MANAGER_IFACE, "LockSession", "s", [session]
                    )
                    return
                problem = "Aucune session graphique n'est ouverte pour cet utilisateur."
        except BusError as exc:
            problem = explain_failure("lock", exc.name, ())
        # Repli : l'écran de verrouillage de GNOME, sur le bus de session.
        try:
            async with connected(self._session_bus) as bus:
                await bus.call(SCREENSAVER, SCREENSAVER_PATH, SCREENSAVER, "Lock")
                return
        except BusError:
            pass
        raise PowerError(problem or "Impossible de verrouiller la session.")


# --- le fournisseur ----------------------------------------------------------------------------


class PowerProvider(Provider):
    topic = "power"
    lazy = True

    def __init__(self, backend: PowerBackend | None = None, *, interval: float = REFRESH_SECONDS):
        self._backend: PowerBackend = backend or SystemPower()
        self._interval = interval

    @property
    def backend(self) -> PowerBackend:
        return self._backend

    async def run(self, publish: Publish) -> None:
        while True:
            state = await self._backend.probe()
            await publish(state.to_dict())
            await asyncio.sleep(self._interval)


# --- actions -----------------------------------------------------------------------------------


def register_power_actions(
    registry: ActionRegistry,
    backend: PowerBackend,
    *,
    hostname: Callable[[], str] = socket.gethostname,
    demo: bool = False,
) -> None:
    """`power.lock` (libre) puis `power.suspend`, `power.reboot`, `power.poweroff` (confirmées)."""
    busy = asyncio.Lock()  # jamais deux ordres d'alimentation en même temps

    def pc() -> str:
        return f"« {clean_text(hostname(), 40) or 'ce PC'} »"

    def label_for(control: str) -> Callable[[Any], str]:
        def label(_params: Any) -> str:
            text = {
                "suspend": f"Mettre {pc()} en veille",
                "reboot": f"Redémarrer {pc()} (les programmes ouverts seront fermés)",
                "poweroff": f"Éteindre {pc()} (les programmes ouverts seront fermés)",
            }[control]
            return f"{text} — démo, sans effet réel" if demo else text

        return label

    def handler_for(control: str):
        async def handler(ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
            if busy.locked():
                raise ActionError("busy", "Une autre commande d'alimentation est en cours.")
            async with busy:
                log.warning("Alimentation : « %s » demandé depuis %s", control, ctx.peer)
                try:
                    await backend.perform(control)
                except PowerError as exc:
                    raise ActionError(exc.code, str(exc)) from None
            return {"requested": control}

        return handler

    registry.register("power.lock", handler_for("lock"), timeout=8.0)
    for control in ("suspend", "reboot", "poweroff"):
        registry.register(
            f"power.{control}",
            handler_for(control),
            sensitive=True,
            label=label_for(control),
            timeout=12.0,
        )


__all__ = [
    "CONTROLS",
    "Blocker",
    "Control",
    "PowerBackend",
    "PowerError",
    "PowerProvider",
    "PowerState",
    "SystemPower",
    "register_power_actions",
]
