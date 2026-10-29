import pytest

from mydock_agent.session import detect_session


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
