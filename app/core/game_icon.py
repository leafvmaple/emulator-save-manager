"""Game icon / cover art provider.

Resolution order
────────────────
1. Local cache  (``{data_dir}/icons/{serial}.jpg``)
2. Emulator covers directory  (PCSX2: ``{data_path}/covers/{serial}.*``)
3. Remote download from ``xlenore/ps2-covers`` on GitHub
4. Fallback → ``None`` (caller should show a generic icon)

A background :class:`QThread` (:class:`IconDownloadWorker`) can be used to
batch-download missing icons without blocking the UI.
"""

from __future__ import annotations

import re
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QPainter, QPainterPath, QImage
from loguru import logger


# -----------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------

_COVER_URL = (
    "https://raw.githubusercontent.com/xlenore/ps2-covers"
    "/main/covers/default/{serial}.jpg"
)

_SERIAL_RE = re.compile(r"^[A-Z]{4}-\d{5}$")
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# Plugin folder root (app/plugins/)
_PLUGINS_DIR = Path(__file__).parent.parent / "plugins"

# In-memory cache for emulator plugin icon pixmaps
_emu_icon_cache: dict[tuple[str, int], QPixmap | None] = {}


def get_plugin_icon(plugin_name: str, size: int = 36) -> QPixmap | None:
    """Load the emulator icon from ``app/plugins/{name}/icon.png``.

    Returns a scaled ``QPixmap`` or *None* if no icon file exists.
    """
    key = (plugin_name, size)
    if key in _emu_icon_cache:
        return _emu_icon_cache[key]

    # Plugin folder names are lowercase (e.g. "pcsx2", "mesen")
    folder_name = plugin_name.lower()
    icon_path = _PLUGINS_DIR / folder_name / "icon.png"
    if not icon_path.is_file():
        _emu_icon_cache[key] = None
        return None

    pm = QPixmap(str(icon_path))
    if pm.isNull():
        _emu_icon_cache[key] = None
        return None

    pm = pm.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    _emu_icon_cache[key] = pm
    return pm


# -----------------------------------------------------------------------
# Provider
# -----------------------------------------------------------------------

class GameIconProvider:
    """Resolves game cover art from local files or remote sources."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._emulator_data_paths: dict[str, Path] = {}
        # In-memory pixmap cache: (emulator, game_id, size) → QPixmap
        self._pixmap_cache: dict[tuple[str, str, int], QPixmap] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def register_emulator(self, name: str, data_path: Path) -> None:
        """Register an emulator's data directory for cover look-up."""
        self._emulator_data_paths[name] = data_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_icon_path(self, emulator: str, game_id: str) -> Path | None:
        """Return an on-disk path for the requested cover, or *None*."""
        # 1. Local cache
        cached = self._find_in_cache(game_id)
        if cached:
            return cached

        # 2. Emulator covers directory (PCSX2 keeps covers/{serial}.*)
        emu_path = self._emulator_data_paths.get(emulator)
        if emu_path:
            covers_dir = emu_path / "covers"
            found = self._find_cover_file(covers_dir, game_id)
            if found:
                # Copy into our cache so subsequent look-ups are fast
                dest = self._cache_dir / f"{game_id}{found.suffix}"
                try:
                    shutil.copy2(found, dest)
                except OSError:
                    pass
                return found

        return None

    def get_pixmap(
        self, emulator: str, game_id: str, width: int = 42, max_height: int = 58
    ) -> QPixmap | None:
        """Return a rounded *QPixmap* scaled to fixed *width*, or *None*."""
        key = (emulator, game_id, width)
        if key in self._pixmap_cache:
            return self._pixmap_cache[key]

        path = self.get_icon_path(emulator, game_id)
        if path is None:
            return None

        pm = QPixmap(str(path))
        if pm.isNull():
            return None

        pm = _rounded_cover_pixmap(pm, width, max_height)
        self._pixmap_cache[key] = pm
        return pm

    def put_pixmap(
        self, emulator: str, game_id: str, width: int, pm: QPixmap
    ) -> None:
        """Manually store a pixmap in the in-memory cache."""
        self._pixmap_cache[(emulator, game_id, width)] = pm

    def download_cover(self, game_id: str) -> Path | None:
        """Try to download a cover image and cache it.  Returns path or None."""
        if not _SERIAL_RE.match(game_id):
            return None

        url = _COVER_URL.format(serial=game_id)
        dest = self._cache_dir / f"{game_id}.jpg"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "EmulatorSaveManager/0.1"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            if len(data) < 500:
                # Probably an error page, not an image
                return None
            dest.write_bytes(data)
            logger.debug("Downloaded cover for {} ({} bytes)", game_id, len(data))
            return dest
        except Exception:
            logger.debug("Cover not available for {}", game_id)
            return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _find_in_cache(self, game_id: str) -> Path | None:
        for ext in _IMAGE_EXTS:
            p = self._cache_dir / f"{game_id}{ext}"
            if p.is_file():
                return p
        return None

    @staticmethod
    def _find_cover_file(covers_dir: Path, game_id: str) -> Path | None:
        if not covers_dir.is_dir():
            return None
        for ext in _IMAGE_EXTS:
            p = covers_dir / f"{game_id}{ext}"
            if p.is_file():
                return p
        return None


# -----------------------------------------------------------------------
# Background downloader
# -----------------------------------------------------------------------

class IconDownloadWorker(QThread):
    """Downloads missing covers in a background thread.

    Emits *icon_ready(emulator, game_id, path_str)* for each successful
    download so the UI can update the corresponding card.
    """

    icon_ready = Signal(str, str, str)  # emulator, game_id, path
    all_done = Signal()

    def __init__(
        self,
        provider: GameIconProvider,
        requests: Sequence[tuple[str, str]],  # [(emulator, game_id), ...]
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self._provider = provider
        self._requests = list(requests)

    def run(self) -> None:
        for emulator, game_id in self._requests:
            # Skip if already cached
            if self._provider.get_icon_path(emulator, game_id):
                continue
            path = self._provider.download_cover(game_id)
            if path:
                self.icon_ready.emit(emulator, game_id, str(path))
        self.all_done.emit()


# -----------------------------------------------------------------------
# Pixmap helpers
# -----------------------------------------------------------------------

def _rounded_cover_pixmap(
    src: QPixmap, width: int, max_height: int = 80, radius: int = 6
) -> QPixmap:
    """Scale *src* to fixed *width* keeping aspect ratio, clamp to *max_height*."""
    if src.isNull():
        return src
    # Scale to target width, keep aspect ratio.
    scaled = src.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
    h = min(scaled.height(), max_height)
    # Crop vertically from top if taller than max_height
    cropped = scaled.copy(0, 0, scaled.width(), h)

    result = QPixmap(cropped.width(), cropped.height())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, cropped.width(), cropped.height(), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return result
