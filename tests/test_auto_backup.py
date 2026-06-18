"""Auto-backup — change detection and sweep over many games."""

from __future__ import annotations

from app.core.backup import BackupManager, source_content_hash


def test_source_hash_is_stable_and_order_independent(cfg, make_game_save, tmp_path):
    gs = make_game_save(tmp_path / "s", files={"a.bin": b"AAA", "b.bin": b"BBB"})
    h1 = source_content_hash([gs])
    h2 = source_content_hash([gs])
    assert h1 and h1 == h2

    # Reordering the save_files must not change the hash.
    gs.save_files.reverse()
    assert source_content_hash([gs]) == h1


def test_source_hash_changes_with_content(cfg, make_game_save, tmp_path):
    root = tmp_path / "s"
    gs = make_game_save(root, files={"a.bin": b"AAA"})
    before = source_content_hash([gs])
    (root / "a.bin").write_bytes(b"CHANGED")
    assert source_content_hash([gs]) != before


def test_backup_if_changed_skips_unchanged(cfg, make_game_save, tmp_path):
    gs = make_game_save(tmp_path / "s", files={"a.bin": b"AAA"})
    bm = BackupManager(cfg)

    first = bm.backup_if_changed([gs])
    assert first is not None                       # no prior backup → backs up
    assert bm.backup_if_changed([gs]) is None      # unchanged → skipped
    assert len(bm.list_backups(gs.emulator, gs.game_id)) == 1


def test_backup_if_changed_backs_up_after_change(cfg, make_game_save, tmp_path):
    root = tmp_path / "s"
    gs = make_game_save(root, files={"a.bin": b"AAA"})
    bm = BackupManager(cfg)

    assert bm.backup_if_changed([gs]) is not None
    (root / "a.bin").write_bytes(b"NEW")
    assert bm.backup_if_changed([gs]) is not None
    assert len(bm.list_backups(gs.emulator, gs.game_id)) == 2


def test_auto_backup_all_counts(cfg, make_game_save, tmp_path):
    g1 = make_game_save(tmp_path / "g1", game_id="GAME-1", files={"a.bin": b"1"})
    g2 = make_game_save(tmp_path / "g2", game_id="GAME-2", files={"a.bin": b"2"})
    bm = BackupManager(cfg)

    r1 = bm.auto_backup_all([g1, g2])
    assert r1.backed_up == 2 and r1.skipped == 0 and not r1.errors

    # Nothing changed → all skipped.
    r2 = bm.auto_backup_all([g1, g2])
    assert r2.backed_up == 0 and r2.skipped == 2

    # Change only g1 → exactly one backed up.
    (tmp_path / "g1" / "a.bin").write_bytes(b"1-changed")
    r3 = bm.auto_backup_all([g1, g2])
    assert r3.backed_up == 1 and r3.skipped == 1
    assert g1.game_name in r3.games


def test_source_hash_recorded_in_metadata(cfg, make_game_save, tmp_path):
    import json
    gs = make_game_save(tmp_path / "s", files={"a.bin": b"AAA"})
    record = BackupManager(cfg).create_backup([gs])
    meta = json.loads(record.backup_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["source_hash"] == source_content_hash([gs])


def test_auto_backup_skips_large_size_shrink(cfg, make_game_save, tmp_path):
    root = tmp_path / "s"
    gs = make_game_save(root, files={"a.bin": b"A" * (512 * 1024)})
    bm = BackupManager(cfg)

    assert bm.auto_backup_all([gs]).backed_up == 1
    (root / "a.bin").write_bytes(b"x")

    result = bm.auto_backup_all([gs])
    assert result.backed_up == 0
    assert result.errors and "save size anomaly" in result.errors[0]
    assert len(bm.list_backups(gs.emulator, gs.game_id)) == 1


def test_headless_auto_backup_once_runs_scan_then_backup():
    from app.core.auto_backup import run_auto_backup_once
    from app.core.backup import AutoBackupResult

    class FakeScanner:
        def __init__(self) -> None:
            self.called = False

        def full_scan(self, should_cancel=None):  # noqa: ANN001
            self.called = True
            return ["emu"], ["save"]

    class FakeBackupManager:
        def __init__(self) -> None:
            self.saves = None

        def auto_backup_all(self, saves):  # noqa: ANN001
            self.saves = saves
            return AutoBackupResult(backed_up=1)

    scanner = FakeScanner()
    backup_manager = FakeBackupManager()
    result = run_auto_backup_once(scanner, backup_manager)

    assert scanner.called is True
    assert backup_manager.saves == ["save"]
    assert result.backup.backed_up == 1