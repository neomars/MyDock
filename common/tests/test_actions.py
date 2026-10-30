import asyncio

import pytest
from pydantic import BaseModel, ConfigDict, Field

from mydock_common.actions import (
    ActionContext,
    ActionError,
    ActionRegistry,
    ConfirmStore,
    NoParams,
)
from mydock_common.protocol import Act, ErrorCode


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class VolumeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: int = Field(ge=0, le=100)


def ctx(conn_id: int = 1) -> ActionContext:
    return ActionContext(conn_id, "127.0.0.1")


def act(name: str, params=None, confirm=None, act_id: str = "c1") -> Act:
    return Act(act_id, name, params or {}, confirm)


@pytest.fixture
def registry() -> ActionRegistry:
    reg = ActionRegistry(clock=FakeClock())
    calls: list[tuple[str, object]] = []
    reg.calls = calls  # type: ignore[attr-defined]

    async def set_volume(_ctx, params: VolumeParams):
        calls.append(("volume", params.level))
        return {"level": params.level}

    async def poweroff(_ctx, _params: NoParams):
        calls.append(("poweroff", None))
        return {"done": True}

    reg.register("audio.set_volume", set_volume, params=VolumeParams)
    reg.register("power.poweroff", poweroff, sensitive=True, label="Éteindre le PC")
    return reg


async def test_unknown_action_is_refused(registry):
    reply = await registry.invoke(act("rm.rf"), ctx())
    assert reply["ok"] is False
    assert reply["e"]["code"] == ErrorCode.UNKNOWN_ACTION


async def test_valid_params_reach_the_handler_and_ack_carries_timing(registry):
    reply = await registry.invoke(act("audio.set_volume", {"level": 40}), ctx())
    assert reply["ok"] is True
    assert reply["r"] == {"level": 40}
    assert reply["ms"] >= 0
    assert registry.calls == [("volume", 40)]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"level": -1},
        {"level": 101},
        {"level": "fort"},
        {"level": 10, "cmd": "rm -rf /"},  # champ inconnu : refusé
    ],
)
async def test_invalid_params_never_reach_the_handler(registry, params):
    reply = await registry.invoke(act("audio.set_volume", params), ctx())
    assert reply["ok"] is False
    assert reply["e"]["code"] == ErrorCode.BAD_PARAMS
    assert registry.calls == []


async def test_actions_without_params_reject_unexpected_ones(registry):
    reply = await registry.invoke(act("power.poweroff", {"force": True}), ctx())
    assert reply["e"]["code"] == ErrorCode.BAD_PARAMS


async def test_sensitive_action_needs_confirmation_then_runs_once(registry):
    first = await registry.invoke(act("power.poweroff"), ctx())
    assert first["e"]["code"] == ErrorCode.CONFIRM_REQUIRED
    assert first["confirm"]["label"] == "Éteindre le PC"
    assert first["confirm"]["ttl"] == 10.0
    assert registry.calls == []  # rien n'a été exécuté

    token = first["confirm"]["token"]
    done = await registry.invoke(act("power.poweroff", confirm=token, act_id="c2"), ctx())
    assert done["ok"] is True
    assert registry.calls == [("poweroff", None)]

    replay = await registry.invoke(act("power.poweroff", confirm=token, act_id="c3"), ctx())
    assert replay["e"]["code"] == ErrorCode.CONFIRM_INVALID  # à usage unique
    assert registry.calls == [("poweroff", None)]


async def test_confirmation_is_bound_to_the_connection(registry):
    token = (await registry.invoke(act("power.poweroff"), ctx(1)))["confirm"]["token"]
    stolen = await registry.invoke(act("power.poweroff", confirm=token), ctx(2))
    assert stolen["e"]["code"] == ErrorCode.CONFIRM_INVALID
    assert registry.calls == []


async def test_confirmation_is_bound_to_the_action():
    reg = ActionRegistry()

    async def noop(_ctx, _params):
        return None

    reg.register("power.reboot", noop, sensitive=True, label="Redémarrer")
    reg.register("power.poweroff", noop, sensitive=True, label="Éteindre")
    token = (await reg.invoke(act("power.reboot"), ctx()))["confirm"]["token"]
    reply = await reg.invoke(act("power.poweroff", confirm=token), ctx())
    assert reply["e"]["code"] == ErrorCode.CONFIRM_INVALID


async def test_confirmation_is_bound_to_the_parameters():
    reg = ActionRegistry()

    async def kill(_ctx, params):
        return {"pid": params.pid}

    class KillParams(BaseModel):
        model_config = ConfigDict(extra="forbid")
        pid: int

    reg.register("process.kill", kill, params=KillParams, sensitive=True, label="Arrêter")
    token = (await reg.invoke(act("process.kill", {"pid": 1234}), ctx()))["confirm"]["token"]
    other = await reg.invoke(act("process.kill", {"pid": 1}, confirm=token), ctx())
    assert other["e"]["code"] == ErrorCode.CONFIRM_INVALID


async def test_confirmation_expires():
    clock = FakeClock()
    reg = ActionRegistry(confirm_ttl=10, clock=clock)

    async def noop(_ctx, _params):
        return None

    reg.register("power.poweroff", noop, sensitive=True, label="Éteindre")
    token = (await reg.invoke(act("power.poweroff"), ctx()))["confirm"]["token"]
    clock.now += 10.5
    late = await reg.invoke(act("power.poweroff", confirm=token), ctx())
    assert late["e"]["code"] == ErrorCode.CONFIRM_INVALID


