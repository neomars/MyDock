"""Processus : sélection des plus gourmands et arrêt confirmé, avec de vrais processus."""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time

import psutil
import pytest

from mydock_agent.collectors import procs
from mydock_agent.collectors.procs import (
    KillParams,
    ProcessSource,
    ProcSample,
    ProcsProvider,
    is_killable,
    register_system_actions,
    select_top,
)
from mydock_agent.testing import eventually
from mydock_common.actions import ActionContext, ActionRegistry
from mydock_common.protocol import Act, ErrorCode

ME = os.getuid()


def sample(pid, name="appli", cpu=0.0, rss=1 << 20, uid=ME, started=1000.0):
    return ProcSample(pid, name, cpu, rss, "mars", uid, started)


class FakeSource:
    cores = 8

    def __init__(self, samples):
        self.samples = samples
        self.scans = 0

    def scan(self):
        self.scans += 1
        return list(self.samples)


# --- sélection -----------------------------------------------------------------------------------


def test_the_heaviest_processes_come_first_and_ties_go_to_memory():
    chosen = select_top(
        [
            sample(10, "b", cpu=5.0),
            sample(11, "a", cpu=5.0, rss=9 << 20),
            sample(12, "c", cpu=0.0, rss=500 << 20),
            sample(13, "d", cpu=40.0),
        ],
        3,
    )
    assert [s.pid for s in chosen] == [13, 11, 10]


def test_an_idle_machine_lists_the_biggest_memory_users():
    chosen = select_top([sample(1, rss=1), sample(2, rss=300), sample(3, rss=20)], 2)
    assert [s.pid for s in chosen] == [2, 3]


@pytest.mark.parametrize(
    ("kwargs", "killable"),
    [
        ({}, True),
        ({"uid": ME + 1}, False),  # un autre utilisateur (ou root)
        ({"pid": 1}, False),
        ({"name": "gnome-shell"}, False),
        ({"name": "gnome-session-b"}, False),  # nom tronqué à 15 caractères par le noyau
        ({"name": "pipewire"}, False),
        ({"name": "Xwayland"}, False),
        ({"name": "systemd"}, False),
        ({"name": "firefox"}, True),
        ({"name": "brave"}, True),
    ],
)
def test_which_processes_may_be_stopped(kwargs, killable):
    base = {"pid": 4242}
    assert is_killable(sample(**{**base, **kwargs}), uid=ME, protected=frozenset({7})) is killable


def test_the_agent_and_its_parents_are_never_killable():
    protected = procs.protected_pids()
    assert os.getpid() in protected
    assert os.getppid() in protected
    assert not is_killable(sample(os.getpid()), uid=ME, protected=protected)


# --- fournisseur ---------------------------------------------------------------------------------


def test_the_published_state_is_json_with_a_killable_flag_per_process():
    provider = ProcsProvider(FakeSource([]), uid=ME, protected=lambda: frozenset({7}))
    data = provider.build(
        [
            sample(100, "firefox", cpu=12.5, started=1234.56),
            sample(7, "mydock-agent", cpu=0.1),
            sample(5, "gnome-shell", cpu=3.0),
            sample(9, "cupsd", cpu=1.0, uid=0 if ME else 1),
        ]
    )
    json.dumps(data)
    assert data["count"] == 4
    assert data["cores"] == 8
    by_name = {p["name"]: p for p in data["procs"]}
    assert by_name["firefox"] == {
        "pid": 100,
        "name": "firefox",
        "cpu": 12.5,
        "mem": 1 << 20,
        "user": "mars",
        "started": 1234.56,
        "killable": True,
    }
    assert by_name["mydock-agent"]["killable"] is False
    assert by_name["gnome-shell"]["killable"] is False
    assert by_name["cupsd"]["killable"] is False
    assert provider.name_of(100) == "firefox"
    assert provider.name_of(31337) is None


def test_only_the_top_eight_are_published():
    provider = ProcsProvider(FakeSource([]), uid=ME)
    data = provider.build([sample(n, cpu=float(n)) for n in range(2, 40)])
    assert len(data["procs"]) == procs.TOP
    assert data["count"] == 38
    assert data["procs"][0]["pid"] == 39


