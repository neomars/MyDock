"""Le dépôt est public : aucun secret, aucun fichier de configuration privé, rien d'énorme."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, text=True
    ).stdout
    return [ROOT / name for name in out.split("\0") if name and (ROOT / name).is_file()]


SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)(token|secret|api[_-]?key|password|passwd)\s*[:=]\s*["'][A-Za-z0-9_\-+/=.~]{32,}["']"""
)
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
IGNORED_NAMES = (
    "agent.toml",
    "dock.toml",
    ".env",
    ".env.local",
    "id_rsa",
    "server.pem",
    "secret.key",
)


def test_no_secret_literals_in_tracked_text_files():
    offenders = []
    for path in tracked_files():
        if path.suffix in {".png", ".woff2", ".ico"} or "package-lock" in path.name:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if "pas un secret" in line or "noqa: secret" in line:
                continue  # jeton de démonstration public, assumé
            if SECRET_ASSIGNMENT.search(line) or PRIVATE_KEY.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert offenders == []


@pytest.mark.parametrize("name", IGNORED_NAMES)
def test_private_files_are_ignored_by_git(name):
    result = subprocess.run(
        ["git", "check-ignore", "-q", f"some/dir/{name}"], cwd=ROOT, check=False
    )
    assert result.returncode == 0, f"{name} devrait être dans .gitignore"


def test_no_private_config_file_is_tracked():
    names = {p.name for p in tracked_files()}
    assert not names & set(IGNORED_NAMES)


def test_no_oversized_file_is_tracked():
    big = [str(p.relative_to(ROOT)) for p in tracked_files() if p.stat().st_size > 2_000_000]
    assert big == []


def test_the_built_interface_is_committed():
    """Le Pi n'a pas Node : l'interface compilée fait partie du dépôt."""
    names = {str(p.relative_to(ROOT)) for p in tracked_files()}
    assert "dock/web/dist/index.html" in names
