"""Shared pytest fixtures.

Qt tests run headless via the ``offscreen`` platform plugin, so no display
server is required (works in CI).  Config-touching tests are isolated to a
temp directory so they never read or write the real user config.
"""

from __future__ import annotations

import os

# Must be set before any QApplication is created (pytest-qt reads it).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from app.models.game_save import GameSave, SaveFile, SaveType


# ----------------------------------------------------------------------
# Config isolation
# ----------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A fully isolated :class:`Config` singleton rooted under *tmp_path*."""
    from app import config as config_mod

    data_dir = tmp_path / "data"
    monkeypatch.setattr(config_mod, "_default_data_dir", lambda: data_dir)
    config_mod.Config.reset()
    c = config_mod.Config()
    c.set("backup_path", str(tmp_path / "backups"))
    c.set("machine_id", "machineA")
    yield c
    config_mod.Config.reset()


@dataclass
class FakeConfig:
    """Minimal duck-typed config for multi-machine sync tests.

    The real :class:`Config` is a singleton, so simulating two devices at
    once needs a lightweight stand-in exposing only what the backup/sync
    engines read.
    """

    backup_path: Path
    sync_folder: Path
    machine_id: str
    max_backups: int = 5


# ----------------------------------------------------------------------
# Factories
# ----------------------------------------------------------------------

@pytest.fixture
def make_game_save():
    """Create a :class:`GameSave` backed by real files under *root*."""

    def _make(
        root: Path,
        emulator: str = "PCSX2",
        game_id: str = "SLUS-12345",
        files: dict[str, bytes] | None = None,
        save_type: SaveType = SaveType.SAVESTATE,
        data_path: Path | None = None,
    ) -> GameSave:
        root.mkdir(parents=True, exist_ok=True)
        files = files or {"slot0.p2s": b"SAVE-DATA-0"}
        save_files: list[SaveFile] = []
        for name, content in files.items():
            p = root / name
            p.write_bytes(content)
            save_files.append(
                SaveFile(path=p, save_type=save_type, size=len(content),
                         modified=datetime.now())
            )
        return GameSave(
            emulator=emulator, game_name=game_id, game_id=game_id,
            platform="PS2", save_files=save_files, data_path=data_path,
        )

    return _make


@pytest.fixture
def machine_factory(tmp_path):
    """Build an independent (config, BackupManager, SyncManager) trio.

    Used to simulate several devices pushing/pulling through one shared
    sync folder.
    """
    from app.core.backup import BackupManager
    from app.core.sync import SyncManager

    def _make(name: str, sync_folder: Path):
        cfg = FakeConfig(
            backup_path=tmp_path / f"{name}_backups",
            sync_folder=Path(sync_folder),
            machine_id=name,
        )
        bm = BackupManager(cfg)
        sm = SyncManager(cfg, bm)
        return cfg, bm, sm

    return _make
