"""`mydock-dock doctor` : la section du son (le Pi n'a pas d'enceintes, le son des vidéos, de la TV et des jeux sort sur le PC)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mydock_common.report import Report
from mydock_dock.config import DockConfig
from mydock_dock.doctor import check_sound

FIX = "sudo apt install pipewire-pulse wireplumber pulseaudio-utils"


def config_for(tmp_path: Path, **media) -> DockConfig:
    return DockConfig.from_dict({"media": media}, tmp_path / "d.toml")


def render(
    config: DockConfig, *, tools=("pactl", "parec", "pipewire"), log: Path | None = None
) -> str:
    report = Report()
    check_sound(
        report,
        config,
        which=lambda name: f"/usr/bin/{name}" if name in tools else None,
        journal=lambda: log,
    )
    return report.render()


def journal(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "player.log"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_with_everything_in_place_the_sound_goes_to_the_pc(tmp_path):
    out = render(config_for(tmp_path))
    assert (
        "✔ Le son des vidéos, de la TV et des jeux sort sur le PC (le son des vidéos est avancé de 150 ms)"
        in out
    )
    assert "✔ pactl présent" in out
    assert "✔ parec présent" in out
    assert "✔ Un serveur de son est installé" in out
    assert "⚠" not in out


def test_the_advance_is_the_one_in_the_settings(tmp_path):
    assert "avancé de 300 ms" in render(config_for(tmp_path, audio_advance_ms=300))


def test_with_the_sound_on_the_pi_nothing_else_is_checked(tmp_path):
    out = render(config_for(tmp_path, pc_audio=False), tools=())
    assert "Le son reste sur le Pi" in out
    assert "pactl" not in out
    assert "⚠" not in out


@pytest.mark.parametrize("missing", ["pactl", "parec"])
def test_a_missing_tool_says_how_to_install_it(tmp_path, missing):
    tools = tuple(t for t in ("pactl", "parec", "pipewire") if t != missing)
    out = render(config_for(tmp_path), tools=tools)
    assert f"⚠ {missing} absent : le son ne pourra pas sortir sur le PC" in out
    assert FIX in out


def test_a_pi_without_a_sound_server_says_so(tmp_path):
    out = render(config_for(tmp_path), tools=("pactl", "parec"))
    assert "⚠ Aucun serveur de son (PipeWire) sur le Pi" in out
    assert FIX in out


def test_pulseaudio_counts_as_a_sound_server_too(tmp_path):
    assert "✔ Un serveur de son est installé" in render(
        config_for(tmp_path), tools=("pactl", "parec", "pulseaudio")
    )


def test_before_any_playback_the_doctor_says_the_helper_has_not_spoken(tmp_path):
    assert "L'assistant de l'écran n'a encore rien dit du son" in render(config_for(tmp_path))


def test_the_last_word_of_the_helper_is_shown(tmp_path):
    log = journal(
        tmp_path,
        "10:00:01 son : serveur de son du Pi : PulseAudio (on PipeWire 1.0.5) — réglage : sur le PC",
        "10:05:00 lecture 3 : demandée par le dock",
        "10:05:01 son : capture de mydock_pc démarrée (avance du son des vidéos : 150 ms)",
    )
    out = render(config_for(tmp_path), log=log)
    assert "✔ Son, d'après l'assistant de l'écran : capture de mydock_pc démarrée" in out


@pytest.mark.parametrize(
    "line",
    [
        "son : impossible — Pas de serveur de son (PipeWire) dans la session de l'écran (Connection refused) : sudo apt install pipewire-pulse",
        "son : serveur de son du Pi : aucun serveur de son joignable — Pas de serveur de son (PipeWire)",
        "son : la capture s'est arrêtée toute seule",
    ],
)
def test_a_helper_that_could_not_capture_is_a_warning_with_its_reason(tmp_path, line):
    out = render(config_for(tmp_path), log=journal(tmp_path, f"10:00:00 {line}"))
    assert "⚠ Son, d'après l'assistant de l'écran :" in out
    assert line.split("son : ", 1)[1][:25] in out
