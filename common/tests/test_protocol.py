import json

import pytest

from mydock_common import protocol as p


def test_roundtrip_is_compact_and_keeps_accents():
    text = p.dumps({"t": "snap", "topic": "host", "rev": 1, "data": {"os": "Système"}})
    assert " " not in text.replace("Système", "")
    assert "Système" in text  # pas d'échappement è
    assert p.loads(text)["data"]["os"] == "Système"


@pytest.mark.parametrize("raw", ["", "pas du json", "[]", "42", '"t"', '{"x": 1}', '{"t": 3}'])
def test_loads_rejects_non_typed_objects(raw):
    with pytest.raises(p.ProtocolError) as info:
        p.loads(raw)
    assert info.value.code == p.ErrorCode.BAD_MESSAGE


def test_loads_rejects_oversized_messages():
    raw = json.dumps({"t": "x", "pad": "a" * p.MAX_MESSAGE_BYTES})
    with pytest.raises(p.ProtocolError) as info:
        p.loads(raw)
    assert info.value.code == p.ErrorCode.TOO_LARGE


def test_loads_accepts_bytes():
    assert p.loads(b'{"t":"ping","n":1}') == {"t": "ping", "n": 1}


def test_parse_topics_dedups_and_keeps_order():
    assert p.parse_topics({"topics": ["music", "host", "music"]}) == ["music", "host"]


@pytest.mark.parametrize(
    "topics",
    [None, [], "music", [1], ["Music"], ["a b"], ["x" * 40], ["a"] * 17, ["../etc"]],
)
def test_parse_topics_rejects_garbage(topics):
    with pytest.raises(p.ProtocolError):
        p.parse_topics({"topics": topics})


def test_parse_act_nominal():
    act = p.parse_act({"t": "act", "id": "c1", "a": "media.play_pause", "p": {"x": 1}})
    assert act == p.Act("c1", "media.play_pause", {"x": 1}, None)


def test_parse_act_defaults_and_confirm():
    act = p.parse_act({"id": "c2", "a": "power.poweroff", "confirm": "abc"})
    assert act.params == {}
    assert act.confirm == "abc"


@pytest.mark.parametrize(
    "msg",
    [
        {"a": "x.y"},  # pas d'id
        {"id": "", "a": "x.y"},
        {"id": "a b", "a": "x.y"},
        {"id": "c1\n", "a": "x.y"},  # « $ » accepterait ce retour à la ligne final
        {"id": "c1", "a": "x.y\n"},
        {"id": "i" * 65, "a": "x.y"},
        {"id": "i", "a": "pas_de_point"},
        {"id": "i", "a": "Majuscule.Nope"},
        {"id": "i", "a": "x.y; rm -rf /"},
        {"id": "i", "a": "x.y", "p": [1, 2]},
        {"id": "i", "a": "x.y", "confirm": 5},
        {"id": "i", "a": "x.y", "confirm": "c" * 129},
    ],
)
def test_parse_act_rejects_invalid(msg):
    with pytest.raises(p.ProtocolError):
        p.parse_act(msg)


def test_ack_builders():
    assert p.ack_ok("c1", {"a": 1}, ms=1.2345) == {
        "t": "ack",
        "id": "c1",
        "ok": True,
        "r": {"a": 1},
        "ms": 1.23,
    }
    assert p.ack_ok("c1") == {"t": "ack", "id": "c1", "ok": True}
    bad = p.ack_error("c1", p.ErrorCode.CONFIRM_REQUIRED, "Confirmer", extra={"confirm": {"x": 1}})
    assert bad["ok"] is False
    assert bad["e"] == {"code": "confirm_required", "msg": "Confirmer"}
    assert bad["confirm"] == {"x": 1}


def test_hello_carries_protocol_version():
    assert p.hello({"version": "1"}) == {
        "t": "hello",
        "proto": p.PROTOCOL_VERSION,
        "agent": {"version": "1"},
    }


def test_process_list_is_a_topic_of_its_own():
    # « procs » n'est échantillonné que page Système ouverte, « system » est suivi en permanence.
    assert "procs" in p.AGENT_TOPICS
    assert p.AGENT_TOPICS.index("system") < p.AGENT_TOPICS.index("procs")
    assert not set(p.AGENT_TOPICS) & set(p.DOCK_TOPICS)
