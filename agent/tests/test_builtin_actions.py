from mydock_agent.builtin_actions import register_builtin
from mydock_common.actions import ActionContext, ActionRegistry
from mydock_common.protocol import Act


async def test_builtin_actions_are_harmless():
    reg = ActionRegistry()
    register_builtin(reg)
    assert reg.names == ["diag.ping"]
    reply = await reg.invoke(Act("c1", "diag.ping", {}), ActionContext(1, "127.0.0.1"))
    assert reply["ok"] is True
    assert reply["r"]["agent_ms"] > 1_700_000_000_000
    demo = ActionRegistry()
    register_builtin(demo, demo=True)
    assert demo.names == ["diag.confirm_demo", "diag.ping"]
    assert demo.is_sensitive("diag.confirm_demo")
