"""Démonstration de « apps », « steam » et « office » : un petit monde fictif sur le disque.

Au démarrage de l'agent de démonstration, un dossier temporaire reçoit de vrais fichiers : des
`.desktop` et leurs icônes, une bibliothèque Steam (`libraryfolders.vdf`, `appmanifest_*.acf`,
jaquettes), un historique LibreOffice (`registrymodifications.xcu`) et quelques documents. Ce sont
les vrais fournisseurs qui les lisent : l'interface ne sait pas qu'elle parle à une démonstration.
Rien n'est lancé pour de bon : le lanceur de démonstration se contente de noter la demande, et un
jeu « lancé » apparaît comme en cours pendant une vingtaine de secondes.
"""

from __future__ import annotations

import html
import os
import re
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from mydock_agent.collectors.desktop import AppsProvider
from mydock_agent.collectors.office import OfficeProvider
from mydock_agent.collectors.steam import SteamProcesses, SteamProvider

GAME_SECONDS = 25.0

# (nom du fichier, nom affiché, catégorie, icône, teinte, lettre)
APPS = (
    ("org.mozilla.firefox", "Firefox", "Navigateur Web", "firefox", 20, "F"),
    ("brave-browser", "Brave", "Navigateur Web", "brave-browser", 14, "B"),
    ("org.gnome.Nautilus", "Fichiers", "Gestionnaire de fichiers", "system-file-manager", 212, "F"),
    ("org.gnome.Terminal", "Terminal", "Émulateur de terminal", "utilities-terminal", 150, ">"),
    ("org.gnome.Calculator", "Calculatrice", "Calculatrice", "accessories-calculator", 280, "="),
    ("org.gnome.TextEditor", "Éditeur de texte", "Éditeur de texte", "text-editor", 190, "T"),
    (
        "libreoffice-writer",
        "LibreOffice Writer",
        "Traitement de texte",
        "libreoffice-writer",
        220,
        "W",
    ),
    ("libreoffice-calc", "LibreOffice Calc", "Tableur", "libreoffice-calc", 130, "C"),
    ("libreoffice-impress", "LibreOffice Impress", "Présentation", "libreoffice-impress", 8, "I"),
    ("spotify", "Spotify", "Lecteur de musique", "spotify-client", 142, "S"),
    ("steam", "Steam", "Jeux vidéo", "steam", 205, "S"),
    ("discord", "Discord", "Messagerie", "discord", 235, "D"),
    ("vlc", "Lecteur multimédia VLC", "Lecteur vidéo", "vlc", 30, "V"),
    ("gimp", "GIMP", "Éditeur d'images", "gimp", 60, "G"),
    ("org.gnome.Settings", "Paramètres", "Paramètres du système", "preferences-system", 250, "P"),
    (
        "org.gnome.SystemMonitor",
        "Moniteur système",
        "Moniteur système",
        "utilities-system-monitor",
        170,
        "M",
    ),
    ("org.gnome.Loupe", "Visionneuse d'images", "Visionneuse d'images", "image-viewer", 320, "I"),
    (
        "org.gnome.Evince",
        "Visionneuse de documents",
        "Visionneuse de documents",
        "evince",
        355,
        "D",
    ),
)

# (appid, titre, Go, jours depuis la dernière partie ou None)
GAMES = (
    (1145360, "Hades", 15, 1),
    (504230, "Celeste", 1, 9),
    (427520, "Factorio", 3, 3),
    (620, "Portal 2", 12, 41),
    (730, "Counter-Strike 2", 37, 5),
    (1245620, "ELDEN RING", 60, 2),
    (413150, "Stardew Valley", 1, 15),
    (367520, "Hollow Knight", 9, 70),
    (1086940, "Baldur's Gate 3", 150, 6),
    (1091500, "Cyberpunk 2077", 70, None),
    (526870, "Satisfactory", 20, 12),
)

