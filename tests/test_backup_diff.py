"""Diffing two backups of the same game."""

from __future__ import annotations

import zipfile

from app.core.backup import BackupManager
from app.core.backup_diff import diff_backups


def _names(diffs):
    return sorted(f.name for f in diffs)


def test_diff_detects_add_modify_remove_unchanged(cfg, make_game_save, tmp_path):
    root = tmp_path / "emu" / "sstates"
    bm = BackupManager(cfg)

    gs1 = make_game_save(
        root, files={"a.p2s": b"AAA", "b.p2s": b"BBB", "keep.p2s": b"SAME"}
    )
    rec1 = bm.create_backup([gs1])

    # a modified, b removed, c added, keep unchanged.
    gs2 = make_game_save(
        root, files={"a.p2s": b"AAA-CHANGED", "c.p2s": b"CCC", "keep.p2s": b"SAME"}
    )
    rec2 = bm.create_backup([gs2])

    d = diff_backups(rec1, rec2)
    assert d.has_changes
    assert _names(d.modified) == ["savestate/a.p2s"]
    assert _names(d.removed) == ["savestate/b.p2s"]
    assert _names(d.added) == ["savestate/c.p2s"]
    assert _names(d.unchanged) == ["savestate/keep.p2s"]


def test_diff_sizes_reported(cfg, make_game_save, tmp_path):
    root = tmp_path / "s"
    bm = BackupManager(cfg)
    rec1 = bm.create_backup([make_game_save(root, files={"a.bin": b"AAA"})])
    rec2 = bm.create_backup([make_game_save(root, files={"a.bin": b"AAAAAAAA"})])

    a = next(f for f in diff_backups(rec1, rec2).files if f.name == "savestate/a.bin")
    assert a.status == "modified"
    assert a.size_old == 3 and a.size_new == 8


def test_diff_identical_backups_has_no_changes(cfg, make_game_save, tmp_path):
    gs = make_game_save(tmp_path / "s", files={"a.bin": b"X", "b.bin": b"Y"})
    bm = BackupManager(cfg)
    rec1 = bm.create_backup([gs])
    rec2 = bm.create_backup([gs])

    d = diff_backups(rec1, rec2)
    assert not d.has_changes
    assert d.files and all(f.status == "unchanged" for f in d.files)


def test_diff_ignores_backup_thumbnail_entries(cfg, make_game_save, tmp_path):
    gs = make_game_save(tmp_path / "s", files={"a.bin": b"X"})
    bm = BackupManager(cfg)
    rec1 = bm.create_backup([gs])
    rec2 = bm.create_backup([gs])

    with zipfile.ZipFile(rec2.backup_path, "a") as zf:
        zf.writestr("thumbnails/000_slot0.png", b"DISPLAY-ONLY")

    d = diff_backups(rec1, rec2)
    assert not d.has_changes


def test_diff_folder_saves(cfg, tmp_path):
    """Folder saves diff at the level of their contained files."""
    from datetime import datetime
    from app.models.game_save import GameSave, SaveFile, SaveType

    folder = tmp_path / "emu" / "memcards" / "Mcd001"
    (folder).mkdir(parents=True)
    (folder / "f1.bin").write_bytes(b"ONE")
    (folder / "f2.bin").write_bytes(b"TWO")

    def gs():
        return GameSave(
            emulator="PCSX2", game_name="G", game_id="G", platform="PS2",
            save_files=[SaveFile(path=folder, save_type=SaveType.FOLDER,
                                 size=0, modified=datetime.now())],
        )

    bm = BackupManager(cfg)
    rec1 = bm.create_backup([gs()])
    (folder / "f2.bin").write_bytes(b"TWO-CHANGED")
    (folder / "f3.bin").write_bytes(b"THREE")
    rec2 = bm.create_backup([gs()])

    d = diff_backups(rec1, rec2)
    assert _names(d.modified) == ["folder/Mcd001/f2.bin"]
    assert _names(d.added) == ["folder/Mcd001/f3.bin"]
    assert _names(d.unchanged) == ["folder/Mcd001/f1.bin"]
