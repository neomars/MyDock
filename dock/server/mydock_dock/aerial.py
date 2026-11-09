"""Vues aériennes : le dock télécharge lui-même quelques films « Aerial » de l'Apple TV et les prépare pour l'écran.

Rien n'est livré dans le paquet (ces films appartiennent à Apple) : une tâche de fond, lente et discrète, prend dix films
de la liste du projet xscreensaver-aerial (cinq de jour, cinq de nuit) à l'adresse publique qu'Apple utilise pour
l'Apple TV, les convertit en MP4 H.264 1024×600 sans le son (ce que le Chromium du Pi lit sans réglage) et les range dans
le dossier de l'économiseur d'écran. Seul le https est suivi, la carte garde toujours de la place libre, et un échec
(réseau, disque, ffmpeg absent) se dit dans les Réglages puis se retente plus tard.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mydock_dock import screensaver

log = logging.getLogger("mydock.aerial")

BASE_URL = "https://sylvan.apple.com/Aerials/2x/Videos"
#: (nom du fichier chez Apple, légende). Liste reprise de xscreensaver-aerial (atv4-2k.sh) : cinq films de jour, cinq de nuit.
CATALOG: tuple[tuple[str, str], ...] = (
    ("AK_A004_C012_SDR_20191217_SDR_2K_HEVC.mov", "Vue aérienne jour 1"),
    ("BO_A012_C031_SDR_20190726_SDR_2K_HEVC.mov", "Vue aérienne jour 2"),
    ("BO_A014_C008_SDR_20190719_SDR_2K_HEVC.mov", "Vue aérienne jour 3"),
    ("BO_A014_C023_SDR_20190717_F240F3709_SDR_2K_HEVC.mov", "Vue aérienne jour 4"),
    ("BO_A018_C029_SDR_20190812_SDR_2K_HEVC.mov", "Vue aérienne jour 5"),
    ("DB_D011_C010_2K_SDR_HEVC.mov", "Vue aérienne nuit 1"),
    ("HK_B005_C011_2K_SDR_HEVC.mov", "Vue aérienne nuit 2"),
    ("LA_A009_C009_2K_SDR_HEVC.mov", "Vue aérienne nuit 3"),
    ("LA_A011_C003_2K_SDR_HEVC.mov", "Vue aérienne nuit 4"),
    ("comp_A015_C018_0128ZS_v03_SDR_PS_FINAL_20180709__SDR_2K_HEVC.mov", "Vue aérienne nuit 5"),
)
WORK = ".work"
START_DELAY = 60.0  # on laisse le Pi finir de démarrer
RETRY_DELAY = 1800.0
MAX_SOURCE = 1024**3  # un film 2K fait quelques centaines de Mo
CHUNK = 1024 * 256

FFMPEG_ARGS = (
    "-vf", "scale=1024:600:force_original_aspect_ratio=increase,crop=1024:600",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
    "-an", "-movflags", "+faststart",
)  # fmt: skip


def target_name(title: str) -> str:
    return f"{title}.mp4"


class AerialError(Exception):
    """Un film qui n'a pas pu être pris ; le message est affichable."""


class AerialFetcher:
    def __init__(
        self,
        directory: Path,
        enabled: Callable[[], bool],
        *,
        which: Callable[[str], str | None] = shutil.which,
        start_delay: float = START_DELAY,
        retry_delay: float = RETRY_DELAY,
    ) -> None:
        self.directory = directory
        self._enabled = enabled
        self._which = which
        self._start_delay = start_delay
        self._retry_delay = retry_delay
        self.current: str | None = None
        self.error: str | None = None

    def status(self) -> dict[str, Any]:
        have = {v.name for v in screensaver.videos(self.directory)}
        done = sum(target_name(title) in have for _, title in CATALOG)
        return {
            "total": len(CATALOG),
            "done": done,
            "active": self.current is not None,
            "current": self.current,
            "error": self.error,
            "enabled": self._enabled(),
        }

    def missing(self) -> list[tuple[str, str]]:
        have = {v.name for v in screensaver.videos(self.directory)}
        return [(f, t) for f, t in CATALOG if target_name(t) not in have]

    async def run(self) -> None:
        await asyncio.sleep(self._start_delay)
        while True:
            try:
                if self._enabled() and self.missing():
                    await self.fetch_missing()
                    self.error = None
                else:
                    self.current = None
                    await asyncio.sleep(60)
                    continue
                if not self.missing():
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise
            except AerialError as exc:
                self.current = None
                self.error = str(exc)
                log.warning("Vues aériennes : %s", exc)
                await asyncio.sleep(self._retry_delay)
            except Exception:
                self.current = None
                self.error = "Erreur inattendue (voir le journal du dock)."
                log.exception("Vues aériennes : erreur inattendue")
                await asyncio.sleep(self._retry_delay)

    async def fetch_missing(self) -> None:
        if not self._which("ffmpeg"):
            raise AerialError("ffmpeg n'est pas installé sur le Pi (sudo apt install ffmpeg).")
        for filename, title in self.missing():
            if not self._enabled():
                break
            self.current = title
            try:
                await asyncio.to_thread(self._download, filename, title)
                await self._convert(filename, title)
            finally:
                self.current = None
                with contextlib.suppress(OSError):
                    (self.directory / WORK / filename).unlink()

    # --- un film -------------------------------------------------------------------------

    def _download(self, filename: str, title: str) -> None:
        work = self.directory / WORK
        try:
            work.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AerialError(
                f"Impossible d'écrire dans {self.directory} ({exc.strerror})."
            ) from None
        if shutil.disk_usage(work).free < screensaver.FREE_MARGIN + 2 * 400 * 1024**2:
            raise AerialError("Pas assez de place libre sur la carte du Pi.")
        request = urllib.request.Request(
            f"{BASE_URL}/{filename}", headers={"User-Agent": "mydock-dock"}
        )
        opener = urllib.request.build_opener(screensaver._HttpsOnly)
        written = 0
        try:
            with opener.open(request, timeout=screensaver.TIMEOUT) as response:
                kind = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if kind and not (kind.startswith("video/") or kind == "application/octet-stream"):
                    raise AerialError(f"Apple ne renvoie pas une vidéo pour {title} ({kind}).")
                with (work / filename).open("wb") as out:
                    while chunk := response.read(CHUNK):
                        written += len(chunk)
                        if written > MAX_SOURCE:
                            raise AerialError(f"{title} est trop gros.")
                        out.write(chunk)
        except (OSError, ValueError, screensaver.SaverError) as exc:
            if isinstance(exc, screensaver.SaverError):
                raise AerialError(str(exc)) from None
            raise AerialError(
                f"Téléchargement impossible : {getattr(exc, 'reason', exc)}."
            ) from None
        if written == 0:
            raise AerialError(f"{title} : fichier vide.")

    async def _convert(self, filename: str, title: str) -> None:
        work = self.directory / WORK
        out = work / f"{Path(filename).stem}.mp4"
        command = ["nice", "-n", "15", "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                   "-i", str(work / filename), *FFMPEG_ARGS, str(out)]  # fmt: skip
        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
            _, err = await process.communicate()
        except OSError as exc:
            raise AerialError(f"ffmpeg ne démarre pas ({exc.strerror}).") from None
        if process.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            with contextlib.suppress(OSError):
                out.unlink()
            tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["?"]
            raise AerialError(f"Conversion de {title} impossible : {tail[0][:120]}")
        out.chmod(0o644)
        os.replace(out, self.directory / target_name(title))
        log.info("Vue aérienne prête : %s", title)
