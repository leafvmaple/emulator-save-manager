"""Backup → restore byte-fidelity round-trips."""

from __future__ import annotations

import json

from app.core.backup import BackupManager
from app.core.restore import RestoreManager
from app.models.game_save import SaveType


def test_backup_restore_roundtrip_files(cfg, make_game_save, tmp_path):
    save_root = tmp_path / "emu" / "sstates"
    gs = make_game_save(save_root, files={"a.p2s": b"AAA", "b.p2s": b"BBBB"})

    bm = BackupManager(cfg)
    record = bm.create_backup([gs])
    assert record.backup_path.exists()
    assert record.backup_path.with_suffix(".json").exists()

    # Wipe the originals, then restore from the backup.
    for sf in gs.save_files:
        sf.path.unlink()

    errors = RestoreManager().restore_backup(record, force=True)
    assert errors == []
    assert (save_root / "a.p2s").read_bytes() == b"AAA"
    assert (save_root / "b.p2s").read_bytes() == b"BBBB"


def test_backup_restore_roundtrip_folder(cfg, make_game_save, tmp_path):
    """Folder-type saves (a directory tree) round-trip intact."""
    folder = tmp_path / "emu" / "memcards" / "Mcd001"
    (folder / "sub").mkdir(parents=True)
    (folder / "root.bin").write_bytes(b"ROOT")
    (folder / "sub" / "nested.bin").write_bytes(b"NESTED")

    from app.models.game_save import GameSave, SaveFile
    from datetime import datetime
    gs = GameSave(
        emulator="PCSX2", game_name="SLUS-1", game_id="SLUS-1", platform="PS2",
        save_files=[SaveFile(path=folder, save_type=SaveType.FOLDER,
                             size=0, modified=datetime.now())],
    )

    bm = BackupManager(cfg)
    record = bm.create_backup([gs])

    # Remove the whole folder, then restore.
    import shutil
    shutil.rmtree(folder)

    errors = RestoreManager().restore_backup(record, force=True)
    assert errors == []
    assert (folder / "root.bin").read_bytes() == b"ROOT"
    assert (folder / "sub" / "nested.bin").read_bytes() == b"NESTED"


def test_backup_records_content_hash(cfg, make_game_save, tmp_path):
    gs = make_game_save(tmp_path / "s", files={"x.bin": b"X"})
    record = BackupManager(cfg).create_backup([gs])
    meta = json.loads(record.backup_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["content_hash"]  # non-empty


def test_rotation_keeps_newest_max_backups(cfg, tmp_path):
    """rotate_backups() drops the oldest non-pinned versions over the limit."""
    import zipfile

    bm = BackupManager(cfg)
    game_dir = bm.backup_root / "PCSX2" / "SLUS-ROT"
    game_dir.mkdir(parents=True)

    stamps = ["2020-01-01_00-00", "2020-01-02_00-00",
              "2020-01-03_00-00", "2020-01-04_00-00"]
    for i, ts in enumerate(stamps):
        with zipfile.ZipFile(game_dir / f"{ts}.zip", "w") as zf:
            zf.writestr("savestate/x.bin", bytes([i]))
        (game_dir / f"{ts}.json").write_text(
            json.dumps({"title": "g", "game_id": "SLUS-ROT",
                        "emulator": "PCSX2", "backup_paths": []}),
            encoding="utf-8",
        )

    assert len(bm.list_backups("PCSX2", "SLUS-ROT")) == 4

    cfg.set("max_backups", 2)
    bm.rotate_backups("PCSX2", "SLUS-ROT")

    remaining = bm.list_backups("PCSX2", "SLUS-ROT")
    assert {r.backup_path.stem for r in remaining} == {
        "2020-01-03_00-00", "2020-01-04_00-00"
    }
