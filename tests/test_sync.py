"""Multi-device sync through a shared folder."""

from __future__ import annotations

from app.core.conflict import ConflictResolution


def test_push_then_pull_propagates_backup(tmp_path, make_game_save, machine_factory):
    sync = tmp_path / "sync"
    sync.mkdir()

    _cfgA, bmA, smA = machine_factory("A", sync)
    _cfgB, bmB, smB = machine_factory("B", sync)

    gs = make_game_save(tmp_path / "savesA", files={"a.bin": b"HELLO"})
    bmA.create_backup([gs])

    res_push = smA.push_all()
    assert res_push.pushed == 1
    assert not res_push.conflicts and not res_push.errors

    # B has nothing yet; pulling brings A's backup over.
    assert bmB.list_all_backups() == {}
    res_pull = smB.pull_all()
    assert res_pull.pulled == 1
    assert "PCSX2:SLUS-12345" in bmB.list_all_backups()


def test_identical_content_is_not_a_conflict(tmp_path, make_game_save, machine_factory):
    """Same save content on both sides must sync clean (the false-positive fix)."""
    sync = tmp_path / "sync"
    sync.mkdir()
    _cfgA, bmA, smA = machine_factory("A", sync)
    _cfgB, bmB, smB = machine_factory("B", sync)

    bmA.create_backup([make_game_save(tmp_path / "sa", files={"x.bin": b"SAME"})])
    smA.push_all()
    smB.pull_all()  # B now holds A's archive

    # B re-pushes the same content — different machine_id in metadata, but
    # identical save bytes → no conflict, nothing to do.
    res = smB.push_all()
    assert not res.conflicts


def test_divergent_content_raises_conflict(tmp_path, make_game_save, machine_factory):
    sync = tmp_path / "sync"
    sync.mkdir()
    _cfgA, bmA, smA = machine_factory("A", sync)
    _cfgB, bmB, smB = machine_factory("B", sync)

    bmA.create_backup([make_game_save(tmp_path / "sa", files={"x.bin": b"AAA"})])
    smA.push_all()

    # B has its own different backup for the same game.
    bmB.create_backup([make_game_save(tmp_path / "sb", files={"x.bin": b"BBB"})])

    res = smB.pull_all()
    assert res.conflicts, "differing content on both sides should conflict"
    conflict = res.conflicts[0]
    assert conflict.game_id == "SLUS-12345"


def test_apply_resolution_use_remote(tmp_path, make_game_save, machine_factory):
    sync = tmp_path / "sync"
    sync.mkdir()
    _cfgA, bmA, smA = machine_factory("A", sync)
    _cfgB, bmB, smB = machine_factory("B", sync)

    bmA.create_backup([make_game_save(tmp_path / "sa", files={"x.bin": b"AAA"})])
    smA.push_all()
    bmB.create_backup([make_game_save(tmp_path / "sb", files={"x.bin": b"BBB"})])

    conflict = smB.pull_all().conflicts[0]
    errors = smB.apply_resolution(conflict, ConflictResolution.USE_REMOTE)
    assert errors == []

    # After USE_REMOTE the two sides reconcile: a subsequent pull is clean.
    assert not smB.pull_all().conflicts


def test_sync_all_composes_push_and_pull(tmp_path, make_game_save, machine_factory):
    sync = tmp_path / "sync"
    sync.mkdir()
    _cfgA, bmA, smA = machine_factory("A", sync)
    bmA.create_backup([make_game_save(tmp_path / "sa", files={"x.bin": b"DATA"})])

    res = smA.sync_all()
    assert res.pushed == 1
    assert not res.errors


def test_push_all_reports_transfer_progress(tmp_path, make_game_save, machine_factory):
    sync = tmp_path / "sync"
    sync.mkdir()
    _cfgA, bmA, smA = machine_factory("A", sync)
    bmA.create_backup([make_game_save(tmp_path / "sa", files={"x.bin": b"DATA"})])

    events = []
    res = smA.push_all(progress_callback=events.append)

    assert res.pushed == 1
    assert events
    assert events[-1].operation == "push"
    assert events[-1].current == events[-1].total
    assert events[-1].total > 0
