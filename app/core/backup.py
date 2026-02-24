"""Versioned backup engine — creates, lists, and rotates save backups."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.config import Config
from app.models.backup_record import BackupInfo, BackupRecord
from app.models.game_save import GameSave, SaveFile, SaveType


class BackupManager:
    """Manages versioned backups of game saves."""

    def __init__(self, config: Config) -> None:
        self._cfg = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def backup_root(self) -> Path:
        return self._cfg.backup_path

    def create_backup(self, game_save: GameSave) -> BackupRecord:
        """Create a new versioned backup for *game_save*.

        Directory layout::

            {backup_root}/{emulator}/{game_id}/{YYYY-MM-DD_HH-mm}/
                backup_info.json
                path1/  (copy of save file/dir)
                path2/
                ...
        """
        now = datetime.now()
        folder_name = now.strftime("%Y-%m-%d_%H-%M")
        game_backup_dir = self.backup_root / game_save.emulator / game_save.game_id
        backup_dir = game_backup_dir / folder_name
        backup_dir.mkdir(parents=True, exist_ok=True)

        backup_paths: list[dict] = []
        for idx, sf in enumerate(game_save.save_files, start=1):
            dest_name = f"path{idx}"
            dest_path = backup_dir / dest_name
            dest_path.mkdir(parents=True, exist_ok=True)
            try:
                if sf.path.is_dir():
                    shutil.copytree(sf.path, dest_path, dirs_exist_ok=True)
                    backup_paths.append({
                        "folder_name": dest_name,
                        "source": str(sf.path),
                        "type": sf.save_type.value,
                    })
                elif sf.path.is_file():
                    shutil.copy2(sf.path, dest_path / sf.path.name)
                    backup_paths.append({
                        "folder_name": dest_name,
                        "source": str(sf.path),
                        "type": sf.save_type.value,
                        "filename": sf.path.name,
                    })
            except Exception as e:
                logger.error("Failed to copy {}: {}", sf.path, e)
                raise

        # Write metadata
        info = BackupInfo(
            title=game_save.game_name,
            game_id=game_save.game_id,
            emulator=game_save.emulator,
            platform=game_save.platform,
            backup_paths=backup_paths,
            source_machine=self._cfg.machine_id,
            crc32=game_save.crc32,
        )
        info_path = backup_dir / "backup_info.json"
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump(info.to_dict(), f, indent=4, ensure_ascii=False)

        # Version number
        existing = self.list_backups(game_save.emulator, game_save.game_id)
        version = len(existing) + 1

        record = BackupRecord(
            game_save=game_save,
            backup_time=now,
            backup_path=backup_dir,
            version=version,
        )
        logger.info("Created backup v{} for {} at {}", version, game_save.game_name, backup_dir)

        # Auto-rotate
        self.rotate_backups(game_save.emulator, game_save.game_id)
        return record

    def list_backups(self, emulator: str, game_id: str) -> list[BackupRecord]:
        """List all backup records for a given game, sorted newest-first."""
        game_dir = self.backup_root / emulator / game_id
        if not game_dir.exists():
            return []

        records: list[BackupRecord] = []
        for child in sorted(game_dir.iterdir(), reverse=True):
            if not child.is_dir():
                continue
            info_path = child / "backup_info.json"
            if not info_path.exists():
                continue
            try:
                with open(info_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                info = BackupInfo.from_dict(data)
                # Parse folder name as datetime
                try:
                    bt = datetime.strptime(child.name, "%Y-%m-%d_%H-%M")
                except ValueError:
                    bt = datetime.fromtimestamp(child.stat().st_mtime)

                gs = GameSave(
                    emulator=info.emulator,
                    game_name=info.title,
                    game_id=info.game_id,
                    platform=info.platform,
                )
                records.append(BackupRecord(
                    game_save=gs,
                    backup_time=bt,
                    backup_path=child,
                    is_pinned=info.is_pinned,
                    label=info.label,
                ))
            except Exception as e:
                logger.warning("Failed to read backup info {}: {}", info_path, e)

        # Assign version numbers (oldest = 1)
        for i, r in enumerate(reversed(records), start=1):
            r.version = i
        return records

    def list_all_backups(self) -> dict[str, list[BackupRecord]]:
        """Return all backups grouped by ``emulator:game_id``."""
        result: dict[str, list[BackupRecord]] = {}
        if not self.backup_root.exists():
            return result
        for emu_dir in self.backup_root.iterdir():
            if not emu_dir.is_dir():
                continue
            for game_dir in emu_dir.iterdir():
                if not game_dir.is_dir():
                    continue
                key = f"{emu_dir.name}:{game_dir.name}"
                records = self.list_backups(emu_dir.name, game_dir.name)
                if records:
                    result[key] = records
        return result

    def rotate_backups(self, emulator: str, game_id: str) -> int:
        """Delete oldest non-pinned backups exceeding the max limit.

        Returns the number of backups deleted.
        """
        max_backups = self._cfg.max_backups
        records = self.list_backups(emulator, game_id)
        non_pinned = [r for r in records if not r.is_pinned]

        deleted = 0
        if len(non_pinned) > max_backups:
            # records are sorted newest-first; remove from the end (oldest)
            to_delete = non_pinned[max_backups:]
            for record in to_delete:
                try:
                    shutil.rmtree(record.backup_path)
                    deleted += 1
                    logger.info("Rotated old backup: {}", record.backup_path)
                except Exception as e:
                    logger.error("Failed to delete backup {}: {}", record.backup_path, e)
        return deleted

    def pin_backup(self, record: BackupRecord, label: str = "") -> None:
        """Mark a backup as permanently pinned."""
        self._update_backup_info(record.backup_path, is_pinned=True, label=label)
        record.is_pinned = True
        record.label = label

    def unpin_backup(self, record: BackupRecord) -> None:
        """Remove the pin from a backup."""
        self._update_backup_info(record.backup_path, is_pinned=False)
        record.is_pinned = False

    def delete_backup(self, record: BackupRecord) -> None:
        """Delete a specific backup."""
        if record.backup_path.exists():
            shutil.rmtree(record.backup_path)
            logger.info("Deleted backup: {}", record.backup_path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_backup_info(self, backup_path: Path, **kwargs: object) -> None:
        info_path = backup_path / "backup_info.json"
        if not info_path.exists():
            return
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.update(kwargs)
            with open(info_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to update backup info {}: {}", info_path, e)
