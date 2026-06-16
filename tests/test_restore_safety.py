"""Transactional restore — snapshot + rollback on failure."""

from __future__ import annotations

import json

from app.core.backup import BackupManager
from app.core.restore import RestoreManager


def _corrupt_second_entry(meta_path):
    """Point the 2nd backup_path at a non-existent zip entry so its write fails."""
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    assert len(data["backup_paths"]) >= 2
    data["backup_paths"][1]["zip_path"] = "savestate/__does_not_exist__.p2s"
    meta_path.write_text(json.dumps(data), encoding="utf-8")


def test_rollback_restores_overwritten_files(cfg, make_game_save, tmp_path):
    save_root = tmp_path / "emu" / "sstates"
    gs = make_game_save(save_root, files={"a.p2s": b"BACKUP-A", "b.p2s": b"BACKUP-B"})

    record = BackupManager(cfg).create_backup([gs])

    # Local saves have since changed.
    (save_root / "a.p2s").write_bytes(b"CURRENT-A-LIVE")
    (save_root / "b.p2s").write_bytes(b"CURRENT-B-LIVE")

    # Make the second write blow up mid-restore.
    _corrupt_second_entry(record.backup_path.with_suffix(".json"))

    errors = RestoreManager().restore_backup(record, force=True)

    assert errors, "a failed restore must report errors"
    # Everything is back to the pre-restore (live) state — not half-written.
    assert (save_root / "a.p2s").read_bytes() == b"CURRENT-A-LIVE"
    assert (save_root / "b.p2s").read_bytes() == b"CURRENT-B-LIVE"


def test_rollback_removes_newly_created_files(cfg, make_game_save, tmp_path):
    save_root = tmp_path / "emu" / "sstates"
    gs = make_game_save(save_root, files={"a.p2s": b"BACKUP-A", "b.p2s": b"BACKUP-B"})
    record = BackupManager(cfg).create_backup([gs])

    # a.p2s no longer exists locally; b.p2s changed.
    (save_root / "a.p2s").unlink()
    (save_root / "b.p2s").write_bytes(b"CURRENT-B-LIVE")

    _corrupt_second_entry(record.backup_path.with_suffix(".json"))

    errors = RestoreManager().restore_backup(record, force=True)

    assert errors
    # The file the restore created before failing is removed again...
    assert not (save_root / "a.p2s").exists()
    # ...and the untouched one keeps its live content.
    assert (save_root / "b.p2s").read_bytes() == b"CURRENT-B-LIVE"


def test_snapshot_cleaned_up_on_success(cfg, make_game_save, tmp_path, monkeypatch):
    snap_dir = tmp_path / "snapshot"

    def fake_mkdtemp(prefix=""):
        snap_dir.mkdir(exist_ok=True)
        return str(snap_dir)

    monkeypatch.setattr("tempfile.mkdtemp", fake_mkdtemp)

    save_root = tmp_path / "emu" / "sstates"
    gs = make_game_save(save_root, files={"a.p2s": b"DATA"})
    record = BackupManager(cfg).create_backup([gs])
    (save_root / "a.p2s").write_bytes(b"CHANGED")

    errors = RestoreManager().restore_backup(record, force=True)

    assert errors == []
    assert (save_root / "a.p2s").read_bytes() == b"DATA"
    assert not snap_dir.exists(), "snapshot dir must be cleaned up after success"


def test_missing_backup_reports_error_without_snapshot(cfg, tmp_path):
    """A missing zip is reported up front, before any snapshot/write."""
    from app.models.backup_record import BackupRecord
    from app.models.game_save import GameSave
    from datetime import datetime

    bogus = BackupRecord(
        game_save=GameSave(emulator="PCSX2", game_name="x", game_id="x"),
        backup_time=datetime.now(),
        backup_path=tmp_path / "nope.zip",
    )
    errors = RestoreManager().restore_backup(bogus)
    assert errors and "not found" in errors[0].lower()
