import asyncio

import pytest

from mydock_common.outbox import Outbox


async def drain(outbox: Outbox, sent: list[str], **kwargs):
    async def send(text: str):
        sent.append(text)

    await outbox.run(send, **kwargs)


async def test_control_messages_keep_their_order():
    box, sent = Outbox(), []
    for i in range(5):
        box.push_control(f"c{i}")
    box.close()
    await drain(box, sent)
    assert sent == ["c0", "c1", "c2", "c3", "c4"]


async def test_only_latest_snapshot_per_topic_is_sent():
    box, sent = Outbox(), []
    box.push_snapshot("music", "m1")
    box.push_snapshot("system", "s1")
    box.push_snapshot("music", "m2")
    box.push_snapshot("music", "m3")
    box.close()
    await drain(box, sent)
    assert sorted(sent) == ["m3", "s1"]


async def test_control_goes_before_snapshots():
    box, sent = Outbox(), []
    box.push_snapshot("host", "snap")
    box.push_control("ack")
    box.close()
    await drain(box, sent)
    assert sent == ["ack", "snap"]


async def test_messages_pushed_while_sending_are_not_lost():
    box, sent = Outbox(), []
    gate = asyncio.Event()

    async def send(text: str):
        sent.append(text)
        if text == "first":
            box.push_control("second")
            gate.set()

    task = asyncio.create_task(box.run(send))
    box.push_control("first")
    await asyncio.wait_for(gate.wait(), 1)
    await asyncio.sleep(0.05)
    box.close()
    await asyncio.wait_for(task, 1)
    assert sent == ["first", "second"]


async def test_overflow_disconnects_slow_clients():
    box = Outbox(max_control=3)
    for i in range(10):
        box.push_control(str(i))
    assert box.overflowed

    async def send(_text: str):  # pragma: no cover - ne doit pas être appelé avant l'erreur
        pass

    with pytest.raises(ConnectionError):
        await asyncio.wait_for(box.run(send), 1)


async def test_slow_send_times_out():
    box = Outbox()
    box.push_control("x")

    async def stuck(_text: str):
        await asyncio.sleep(5)

    with pytest.raises(asyncio.TimeoutError):
        await box.run(stuck, send_timeout=0.05)


async def test_push_after_close_is_ignored():
    box, sent = Outbox(), []
    box.close()
    box.push_control("late")
    box.push_snapshot("t", "late")
    await drain(box, sent)
    assert sent == []