# (dossier, nom du fichier, heures d'ancienneté)
DOCUMENTS = (
    ("Documents/Travail", "Rapport trimestriel.odt", 1),
    ("Documents/Finances", "Budget 2026.ods", 5),
    ("Documents/Travail", "Présentation clients.odp", 26),
    ("Documents/Administratif", "Courrier assurance.docx", 50),
    ("Documents/Travail", "Planning chantier.xlsx", 80),
    ("Documents/Réseau", "Schéma réseau.odg", 200),
)


def _icon_svg(letter: str, hue: int) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">'
        f'<rect width="128" height="128" rx="28" fill="hsl({hue} 55% 36%)"/>'
        f'<rect x="6" y="6" width="116" height="116" rx="24" fill="none" stroke="hsl({hue} 70% 62%)" stroke-width="3"/>'
        '<text x="64" y="88" font-family="sans-serif" font-size="68" font-weight="700" '
        f'text-anchor="middle" fill="hsl({hue} 90% 92%)">{html.escape(letter)}</text></svg>'
    )


def _cover_svg(title: str, hue: int) -> str:
    words = title.split()
    lines: list[str] = []
    for word in words:
        if lines and len(lines[-1]) + len(word) < 11:
            lines[-1] += " " + word
        else:
            lines.append(word)
    text = "".join(
        f'<text x="150" y="{190 + 36 * i}" font-family="sans-serif" font-size="30" font-weight="700" '
        f'text-anchor="middle" fill="rgba(255,255,255,.92)">{html.escape(line)}</text>'
        for i, line in enumerate(lines[:4])
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 450">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="hsl({hue} 65% 46%)"/>'
        f'<stop offset="1" stop-color="hsl({(hue + 60) % 360} 70% 18%)"/></linearGradient></defs>'
        f'<rect width="300" height="450" fill="url(#g)"/>{text}</svg>'
    )


