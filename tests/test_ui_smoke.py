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
    from app.ui.pages.backup_page import _AutoBackupWorker

    for worker_cls in (_SyncWorker, _ResolveWorker, _RestoreWorker, _AutoBackupWorker):
        w = worker_cls()
        assert w is not None


def test_auto_backup_wiring(qtbot, cfg):
    from app.ui.main_window import MainWindow

    w = MainWindow(cfg)
    qtbot.addWidget(w)
    assert hasattr(w, "start_auto_backup_cycle")
    assert hasattr(w.backup_page, "auto_backup")
    assert hasattr(w.scan_page, "is_scanning")
    # interval defaults to 0 → no timer created.
    assert w._auto_timer is None


def test_auto_backup_worker_end_to_end(qtbot, cfg, make_game_save, tmp_path):
    """The background worker actually drives auto_backup_all and creates a backup."""
    from app.ui.pages.backup_page import _AutoBackupWorker
    from app.core.backup import BackupManager

    bm = BackupManager(cfg)
    gs = make_game_save(tmp_path / "s", files={"a.bin": b"X"})

    worker = _AutoBackupWorker()
    worker.set_data(bm, [gs])
    with qtbot.waitSignal(worker.finished, timeout=5000) as blocker:
        worker.start()

    assert blocker.args[0].backed_up == 1
    assert bm.list_backups(gs.emulator, gs.game_id)


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


def test_cancel_buttons_present(qtbot, cfg):
    """Each long-running page exposes a (hidden) cancel button."""
    from app.ui.pages.scan_page import ScanPage
    from app.ui.pages.backup_page import BackupPage
    from app.ui.pages.sync_page import SyncPage

    for page_cls in (ScanPage, BackupPage, SyncPage):
        page = page_cls()
        qtbot.addWidget(page)
        assert hasattr(page, "_cancel_btn")
        assert page._cancel_btn.isHidden()


def test_backup_management_card_and_dialog(qtbot, cfg, tmp_path):
    from datetime import datetime
    from app.ui.pages.restore_page import _GameBackupCard, _LabelDialog
    from app.models.backup_record import BackupRecord
    from app.models.game_save import GameSave

    rec = BackupRecord(
        game_save=GameSave(emulator="PCSX2", game_name="Game", game_id="SLUS-1"),
        backup_time=datetime.now(),
        backup_path=tmp_path / "2020-01-01_00-00.zip",
        version=1,
    )
    card = _GameBackupCard("Game", "PCSX2", "SLUS-1", [rec])
    qtbot.addWidget(card)
    # Management signals are forwarded by the card.
    for sig in ("restore_requested", "pin_requested", "label_requested", "delete_requested"):
        assert hasattr(card, sig)

    from PySide6.QtWidgets import QWidget
    parent = QWidget()
    parent.resize(400, 200)
    qtbot.addWidget(parent)
    dlg = _LabelDialog("before boss", parent)
    qtbot.addWidget(dlg)
    assert dlg.label_text == "before boss"


def test_restore_select_dialog_builds(qtbot, cfg):
    from pathlib import Path
    from PySide6.QtWidgets import QWidget
    from app.ui.pages.restore_page import _RestoreSelectDialog
    from app.core.restore import RestoreItem

    items = [
        RestoreItem(index=0, save_type="savestate", destination=Path("a.p2s"),
                    is_dir=False, dest_exists=True, is_newer_locally=False),
        RestoreItem(index=1, save_type="memcard", destination=Path("b.ps2"),
                    is_dir=False, dest_exists=True, is_newer_locally=True),
    ]
    parent = QWidget()
    parent.resize(500, 400)
    qtbot.addWidget(parent)
    dlg = _RestoreSelectDialog(items, parent)
    qtbot.addWidget(dlg)

    # All items checked by default.
    assert dlg.selected_indices == {0, 1}
