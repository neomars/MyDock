"""Thème « procs » : les processus qui pèsent le plus, et l'arrêt confirmé d'un processus.

Échantillonné toutes les 2 s seulement tant que la page Système est ouverte (thème paresseux).
L'arrêt d'un processus est une action sensible : confirmation à l'écran ET jeton à usage unique,
puis l'agent revérifie tout lui-même (le dock n'est jamais cru sur parole) :

  * le processus existe encore et c'est bien le même (heure de démarrage identique : un PID
    réattribué entre-temps ne sera pas tué par erreur) ;
  * il appartient à l'utilisateur de l'agent, jamais à un autre ni au système ;
  * ce n'est ni l'agent, ni l'un de ses parents, ni un processus indispensable à la session.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psutil
from pydantic import BaseModel, ConfigDict, Field

from mydock_agent.hub import Provider, Publish
from mydock_common.actions import ActionContext, ActionError, ActionRegistry

log = logging.getLogger(__name__)

INTERVAL = 2.0
TOP = 8
START_TOLERANCE = 1.0  # secondes d'écart admises sur l'heure de démarrage
EXIT_WAIT = 1.5

#: Processus de la session qu'on ne tue jamais d'un toucher : les arrêter ferme la session,
#: le son ou le bus de messages. (Même utilisateur, donc `killable` les distingue par le nom.)
PROTECTED_NAMES = frozenset(
    {
        "systemd",
        "(sd-pam)",
        "dbus-daemon",
        "dbus-broker",
        "dbus-broker-launch",
        "gnome-shell",
        "gnome-session-binary",
        "gnome-session-ctl",
        "gdm-wayland-session",
        "gdm-x-session",
        "gdm-session-worker",
        "Xwayland",
        "Xorg",
        "pipewire",
        "pipewire-pulse",
        "wireplumber",
        "mutter",
        "kwin_wayland",
        "kwin_x11",
        "plasmashell",
        "sddm-helper",
    }
)

# `comm` est tronqué à 15 caractères par le noyau : on protège aussi les noms ainsi coupés.
_PROTECTED = PROTECTED_NAMES | {name[:15] for name in PROTECTED_NAMES}


@dataclass(frozen=True, slots=True)
class ProcSample:
    pid: int
    name: str
    cpu: float  # % de la capacité totale du processeur (comme la jauge du dessus)
    rss: int  # octets
    user: str
    uid: int
    started: float  # epoch (s)


def protected_pids() -> frozenset[int]:
    """L'agent lui-même et tous ses parents (le gestionnaire de services qui le relance)."""
    pids = {os.getpid()}
    try:
        pids.update(p.pid for p in psutil.Process().parents())
    except psutil.Error:
        pass
    return frozenset(pids)


def is_killable(sample: ProcSample, *, uid: int, protected: frozenset[int]) -> bool:
    return (
        sample.uid == uid
        and sample.pid not in protected
        and sample.pid > 1
        and sample.name not in _PROTECTED
    )


def select_top(samples: list[ProcSample], n: int = TOP) -> list[ProcSample]:
    """Les plus gourmands en processeur ; à égalité (machine au repos), les plus gros en mémoire."""
    return sorted(samples, key=lambda s: (-s.cpu, -s.rss, s.pid))[:n]


class ProcessSource:
    """Lecture des processus par psutil. Remplacée par une fausse dans les tests."""

    def __init__(self) -> None:
        self._known: dict[int, psutil.Process] = {}
        self._cores = psutil.cpu_count() or 1

    def scan(self) -> list[ProcSample]:
        """Tous les processus visibles (appelé dans un thread). Le premier appel ne mesure rien."""
        samples = []
        alive = set()
        for pid in psutil.pids():
            proc = self._known.get(pid)
            try:
                if proc is None:
                    proc = self._known[pid] = psutil.Process(pid)
                with proc.oneshot():
                    cpu = proc.cpu_percent(None) / self._cores
                    samples.append(
                        ProcSample(
                            pid=pid,
                            name=proc.name(),
                            cpu=round(cpu, 1),
                            rss=proc.memory_info().rss,
                            user=_user_name(proc),
                            uid=proc.uids().real,
                            started=round(proc.create_time(), 2),
                        )
                    )
                alive.add(pid)
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, OSError):
                self._known.pop(pid, None)
        for gone in set(self._known) - alive:
            del self._known[gone]
        return samples

    @property
    def cores(self) -> int:
        return self._cores


