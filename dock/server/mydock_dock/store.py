"""État persistant du dock (hors secrets) : préférences d'interface et ce qu'on sait du PC.

Fichier state.json dans le dossier d'état. S'il est illisible il est mis de côté (state.json.bad)
et on repart d'un état neuf ; s'il n'est pas modifiable, le dock continue en mémoire.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mydock_dock.markets.model import (
    DEFAULT_REFRESH,
    parse_currency,
    parse_refresh,
    parse_watchlist,
)
from mydock_dock.validation import ValidationFailure

log = logging.getLogger(__name__)

PAGE_IDS = ("music", "sound", "apps", "steam", "office", "markets", "system", "power", "settings")
DEFAULT_PAGE = "system"
ALWAYS_VISIBLE = "settings"  # on ne peut pas cacher Réglages : sinon plus moyen de le réparer
MAX_PINNED_APPS = 24
#: Délais d'inactivité (secondes) au bout desquels l'écran du dock s'éteint ; 0 = jamais.
SCREEN_SLEEP_CHOICES = (0, 60, 300, 600, 1800)
APP_ID_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,200}\.desktop$")  # identifiant d'un fichier .desktop


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "ui": {
            "last_page": DEFAULT_PAGE,
            "order": [],
            "hidden": [],
            "pinned_apps": [],
            "screen_sleep": 0,
        },
        "pc": {"hostname": None, "interfaces": [], "wol": None, "seen_at": None},
        "markets": {
            "watchlist": [{"kind": "crypto", "symbol": "BTC"}, {"kind": "crypto", "symbol": "ETH"}],
            "currency": "EUR",
            "refresh": DEFAULT_REFRESH,
        },
    }


class StateStore:
    def __init__(self, directory: Path):
        self._path = Path(directory) / "state.json"
        self._data = _default_state()
        self._load()

    # --- fichier -------------------------------------------------------------------------

    def _load(self) -> None:
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("racine inattendue")
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("state.json illisible (%s) : repart d'un état neuf", exc)
            with contextlib.suppress(OSError):
                os.replace(self._path, self._path.with_suffix(".json.bad"))
            return
        for section, defaults in _default_state().items():
            if isinstance(defaults, dict) and isinstance(loaded.get(section), dict):
                self._data[section].update(
                    {k: v for k, v in loaded[section].items() if k in defaults}
                )
        ui = self._data["ui"]
        if not _is_screen_sleep(ui.get("screen_sleep")):
            ui["screen_sleep"] = 0  # une valeur abîmée ne doit pas atteindre l'écran

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".state.", dir=self._path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self._data, handle, ensure_ascii=False, indent=1)
                os.replace(tmp, self._path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        except OSError as exc:
            log.warning("Impossible d'écrire %s (%s) : l'état reste en mémoire", self._path, exc)

    # --- préférences d'interface ------------------------------------------------------------

    @property
    def ui(self) -> dict[str, Any]:
        return copy.deepcopy(self._data["ui"])

    def update_ui(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        ui = self._data["ui"]
        new = copy.deepcopy(ui)
        if "last_page" in patch:
            if patch["last_page"] not in PAGE_IDS:
                raise ValidationFailure("Page inconnue.")
            new["last_page"] = patch["last_page"]
        if "order" in patch:
            new["order"] = _page_list(patch["order"], "L'ordre des pages")
        if "hidden" in patch:
            hidden = _page_list(patch["hidden"], "La liste des pages cachées")
            if ALWAYS_VISIBLE in hidden:
                raise ValidationFailure("La page Réglages ne peut pas être cachée.")
            new["hidden"] = hidden
        if "pinned_apps" in patch:
            new["pinned_apps"] = _app_list(patch["pinned_apps"])
        if "screen_sleep" in patch:
            if not _is_screen_sleep(patch["screen_sleep"]):
                raise ValidationFailure("Durée de veille de l'écran invalide.")
            new["screen_sleep"] = patch["screen_sleep"]
        if new != ui:
            self._data["ui"] = new
            self._save()
        return copy.deepcopy(new)

    # --- la bourse : valeurs suivies et devise ----------------------------------------------

    @property
    def markets(self) -> dict[str, Any]:
        return copy.deepcopy(self._data["markets"])

    def update_markets(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        markets = self._data["markets"]
        new = copy.deepcopy(markets)
        if "watchlist" in patch:
            new["watchlist"] = [item.to_dict() for item in parse_watchlist(patch["watchlist"])]
        if "currency" in patch:
            new["currency"] = parse_currency(patch["currency"])
        if "refresh" in patch:
            new["refresh"] = parse_refresh(patch["refresh"])
        if new != markets:
            self._data["markets"] = new
            self._save()
        return copy.deepcopy(new)

    # --- ce qu'on sait du PC ----------------------------------------------------------------

    @property
    def pc(self) -> dict[str, Any]:
        return copy.deepcopy(self._data["pc"])

    def update_pc(self, **fields: Any) -> bool:
        """Retourne vrai si quelque chose a changé (hors horodatage de dernière vue)."""
        pc = self._data["pc"]
        unknown = set(fields) - set(pc)
        if unknown:
            raise KeyError(f"Champs inconnus : {sorted(unknown)}")
        meaningful = {k: v for k, v in fields.items() if k != "seen_at"}
        changed = any(pc[k] != v for k, v in meaningful.items())
        pc.update(fields)
        if changed:
            self._save()
        return changed


def _is_screen_sleep(value: Any) -> bool:
    return type(value) is int and value in SCREEN_SLEEP_CHOICES


def _page_list(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not all(isinstance(v, str) and v in PAGE_IDS for v in value)
        or len(set(value)) != len(value)
    ):
        raise ValidationFailure(f"{label} est invalide.")
    return list(value)


def _app_list(value: Any) -> list[str]:
    """Les applications du lanceur, dans l'ordre choisi : des identifiants .desktop, sans doublon."""
    if (
        not isinstance(value, list)
        or len(value) > MAX_PINNED_APPS
        or not all(isinstance(v, str) and APP_ID_RE.fullmatch(v) for v in value)
        or len(set(value)) != len(value)
    ):
        raise ValidationFailure(
            f"La liste des applications est invalide (au plus {MAX_PINNED_APPS}, sans doublon)."
        )
    return list(value)
