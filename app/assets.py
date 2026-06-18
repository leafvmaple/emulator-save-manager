"""Locate bundled asset files (the app icon, etc.).

Works both from source (files under the repo) and from a PyInstaller build
(bundled under ``sys._MEIPASS`` via ``--add-data``), mirroring
:mod:`app.version`.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _bases() -> list[Path]:
    bases: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bases.append(Path(sys._MEIPASS))
    bases.append(Path(__file__).resolve().parent.parent)  # repo root (parent of app/)
    return bases


def asset_path(*parts: str) -> Path:
    """Return the first existing path for *parts*, or the source-tree fallback."""
    for base in _bases():
        p = base.joinpath(*parts)
        if p.exists():
            return p
    return _bases()[-1].joinpath(*parts)


def app_icon_path() -> Path:
    """Filesystem path to the application icon PNG."""
    return asset_path("app", "resources", "icon.png")