async def test_made_up_confirmation_token_is_refused(registry):
    reply = await registry.invoke(act("power.poweroff", confirm="devine-moi"), ctx())
    assert reply["e"]["code"] == ErrorCode.CONFIRM_INVALID
    assert registry.calls == []


async def test_action_errors_are_reported_with_their_message():
    reg = ActionRegistry()

    async def refuse(_ctx, _params):
        raise ActionError("no_player", "Aucun lecteur multimédia n'est ouvert")

    reg.register("media.play_pause", refuse)
    reply = await reg.invoke(act("media.play_pause"), ctx())
    assert reply["e"] == {"code": "no_player", "msg": "Aucun lecteur multimédia n'est ouvert"}


async def test_unexpected_exceptions_do_not_leak_details(registry, caplog):
    async def boom(_ctx, _params):
        raise RuntimeError("secret interne /home/mars/…")

    registry.register("diag.boom", boom)
    reply = await registry.invoke(act("diag.boom"), ctx())
    assert reply["e"]["code"] == ErrorCode.FAILED
    assert "secret" not in str(reply)
    assert "secret interne" in caplog.text  # mais c'est bien journalisé côté agent


async def test_slow_actions_time_out():
    reg = ActionRegistry()

    async def slow(_ctx, _params):
        await asyncio.sleep(5)

    reg.register("diag.slow", slow, timeout=0.05)
    reply = await reg.invoke(act("diag.slow"), ctx())
    assert reply["e"]["code"] == ErrorCode.TIMEOUT


def test_registration_rules():
    reg = ActionRegistry()

    async def noop(_ctx, _params):
        return None

    with pytest.raises(ValueError, match="invalide"):
        reg.register("pasdepoint", noop)
    with pytest.raises(ValueError, match="invalide"):
        reg.register("Power.Off", noop)
    with pytest.raises(ValueError, match="libellé"):
        reg.register("power.poweroff", noop, sensitive=True)
    reg.register("power.lock", noop)
    with pytest.raises(ValueError, match="déjà"):
        reg.register("power.lock", noop)
    assert reg.names == ["power.lock"]
    assert not reg.is_sensitive("power.lock")


def test_confirm_store_caps_pending_tokens():
    store = ConfirmStore(max_pending=3)
    tokens = [store.issue(1, "power.poweroff", {}) for _ in range(5)]
    assert not store.redeem(1, "power.poweroff", {}, tokens[0])  # les plus anciens sont oubliés
    assert store.redeem(1, "power.poweroff", {}, tokens[-1])


# --- libellé de confirmation dynamique ------------------------------------------------------------


class NamedKill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pid: int = Field(ge=2)


async def kill_handler(_ctx, params: NamedKill):
    return {"pid": params.pid}


async def test_the_confirmation_label_can_describe_the_exact_target():
    reg = ActionRegistry()
    reg.register(
        "system.kill",
        kill_handler,
        params=NamedKill,
        sensitive=True,
        label=lambda params: f"Terminer le processus {params.pid}",
    )
    first = await reg.invoke(act("system.kill", {"pid": 1234}), ctx())
    assert first["e"]["code"] == ErrorCode.CONFIRM_REQUIRED
    assert first["confirm"]["label"] == "Terminer le processus 1234"
    other = await reg.invoke(act("system.kill", {"pid": 99}), ctx())
    assert other["confirm"]["label"] == "Terminer le processus 99"


async def test_a_label_function_receives_validated_params_only():
    seen = []
    reg = ActionRegistry()
    reg.register(
        "system.kill",
        kill_handler,
        params=NamedKill,
        sensitive=True,
        label=lambda params: seen.append(params) or "ok",
    )
    await reg.invoke(act("system.kill", {"pid": 0}), ctx())  # refusé avant d'arriver au libellé
    assert seen == []
    await reg.invoke(act("system.kill", {"pid": 7}), ctx())
    assert [type(p) for p in seen] == [NamedKill]


async def test_a_broken_label_function_never_blocks_the_confirmation():
    def broken(_params):
        raise RuntimeError("boum")

    reg = ActionRegistry()
    reg.register("system.kill", kill_handler, params=NamedKill, sensitive=True, label=broken)
    first = await reg.invoke(act("system.kill", {"pid": 5}), ctx())
    assert first["e"]["code"] == ErrorCode.CONFIRM_REQUIRED
    assert first["confirm"]["label"] == "system.kill"  # jamais de fenêtre sans texte


async def test_a_dynamic_label_does_not_weaken_the_binding_to_the_parameters():
    reg = ActionRegistry()
    reg.register(
        "system.kill",
        kill_handler,
        params=NamedKill,
        sensitive=True,
        label=lambda params: f"Terminer {params.pid}",
    )
    token = (await reg.invoke(act("system.kill", {"pid": 10}), ctx()))["confirm"]["token"]
    swapped = await reg.invoke(act("system.kill", {"pid": 11}, confirm=token), ctx())
    assert swapped["e"]["code"] == ErrorCode.CONFIRM_INVALID


def test_a_sensitive_action_still_needs_some_label():
    reg = ActionRegistry()
    with pytest.raises(ValueError, match="libellé"):
        reg.register("system.kill", kill_handler, params=NamedKill, sensitive=True)