async def test_the_provider_primes_the_cpu_counters_before_publishing():
    source = FakeSource([sample(100, "firefox", cpu=5.0)])
    provider = ProcsProvider(source, interval=0.01, uid=ME)
    published = []

    async def publish(data):
        published.append((source.scans, data))
        return True

    task = asyncio.create_task(provider.run(publish))
    try:
        await eventually(lambda: len(published) >= 3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert published[0][0] >= 2  # une lecture d'amorce, puis la première vraie mesure
    assert published[0][1]["procs"][0]["name"] == "firefox"


def test_the_real_scan_sees_this_very_process():
    source = ProcessSource()
    source.scan()
    found = {s.pid: s for s in source.scan()}
    me = found[os.getpid()]
    assert me.uid == ME
    assert me.rss > 0
    assert me.started == pytest.approx(psutil.Process().create_time(), abs=0.01)
    assert source.cores >= 1
    assert all(s.cpu >= 0 for s in found.values())
    json.dumps(ProcsProvider(source).build(list(found.values())))


def test_processes_that_vanish_between_the_listing_and_the_reading_are_skipped(monkeypatch):
    source = ProcessSource()
    real_pids = psutil.pids()
    monkeypatch.setattr(psutil, "pids", lambda: [*real_pids, 4_000_000])  # n'existe pas
    assert os.getpid() in {s.pid for s in source.scan()}


# --- arrêt d'un processus ------------------------------------------------------------------------


@pytest.fixture
def victims():
    started: list[subprocess.Popen] = []

    def spawn(*argv: str) -> subprocess.Popen:
        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        started.append(proc)
        time.sleep(0.1)  # le temps que le programme soit vraiment lancé
        return proc

    yield spawn
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


@pytest.fixture
def kill_registry():
    provider = ProcsProvider(FakeSource([]), uid=ME)
    registry = ActionRegistry()
    register_system_actions(registry, provider)
    return registry, provider


def kill_act(pid, started, *, force=False, confirm=None, act_id="k1"):
    return Act(act_id, "system.kill", {"pid": pid, "started": started, "force": force}, confirm)


async def confirmed(registry, pid, started, **kw):
    """Joue les deux temps de la confirmation, comme le fait le dock."""
    first = await registry.invoke(kill_act(pid, started, **kw), ActionContext(1, "192.168.1.60"))
    assert first["e"]["code"] == ErrorCode.CONFIRM_REQUIRED, first
    token = first["confirm"]["token"]
    reply = await registry.invoke(
        kill_act(pid, started, confirm=token, act_id="k2", **kw), ActionContext(1, "192.168.1.60")
    )
    return first, reply


def started_of(proc):
    return round(psutil.Process(proc.pid).create_time(), 2)


async def test_killing_a_process_needs_a_confirmation_that_names_it(kill_registry, victims):
    registry, provider = kill_registry
    victim = victims("sleep", "60")
    provider.build([sample(victim.pid, "sleep", cpu=1.0)])  # le dock l'a vu dans la liste
    first = await registry.invoke(
        kill_act(victim.pid, started_of(victim)), ActionContext(1, "192.168.1.60")
    )
    assert first["e"]["code"] == ErrorCode.CONFIRM_REQUIRED
    assert first["confirm"]["label"] == f"Terminer « sleep » (PID {victim.pid})"
    assert victim.poll() is None  # rien n'est arrivé sans confirmation


async def test_a_confirmed_kill_terminates_the_process(kill_registry, victims):
    registry, _ = kill_registry
    victim = victims("sleep", "60")
    _, reply = await confirmed(registry, victim.pid, started_of(victim))
    assert reply["ok"] is True, reply
    assert reply["r"]["gone"] is True
    assert reply["r"]["signal"] == "SIGTERM"
    # L'agent attend la fin du processus ; ici c'est notre enfant, il est donc déjà « récolté ».
    assert victim.wait(timeout=5) in (0, -signal.SIGTERM)


async def test_a_stubborn_process_needs_the_forced_variant(kill_registry, victims):
    registry, provider = kill_registry
    stubborn = victims(
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
    )
    await asyncio.sleep(0.5)  # le temps d'installer le gestionnaire de signal
    started = started_of(stubborn)
    _, polite = await confirmed(registry, stubborn.pid, started)
    assert polite["ok"] is True
    assert polite["r"]["gone"] is False  # il a ignoré SIGTERM
    assert stubborn.poll() is None
    provider.build([sample(stubborn.pid, "python", cpu=1.0)])
    first, forced = await confirmed(registry, stubborn.pid, started, force=True)
    assert first["confirm"]["label"].startswith("Forcer l'arrêt de « python »")
    assert forced["r"] == {
        "pid": stubborn.pid,
        "name": forced["r"]["name"],
        "signal": "SIGKILL",
        "gone": True,
    }
    assert stubborn.wait(timeout=5) in (0, -signal.SIGKILL)


async def test_a_reused_pid_is_not_killed(kill_registry, victims):
    registry, _ = kill_registry
    victim = victims("sleep", "60")
    _, reply = await confirmed(registry, victim.pid, started_of(victim) - 3600)  # autre heure
    assert reply["ok"] is False
    assert reply["e"]["code"] == "changed"
    assert victim.poll() is None


async def test_a_process_that_already_exited_is_reported(kill_registry, victims):
    registry, _ = kill_registry
    victim = victims("sleep", "60")
    started = started_of(victim)
    victim.kill()
    victim.wait()
    _, reply = await confirmed(registry, victim.pid, started)
    assert reply["e"]["code"] == "gone"


async def test_another_users_process_is_refused(kill_registry, victims, monkeypatch):
    registry, _ = kill_registry
    victim = victims("sleep", "60")
    monkeypatch.setattr(procs.os, "getuid", lambda: ME + 1000)
    _, reply = await confirmed(registry, victim.pid, started_of(victim))
    assert reply["e"]["code"] == "forbidden"
    assert "autre utilisateur" in reply["e"]["msg"]
    assert victim.poll() is None


async def test_the_agent_itself_and_its_parent_are_refused(kill_registry):
    registry, _ = kill_registry
    for pid in (os.getpid(), os.getppid()):
        if pid < 2:
            continue
        started = round(psutil.Process(pid).create_time(), 2)
        _, reply = await confirmed(registry, pid, started)
        assert reply["e"]["code"] == "forbidden", pid


async def test_session_processes_are_refused_by_name(kill_registry, victims, tmp_path):
    registry, _ = kill_registry
    fake_shell = tmp_path / "gnome-shell"
    shutil.copy(shutil.which("sleep"), fake_shell)
    victim = victims(str(fake_shell), "60")
    _, reply = await confirmed(registry, victim.pid, started_of(victim))
    assert reply["e"]["code"] == "forbidden"
    assert "gnome-shell" in reply["e"]["msg"]
    assert victim.poll() is None


@pytest.mark.parametrize(
    "params",
    [
        {"pid": 1, "started": 1.0},  # init n'est jamais une cible
        {"pid": 0, "started": 1.0},
        {"pid": -5, "started": 1.0},
        {"pid": "12", "started": 1.0},
        {"pid": 12},
        {"pid": 12, "started": 1.0, "signal": 9},  # champ inconnu
        {"pid": 12, "started": 1.0, "force": "oui"},
        {"pid": 5_000_000, "started": 1.0},
    ],
)
async def test_bad_parameters_never_reach_the_kill(kill_registry, params):
    registry, _ = kill_registry
    reply = await registry.invoke(Act("k", "system.kill", params, None), ActionContext(1, "x"))
    assert reply["e"]["code"] == ErrorCode.BAD_PARAMS


def test_kill_parameters_are_strict():
    assert KillParams(pid=12, started=1.5).force is False
    with pytest.raises(ValueError, match="pid"):
        KillParams(pid=1, started=1.0)
