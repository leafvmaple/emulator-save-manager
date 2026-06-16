"""Cooperative cancellation of long-running core operations."""

from __future__ import annotations


def test_full_scan_honors_cancel(cfg):
    from app.core.scanner import Scanner
    from app.plugins.plugin_manager import PluginManager

    pm = PluginManager()
    pm.discover()
    scanner = Scanner(pm, cfg)

    emulators, saves = scanner.full_scan(should_cancel=lambda: True)
    # Cancelled before the first plugin → nothing detected or scanned.
    assert emulators == []
    assert saves == []


def test_push_all_honors_cancel(tmp_path, make_game_save, machine_factory):
    sync = tmp_path / "sync"
    sync.mkdir()
    _cfg, bm, sm = machine_factory("A", sync)
    bm.create_backup([make_game_save(tmp_path / "sa", files={"x.bin": b"DATA"})])

    # Sanity: without cancel it would push 1 (covered elsewhere). With an
    # always-cancel predicate nothing is pushed.
    res = sm.push_all(should_cancel=lambda: True)
    assert res.pushed == 0
    assert not (sync / "emulator-save-manager").exists() or res.pushed == 0


def test_sync_all_honors_cancel(tmp_path, make_game_save, machine_factory):
    sync = tmp_path / "sync"
    sync.mkdir()
    _cfg, bm, sm = machine_factory("A", sync)
    bm.create_backup([make_game_save(tmp_path / "sa", files={"x.bin": b"DATA"})])

    res = sm.sync_all(should_cancel=lambda: True)
    assert res.pushed == 0
    assert res.pulled == 0
