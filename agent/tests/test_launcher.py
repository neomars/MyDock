"""Lancer un programme graphique : scope systemd, programme introuvable, échec immédiat, programme qui dure."""

import os
import stat
from types import SimpleNamespace

import pytest

from mydock_agent import launcher
from mydock_agent.launcher import LaunchError, SystemLauncher, graphical_environment
from mydock_agent.testing import eventually


@pytest.fixture
def system(tmp_path):
    """Un PATH jetable avec de faux programmes qui notent comment on les a lancés."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"

    def make(name: str, body: str) -> str:
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def env(**extra: str) -> dict[str, str]:
        return {"PATH": str(bin_dir), "LOG": str(log), **extra}

    def calls() -> list[str]:
        return log.read_text().splitlines() if log.exists() else []

    return SimpleNamespace(make=make, env=env, calls=calls, dir=bin_dir, tmp=tmp_path)


def with_systemd_run(system) -> None:
    system.make(
        "systemd-run",
        'echo "systemd-run $*" >> "$LOG"\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n',
    )


async def test_a_program_is_started_in_its_own_systemd_scope_with_the_session_environment(system):
    with_systemd_run(system)
    system.make(
        "appli", 'echo "appli:$WAYLAND_DISPLAY" >> "$LOG"\nprintf "arg=%s\\n" "$@" >> "$LOG"\n'
    )
    await SystemLauncher(system.env(WAYLAND_DISPLAY="wayland-test"), settle=0.5).start(
        ["appli", "un", "deux mots"], label="Mon appli"
    )
    first, *rest = system.calls()
    assert first.startswith("systemd-run --user --scope --quiet --collect ")
    assert "--description=MyDock : Mon appli" in first
    assert first.endswith(f"-- {system.dir}/appli un deux mots")
    assert rest == [
        "appli:wayland-test",
        "arg=un",
        "arg=deux mots",
    ]  # les arguments restent intacts


async def test_without_systemd_run_the_program_is_started_directly(system):
    system.make("appli", 'echo "direct:$1" >> "$LOG"\n')
    await SystemLauncher(system.env(), settle=0.5).start(["appli", "x"], label="Appli")
    assert system.calls() == ["direct:x"]


async def test_a_program_that_does_not_exist_is_reported_by_name(system):
    with pytest.raises(LaunchError, match="« inconnu » est introuvable"):
        await SystemLauncher(system.env(), settle=0.2).start(["inconnu"], label="X")


async def test_a_launcher_that_fails_at_once_reports_its_exit_code(system):
    system.make("echoue", "exit 3\n")
    with pytest.raises(LaunchError, match=r"« Appli » a échoué \(code 3\)"):
        await SystemLauncher(system.env(), settle=1.0).start(["echoue"], label="Appli")


async def test_a_program_that_keeps_running_counts_as_started_and_is_detached(system):
    pidfile = system.tmp / "pid"
    system.make("longue", f"echo $$ > {pidfile}\nexec /bin/sleep 30\n")
    started = SystemLauncher(system.env(), settle=0.3)
    await started.start(["longue"], label="Appli")
    pid = int(pidfile.read_text())
    try:
        assert os.getsid(pid) == pid  # sa propre session : la fin de l'agent n'est pas la sienne
        assert os.kill(pid, 0) is None  # toujours vivant
    finally:
        os.kill(pid, 15)
    await eventually(
        lambda: not started._tasks, timeout=3
    )  # le processus est récolté, pas de zombie


async def test_a_program_that_cannot_be_executed_is_reported(system):
    path = system.dir / "pasexecutable"
    path.write_text("#!/inexistant/interpreteur\n")
    path.chmod(0o755)
    with pytest.raises(LaunchError):
        await SystemLauncher(system.env(), settle=0.5).start(["pasexecutable"], label="X")


def test_the_graphical_environment_is_completed_from_the_user_manager(monkeypatch):
    monkeypatch.setattr(
        launcher,
        "user_manager_environment",
        lambda: {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":9", "XDG_SESSION_TYPE": "wayland"},
    )
    env = graphical_environment({"PATH": "/usr/bin", "DISPLAY": ":0"})
    assert env["DISPLAY"] == ":0"  # ce que l'agent connaît déjà reste prioritaire
    assert env["WAYLAND_DISPLAY"] == "wayland-0"
    assert env["PATH"] == "/usr/bin"


def test_without_a_user_manager_the_environment_is_left_alone(monkeypatch):
    monkeypatch.setattr(launcher, "user_manager_environment", lambda: None)
    assert graphical_environment({"PATH": "/usr/bin"}) == {"PATH": "/usr/bin"}