def _user_name(proc: psutil.Process) -> str:
    try:
        return proc.username()
    except (KeyError, psutil.Error):
        return str(proc.uids().real)


class ProcsProvider(Provider):
    topic = "procs"
    lazy = True

    def __init__(
        self,
        source: ProcessSource | None = None,
        *,
        interval: float = INTERVAL,
        uid: int | None = None,
        protected: Callable[[], frozenset[int]] = protected_pids,
    ):
        self._source = source or ProcessSource()
        self._interval = interval
        self._uid = os.getuid() if uid is None else uid
        self._protected = protected
        self._names: dict[int, str] = {}

    def name_of(self, pid: int) -> str | None:
        """Nom du processus tel que le dernier échantillon l'a vu (pour la fenêtre de confirmation)."""
        return self._names.get(pid)

    def build(self, samples: list[ProcSample]) -> dict[str, Any]:
        protected = self._protected()
        top = select_top(samples)
        self._names = {s.pid: s.name for s in top}
        return {
            "count": len(samples),
            "cores": getattr(self._source, "cores", None),
            "procs": [
                {
                    "pid": s.pid,
                    "name": s.name,
                    "cpu": s.cpu,
                    "mem": s.rss,
                    "user": s.user,
                    "started": s.started,
                    "killable": is_killable(s, uid=self._uid, protected=protected),
                }
                for s in top
            ],
        }

    async def run(self, publish: Publish) -> None:
        await asyncio.to_thread(
            self._source.scan
        )  # amorce : psutil mesure l'écart entre deux appels
        await asyncio.sleep(min(0.5, self._interval))
        while True:
            samples = await asyncio.to_thread(self._source.scan)
            await publish(self.build(samples))
            await asyncio.sleep(self._interval)


# --- arrêt d'un processus ---------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KillParams(_Strict):
    pid: int = Field(ge=2, le=4_194_304, strict=True)
    started: float = Field(gt=0)
    force: bool = Field(default=False, strict=True)


def terminate(pid: int, started: float, force: bool, *, uid: int) -> dict[str, Any]:
    """Arrête le processus après toutes les vérifications (appelé dans un thread)."""
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            actual_start = proc.create_time()
            owner = proc.uids().real
            name = proc.name()
    except psutil.NoSuchProcess:
        raise ActionError("gone", "Ce processus n'existe plus.") from None
    except (psutil.AccessDenied, OSError):
        raise ActionError("forbidden", "Ce processus n'est pas accessible.") from None
    if abs(actual_start - started) > START_TOLERANCE:
        raise ActionError("changed", "La liste a changé : ce n'est plus le même processus.")
    if owner != uid:
        raise ActionError("forbidden", "Ce processus appartient à un autre utilisateur.")
    if pid in protected_pids():
        raise ActionError("forbidden", "Ce processus fait tourner l'agent MyDock lui-même.")
    if name in _PROTECTED:
        raise ActionError("forbidden", f"« {name} » est indispensable à la session : refusé.")
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        proc.send_signal(sig)
    except psutil.NoSuchProcess:
        return {"pid": pid, "name": name, "signal": sig.name, "gone": True}
    except psutil.AccessDenied:
        raise ActionError("forbidden", "Le système refuse d'arrêter ce processus.") from None
    try:
        proc.wait(timeout=EXIT_WAIT)
        gone = True
    except psutil.TimeoutExpired:
        gone = False
    return {"pid": pid, "name": name, "signal": sig.name, "gone": gone}


def register_system_actions(registry: ActionRegistry, provider: ProcsProvider) -> None:
    def label(params: KillParams) -> str:
        name = provider.name_of(params.pid)
        verb = "Forcer l'arrêt de" if params.force else "Terminer"
        target = f"« {name} » (PID {params.pid})" if name else f"le processus {params.pid}"
        return f"{verb} {target}"

    async def kill(ctx: ActionContext, params: KillParams) -> dict[str, Any]:
        result = await asyncio.to_thread(
            terminate, params.pid, params.started, params.force, uid=os.getuid()
        )
        log.warning(
            "Arrêt demandé depuis %s : %s (PID %d), %s, %s",
            ctx.peer,
            result["name"],
            result["pid"],
            result["signal"],
            "arrêté" if result["gone"] else "toujours là",
        )
        return result

    registry.register(
        "system.kill",
        kill,
        params=KillParams,
        sensitive=True,
        label=label,
        timeout=EXIT_WAIT + 3.0,
    )
