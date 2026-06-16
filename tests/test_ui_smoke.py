"""Headless UI smoke tests — construct widgets without a display server.

These don't assert behaviour so much as guarantee the pages and the Stage-0
widgets/workers import and build (something byte-compilation can't verify).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.i18n import init as i18n_init


@pytest.fixture(autouse=True)
def _i18n(cfg):
    i18n_init(cfg.language)


def test_mainwindow_constructs(qtbot, cfg):
    from app.ui.main_window import MainWindow

    w = MainWindow(cfg)
    qtbot.addWidget(w)

    # Stage-0 public entry points exist.
    assert hasattr(w.scan_page, "start_scan")
    assert hasattr(w.sync_page, "start_sync")
    # All five pages are present.
    for page in ("scan_page", "backup_page", "restore_page",
                 "sync_page", "settings_page"):
        assert getattr(w, page) is not None


def test_stage0_workers_instantiate(qtbot, cfg):
    from app.ui.pages.sync_page import _SyncWorker, _ResolveWorker
    from app.ui.pages.restore_page import _RestoreWorker

    for worker_cls in (_SyncWorker, _ResolveWorker, _RestoreWorker):
        w = worker_cls()
        assert w is not None


def test_conflict_dialogs_build(qtbot, cfg):
    from PySide6.QtWidgets import QWidget
    from app.ui.components.conflict_dialog import ConflictDialog, BatchConflictDialog
    from app.core.conflict import ConflictInfo

    parent = QWidget()
    parent.resize(800, 600)
    qtbot.addWidget(parent)

    ci = ConflictInfo(
        game_id="SLUS-12345", emulator="PCSX2", relative_path="x",
        local_path=Path("a.zip"), remote_path=Path("b.zip"),
        local_mtime=datetime.now(), remote_mtime=datetime.now(),
        local_hash="h1", remote_hash="h2", remote_machine="boxA",
    )

    single = ConflictDialog(ci, parent)
    batch = BatchConflictDialog([ci, ci], parent)
    qtbot.addWidget(single)
    qtbot.addWidget(batch)

    # i18n keys resolve to real strings, not the raw dotted key.
    assert single.titleLabel.text() and "." not in single.titleLabel.text()
