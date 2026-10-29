"""Actions de diagnostic de l'agent, sans effet sur le PC."""

from __future__ import annotations

import time

from mydock_common.actions import ActionContext, ActionRegistry, NoParams


def register_builtin(registry: ActionRegistry, *, demo: bool = False) -> None:
    """Actions de diagnostic, sans effet sur le PC."""

    async def ping(_ctx: ActionContext, _params: NoParams) -> dict[str, int]:
        return {"agent_ms": int(time.time() * 1000)}

    registry.register("diag.ping", ping, timeout=2.0)

    if demo:

        async def confirm_demo(_ctx: ActionContext, _params: NoParams) -> dict[str, bool]:
            return {"done": True}

        registry.register(
            "diag.confirm_demo",
            confirm_demo,
            sensitive=True,
            label="Tester la confirmation (démo, sans effet)",
        )
