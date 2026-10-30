import subprocess
from types import SimpleNamespace

import pytest

from mydock_agent import session
from mydock_agent.session import detect_session, user_manager_environment


@pytest.mark.parametrize(
    ("env", "kind", "desktop"),
    [
        (
            {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"},
            "wayland",
            "GNOME",
        ),
        ({"XDG_SESSION_TYPE": "Wayland", "XDG_CURRENT_DESKTOP": "GNOME"}, "wayland", "GNOME"),
        ({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "KDE"}, "x11", "KDE"),
        ({"WAYLAND_DISPLAY": "wayland-0"}, "wayland", None),
        ({"DISPLAY": ":0"}, "x11", None),
        ({"XDG_SESSION_TYPE": "tty"}, "tty", None),
        ({"XDG_SESSION_TYPE": "n_importe_quoi", "DISPLAY": ":1"}, "x11", None),
        ({}, "unknown", None),
    ],
)
def test_detect_session(env, kind, desktop):
    session = detect_session(env, ask_logind=False)
    assert session.type == kind
    assert session.desktop == desktop


def test_session_exposes_display_variables():
    session = detect_session({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, ask_logind=False)
    assert session.wayland_display == "wayland-0"
    assert session.display == ":0"
    assert session.to_dict() == {"type": "wayland", "desktop": None}


def test_the_user_manager_environment_keeps_the_screen_the_session_type_and_the_language(
    monkeypatch,
):
    shown = "\n".join(
        [
            "PATH=/usr/bin:/bin",
            "WAYLAND_DISPLAY=wayland-0",
            "DISPLAY=:0",
            "XDG_SESSION_TYPE=wayland",
            "XDG_CURRENT_DESKTOP=ubuntu:GNOME",
            "LANG=fr_FR.UTF-8",
            "LANGUAGE=fr:en",
            "SECRET_TOKEN=ne-pas-copier",
            "LESS=-R=x",
        ]
    )
    monkeypatch.setattr(session.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        session.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=shown),
    )
    assert user_manager_environment() == {
        "WAYLAND_DISPLAY": "wayland-0",
        "DISPLAY": ":0",
        "XDG_SESSION_TYPE": "wayland",
        "XDG_CURRENT_DESKTOP": "ubuntu:GNOME",
        "LANG": "fr_FR.UTF-8",
        "LANGUAGE": "fr:en",
    }


def test_the_user_manager_environment_is_none_when_systemd_cannot_be_asked(monkeypatch):
    monkeypatch.setattr(session.shutil, "which", lambda name: None)
    assert user_manager_environment() is None

    monkeypatch.setattr(session.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        session.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=""),
    )
    assert user_manager_environment() is None

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("systemctl", 3)

    monkeypatch.setattr(session.subprocess, "run", timeout)
    assert user_manager_environment() is None
