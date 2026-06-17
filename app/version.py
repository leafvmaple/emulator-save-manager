"""Application version — read from the ``VERSION`` file (the single source of
truth that the release workflow bumps), not hard-coded.

Works both from source (``VERSION`` at the repo root) and in a PyInstaller
build (bundled at ``sys._MEIPASS/VERSION`` via ``--add-data``).
"""

from __future__ import annotations

import sys
from pathlib import Path

_FALLBACK = "dev"


def _candidate_bases() -> list[Path]:
    bases: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bases.append(Path(sys._MEIPASS))
    bases.append(Path(__file__).resolve().parent.parent)  # repo root (parent of app/)
    return bases


def get_app_version() -> str:
    """Return the application version string, or ``"dev"`` if unavailable."""
    for base in _candidate_bases():
        version_file = base / "VERSION"
        if version_file.is_file():
            try:
                value = version_file.read_text(encoding="utf-8").strip()
                if value:
                    return value
            except OSError:
                continue
    return _FALLBACK