class DemoWorld:
    """Le dossier temporaire de la démonstration, et les fournisseurs qui le lisent."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mydock-demo-")
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.share = self.root / "share"
        self.launched: list[tuple[tuple[str, ...], str]] = []
        self._game: tuple[int, float] | None = None
        self._write_apps()
        self._write_steam()
        self._write_office()

    # --- fichiers ---

    def _write_apps(self) -> None:
        applications = self.share / "applications"
        icons = self.share / "icons" / "hicolor" / "scalable" / "apps"
        applications.mkdir(parents=True)
        icons.mkdir(parents=True)
        for file_id, name, generic, icon, hue, letter in APPS:
            (applications / f"{file_id}.desktop").write_text(
                "[Desktop Entry]\nType=Application\n"
                f"Name={name}\nName[fr]={name}\nGenericName={generic}\n"
                f"Exec={file_id} %U\nIcon={icon}\nCategories=Utility;\n",
                encoding="utf-8",
            )
            (icons / f"{icon}.svg").write_text(_icon_svg(letter, hue), encoding="utf-8")
        # Ce que le bureau cache : le lanceur doit les écarter aussi.
        (applications / "gnome-initial-setup.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Configuration initiale\nExec=true\nNoDisplay=true\n",
            encoding="utf-8",
        )
        (applications / "kde-only.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Réservé à KDE\nExec=true\nOnlyShowIn=KDE;\n",
            encoding="utf-8",
        )

    def _write_steam(self) -> None:
        steam = self.home / ".local" / "share" / "Steam"
        second = self.root / "jeux" / "SteamLibrary"
        for folder in (
            steam / "steamapps",
            second / "steamapps",
            steam / "appcache" / "librarycache",
        ):
            folder.mkdir(parents=True)
        (steam / "steamapps" / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n'
            f'\t"0"\n\t{{\n\t\t"path"\t\t"{steam}"\n\t\t"label"\t\t""\n\t}}\n'
            f'\t"1"\n\t{{\n\t\t"path"\t\t"{second}"\n\t\t"label"\t\t"Jeux"\n\t}}\n}}\n',
            encoding="utf-8",
        )
        now = time.time()
        for index, (appid, title, gigabytes, days) in enumerate(GAMES):
            library = steam if index % 3 else second
            played = f'\t"LastPlayed"\t\t"{int(now - days * 86400)}"\n' if days is not None else ""
            (library / "steamapps" / f"appmanifest_{appid}.acf").write_text(
                '"AppState"\n{\n'
                f'\t"appid"\t\t"{appid}"\n\t"name"\t\t"{title}"\n\t"StateFlags"\t\t"4"\n'
                f'\t"installdir"\t\t"{title}"\n\t"SizeOnDisk"\t\t"{gigabytes * 1024**3}"\n'
                f'\t"LastUpdated"\t\t"{int(now - 90 * 86400)}"\n{played}}}\n',
                encoding="utf-8",
            )
            (steam / "appcache" / "librarycache" / f"{appid}_library_600x900.svg").write_text(
                _cover_svg(title, (index * 47 + 10) % 360), encoding="utf-8"
            )
        # Des « jeux » qui sont des outils, et un jeu en cours de téléchargement : à écarter.
        for appid, title, flags in (
            (1493710, "Proton Experimental", 4),
            (1628350, "Steam Linux Runtime 3.0 (sniper)", 4),
            (228980, "Steamworks Common Redistributables", 4),
            (990080, "Hogwarts Legacy", 1026),
        ):
            (steam / "steamapps" / f"appmanifest_{appid}.acf").write_text(
                f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{title}"\n\t"StateFlags"\t\t"{flags}"\n}}\n',
                encoding="utf-8",
            )

    def _write_office(self) -> None:
        profile = self.home / ".config" / "libreoffice" / "4" / "user"
        profile.mkdir(parents=True)
        now = time.time()
        items, order = [], []
        for index, (folder, name, hours) in enumerate(DOCUMENTS):
            path = self.home / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
            os.utime(path, (now - hours * 3600, now - hours * 3600))
            url = f"file://{path.as_posix()}"
            items.append(
                f'<node oor:name="{html.escape(url, quote=True)}" oor:op="replace">'
                '<prop oor:name="Filter" oor:op="fuse"><value>writer8</value></prop>'
                f'<prop oor:name="Title" oor:op="fuse"><value>{html.escape(name)}</value></prop></node>'
            )
            order.append(
                f'<node oor:name="{index}" oor:op="replace"><prop oor:name="HistoryItemRef" '
                f'oor:op="fuse"><value>{html.escape(url)}</value></prop></node>'
            )
        base = "/org.openoffice.Office.Histories/Histories/['PickList']"
        (profile / "registrymodifications.xcu").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
            'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
            f'<item oor:path="{base}/ItemList">{"".join(items)}</item>\n'
            f'<item oor:path="{base}/OrderList">{"".join(order)}</item>\n</oor:items>\n',
            encoding="utf-8",
        )

    # --- le lanceur et les processus de démonstration ---

    async def start(self, argv: Sequence[str], *, label: str) -> None:
        self.launched.append((tuple(argv), label))
        match = re.fullmatch(r"steam://rungameid/(\d+)", argv[-1])
        if match:
            self._game = (int(match.group(1)), time.monotonic() + GAME_SECONDS)

    def processes(self) -> SteamProcesses:
        if self._game and time.monotonic() >= self._game[1]:
            self._game = None
        return SteamProcesses(
            client=self._game is not None, running=self._game[0] if self._game else None
        )

    # --- les fournisseurs ---

    def apps(self) -> AppsProvider:
        env = {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME", "LANG": "fr_FR.UTF-8", "PATH": os.defpath}
        return AppsProvider(
            roots=[self.share], launcher=self, env=env, home=self.home, theme=lambda: "hicolor"
        )

    def steam(self) -> SteamProvider:
        return SteamProvider(
            home=self.home,
            scanner=self.processes,
            launcher=self,
            which=lambda name: f"/usr/bin/{name}" if name == "steam" else None,
        )

    def office(self) -> OfficeProvider:
        return OfficeProvider(
            home=self.home,
            launcher=self,
            which=lambda name: f"/usr/bin/{name}" if name == "libreoffice" else None,
            flatpak_present=lambda: False,
        )
