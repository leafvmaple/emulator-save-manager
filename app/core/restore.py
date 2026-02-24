"""Restore engine — copies backup files back to their original emulator locations."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.models.backup_record import BackupRecord


@dataclass
class FileChange:
    """Describes a single file that will be overwritten during restore."""

    source: Path
    """File in the backup."""

    destination: Path
    """Original location where the file will be written."""

    dest_exists: bool
    """Whether the destination already exists."""

    dest_modified: datetime | None
    """Modification time of the existing destination file."""

    source_modified: datetime | None
    """Modification time of the backup file."""

    is_newer_locally: bool = False
    """True if the destination file is newer than the backup."""


class RestoreManager:
    """Handles restoring game saves from backup records."""

    def preview_restore(self, record: BackupRecord) -> list[FileChange]:
        """Preview what files will be changed by restoring a backup.

        Returns a list of :class:`FileChange` objects without actually
        writing anything.
        """
        changes: list[FileChange] = []
        info_path = record.backup_path / "backup_info.json"
        if not info_path.exists():
            logger.warning("backup_info.json not found: {}", record.backup_path)
            return changes

        with open(info_path, "r", encoding="utf-8") as f:
            info = json.load(f)

        for bp in info.get("backup_paths", []):
            folder_name = bp["folder_name"]
            source_dir = record.backup_path / folder_name
            dest_path = Path(bp["source"])
            backup_type = bp.get("type", "file")

            if backup_type in ("folder", "memcard_folder"):
                # Directory restore
                if source_dir.exists():
                    for src_file in source_dir.rglob("*"):
                        if not src_file.is_file():
                            continue
                        rel = src_file.relative_to(source_dir)
                        dst_file = dest_path / rel
                        changes.append(self._make_change(src_file, dst_file))
            else:
                # Single file restore
                filename = bp.get("filename", "")
                if filename:
                    src_file = source_dir / filename
                    dst_file = dest_path
                    if src_file.exists():
                        changes.append(self._make_change(src_file, dst_file))
                else:
                    # Legacy or directory-as-file
                    if source_dir.exists():
                        for src_file in source_dir.rglob("*"):
                            if not src_file.is_file():
                                continue
                            changes.append(self._make_change(src_file, dest_path))

        return changes

    def restore_backup(self, record: BackupRecord, force: bool = False) -> list[str]:
        """Restore files from a backup to their original locations.

        Parameters
        ----------
        record : BackupRecord
            The backup to restore.
        force : bool
            If True, overwrite even when the local file is newer.

        Returns
        -------
        list[str]
            List of error messages (empty on full success).
        """
        errors: list[str] = []
        info_path = record.backup_path / "backup_info.json"
        if not info_path.exists():
            errors.append(f"backup_info.json not found: {record.backup_path}")
            return errors

        with open(info_path, "r", encoding="utf-8") as f:
            info = json.load(f)

        for bp in info.get("backup_paths", []):
            folder_name = bp["folder_name"]
            source_dir = record.backup_path / folder_name
            dest_path = Path(bp["source"])
            backup_type = bp.get("type", "file")
            filename = bp.get("filename", "")

            try:
                if backup_type in ("folder", "memcard_folder"):
                    if not source_dir.exists():
                        errors.append(f"Source not found: {source_dir}")
                        continue
                    dest_path.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(source_dir, dest_path, dirs_exist_ok=True)
                    logger.info("Restored folder {} → {}", source_dir, dest_path)
                else:
                    if filename:
                        src_file = source_dir / filename
                    else:
                        # pick the first file in the directory
                        files = list(source_dir.iterdir()) if source_dir.exists() else []
                        src_file = files[0] if files else None

                    if src_file is None or not src_file.exists():
                        errors.append(f"Source file not found: {source_dir}/{filename}")
                        continue

                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest_path)
                    logger.info("Restored file {} → {}", src_file, dest_path)
            except Exception as e:
                msg = f"Error restoring {folder_name}: {e}"
                logger.error(msg)
                errors.append(msg)

        return errors

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _make_change(src: Path, dst: Path) -> FileChange:
        src_mtime = datetime.fromtimestamp(src.stat().st_mtime) if src.exists() else None
        if dst.exists():
            dst_mtime = datetime.fromtimestamp(dst.stat().st_mtime)
            is_newer = dst_mtime > src_mtime if src_mtime else False
        else:
            dst_mtime = None
            is_newer = False
        return FileChange(
            source=src,
            destination=dst,
            dest_exists=dst.exists(),
            dest_modified=dst_mtime,
            source_modified=src_mtime,
            is_newer_locally=is_newer,
        )
