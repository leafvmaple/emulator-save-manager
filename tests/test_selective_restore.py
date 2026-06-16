"""Selective restore — restore a chosen subset of a backup's saves."""

from __future__ import annotations

from app.core.backup import BackupManager
from app.core.restore import RestoreManager


def _backup_three(cfg, make_game_save, root):
    gs = make_game_save(
        root, files={"a.p2s": b"BACKUP-A", "b.p2s": b"BACKUP-B", "c.p2s": b"BACKUP-C"}
    )
    return BackupManager(cfg).create_backup([gs])


def test_list_backup_items(cfg, make_game_save, tmp_path):
    root = tmp_path / "emu" / "sstates"
    record = _backup_three(cfg, make_game_save, root)

    items = RestoreManager().list_backup_items(record)
    assert len(items) == 3
    assert sorted(it.name for it in items) == ["a.p2s", "b.p2s", "c.p2s"]
    assert all(it.save_type == "savestate" for it in items)
    assert all(it.dest_exists for it in items)
    # Indices are stable positions usable as selectors.
    assert sorted(it.index for it in items) == [0, 1, 2]


def test_restore_only_selected_items(cfg, make_game_save, tmp_path):
    root = tmp_path / "emu" / "sstates"
    record = _backup_three(cfg, make_game_save, root)

    # All three diverge locally.
    (root / "a.p2s").write_bytes(b"LIVE-A")
    (root / "b.p2s").write_bytes(b"LIVE-B")
    (root / "c.p2s").write_bytes(b"LIVE-C")

    rm = RestoreManager()
    idx_a = next(it.index for it in rm.list_backup_items(record) if it.name == "a.p2s")

    errors = rm.restore_backup(record, force=True, indices={idx_a})
    assert errors == []
    assert (root / "a.p2s").read_bytes() == b"BACKUP-A"   # restored
    assert (root / "b.p2s").read_bytes() == b"LIVE-B"      # untouched
    assert (root / "c.p2s").read_bytes() == b"LIVE-C"      # untouched


def test_preview_respects_indices(cfg, make_game_save, tmp_path):
    root = tmp_path / "emu" / "sstates"
    record = _backup_three(cfg, make_game_save, root)

    rm = RestoreManager()
    idx_b = next(it.index for it in rm.list_backup_items(record) if it.name == "b.p2s")
    changes = rm.preview_restore(record, indices={idx_b})
    assert len(changes) == 1
    assert changes[0].destination.name == "b.p2s"


def test_indices_none_restores_everything(cfg, make_game_save, tmp_path):
    root = tmp_path / "emu" / "sstates"
    record = _backup_three(cfg, make_game_save, root)
    for name in ("a.p2s", "b.p2s", "c.p2s"):
        (root / name).unlink()

    errors = RestoreManager().restore_backup(record, force=True, indices=None)
    assert errors == []
    assert (root / "a.p2s").read_bytes() == b"BACKUP-A"
    assert (root / "b.p2s").read_bytes() == b"BACKUP-B"
    assert (root / "c.p2s").read_bytes() == b"BACKUP-C"


def test_selective_restore_is_transactional(cfg, make_game_save, tmp_path):
    """A failure mid selective-restore rolls back only the selected items."""
    import json

    root = tmp_path / "emu" / "sstates"
    record = _backup_three(cfg, make_game_save, root)
    (root / "a.p2s").write_bytes(b"LIVE-A")
    (root / "b.p2s").write_bytes(b"LIVE-B")
    (root / "c.p2s").write_bytes(b"LIVE-C")

    # Corrupt entry b so restoring {a, b} fails on b → rollback a too.
    meta = record.backup_path.with_suffix(".json")
    data = json.loads(meta.read_text(encoding="utf-8"))
    by_name = {bp["zip_path"]: idx for idx, bp in enumerate(data["backup_paths"])}
    idx_a = by_name["savestate/a.p2s"]
    idx_b = by_name["savestate/b.p2s"]
    data["backup_paths"][idx_b]["zip_path"] = "savestate/__missing__.p2s"
    meta.write_text(json.dumps(data), encoding="utf-8")

    errors = RestoreManager().restore_backup(record, force=True, indices={idx_a, idx_b})
    assert errors
    # a rolled back to its live state; c was never selected.
    assert (root / "a.p2s").read_bytes() == b"LIVE-A"
    assert (root / "c.p2s").read_bytes() == b"LIVE-C"
