"""Headless auto-backup helpers for background/daemon use."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from loguru import logger

from app.core.backup import AutoBackupResult, BackupManager
from app.core.scanner import Scanner
from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave


@dataclass(frozen=True)
class AutoBackupRunResult:
    """Result of one scan + auto-backup sweep."""

    emulators: list[EmulatorInfo]
    saves: list[GameSave]
    backup: AutoBackupResult


def run_auto_backup_once(
    scanner: Scanner,
    backup_manager: BackupManager,
    should_cancel: Callable[[], bool] | None = None,
) -> AutoBackupRunResult:
    """Run one headless scan, then back up changed saves."""
    emulators, saves = scanner.full_scan(should_cancel=should_cancel)
    if should_cancel and should_cancel():
        backup = AutoBackupResult()
    else:
        backup = backup_manager.auto_backup_all(saves)
    logger.info(
        "Headless auto-backup: {} emulators, {} saves, {} backed up, {} skipped, {} errors",
        len(emulators), len(saves), backup.backed_up, backup.skipped, len(backup.errors),
    )
    return AutoBackupRunResult(emulators=emulators, saves=saves, backup=backup)


def run_auto_backup_loop(
    scanner: Scanner,
    backup_manager: BackupManager,
    interval_seconds: int,
    stop_event: threading.Event | None = None,
    run_immediately: bool = True,
) -> None:
    """Run auto-backup repeatedly until *stop_event* is set."""
    stop_event = stop_event or threading.Event()
    interval_seconds = max(1, int(interval_seconds))

    if run_immediately and not stop_event.is_set():
        run_auto_backup_once(scanner, backup_manager, stop_event.is_set)

    while not stop_event.wait(interval_seconds):
        run_auto_backup_once(scanner, backup_manager, stop_event.is_set)