"""Démonstration de « power » : des droits inventés, et rien n'est jamais éteint.

`MYDOCK_DEMO_POWER` choisit le décor, pour voir les états de la page sans avoir à les provoquer :

  * `full` (défaut) : tout est permis ;
  * `limited` : pas de veille sur cette machine, et une mise à jour bloque l'extinction ;
  * `down` : logind ne répond pas.
"""

from __future__ import annotations

import asyncio
import os

from mydock_agent.collectors.power import (
    CONTROLS,
    Blocker,
    Control,
    PowerError,
    PowerState,
    explain_failure,
    judge,
)

HOSTNAME = "pc-demo"
UPDATER = Blocker(
    "Mises à jour logicielles", "Installation des mises à jour en cours", ("shutdown",)
)


class DemoPower:
    def __init__(self, mode: str | None = None):
        self._mode = (mode or os.environ.get("MYDOCK_DEMO_POWER", "full")).strip().lower()
        self.performed: list[str] = []

    async def probe(self) -> PowerState:
        if self._mode == "down":
            down = Control(False, "logind injoignable")
            return PowerState(
                HOSTNAME,
                {name: (Control(True) if name == "lock" else down) for name in CONTROLS},
                unavailable="Le service de session du PC (logind) ne répond pas.",
            )
        if self._mode == "limited":
            blocked = judge("challenge", [UPDATER])
            return PowerState(
                HOSTNAME,
                {
                    "lock": Control(True),
                    "suspend": judge("na", []),
                    "reboot": blocked,
                    "poweroff": blocked,
                },
                (UPDATER,),
            )
        return PowerState(HOSTNAME, {name: Control(True) for name in CONTROLS})

    async def perform(self, control: str) -> None:
        state = await self.probe()
        if not state.controls[control].ok:
            blockers = [UPDATER] if self._mode == "limited" and control != "suspend" else []
            raise PowerError(explain_failure(control, "", blockers), "forbidden")
        await asyncio.sleep(0.05)
        self.performed.append(control)


__all__ = ["HOSTNAME", "DemoPower"]
