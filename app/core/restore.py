"""Restore engine — extracts ZIP backup archives back to original emulator locations."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.models.backup_record import BackupRecord
from app.core.path_resolver import resolve_path


@dataclass
class FileChange:
    """Describes a single file that will be overwritten during restore."""

    source: str
    """Path inside the ZIP archive."""

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
    """Handles restoring game saves from ZIP backup records."""

    def preview_restore(self, record: BackupRecord) -> list[FileChange]:
        """Preview what files will be changed by restoring a backup.

        Returns a list of :class:`FileChange` objects without actually
        writing anything.
        """
        changes: list[FileChange] = []
        zip_path = record.backup_path
        meta_path = zip_path.with_suffix(".json")
        if not zip_path.exists() or not meta_path.exists():
            logger.warning("Backup files not found: {}", zip_path)
            return changes

        with open(meta_path, "r", encoding="utf-8") as f:
            info = json.load(f)

        with zipfile.ZipFile(zip_path, "r") as zf:
            names_set = {zi.filename for zi in zf.infolist()}
            for bp in info.get("backup_paths", []):
                source_path = resolve_path(bp["source"])
                is_dir = bp.get("is_dir", False)
                zip_prefix = bp.get("zip_path", "")

                if is_dir:
                    for entry in zf.infolist():
                        if entry.filename.startswith(zip_prefix) and not entry.is_dir():
                            rel = entry.filename[len(zip_prefix):]
                            dst_file = source_path / rel
                            changes.append(self._make_change(entry, dst_file))
                else:
                    if zip_prefix in names_set:
                        entry = zf.getinfo(zip_prefix)
                        changes.append(self._make_change(entry, source_path))

        return changes

    def restore_backup(self, record: BackupRecord, force: bool = False) -> list[str]:
        """Restore files from a ZIP backup to their original locations.

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
        zip_path = record.backup_path
        meta_path = zip_path.with_suffix(".json")

        if not zip_path.exists():
            errors.append(f"Backup zip not found: {zip_path}")
            return errors
        if not meta_path.exists():
            errors.append(f"Backup metadata not found: {meta_path}")
            return errors

        with open(meta_path, "r", encoding="utf-8") as f:
            info = json.load(f)

        with zipfile.ZipFile(zip_path, "r") as zf:
            for bp in info.get("backup_paths", []):
                source_path = resolve_path(bp["source"])
                is_dir = bp.get("is_dir", False)
                zip_prefix = bp.get("zip_path", "")

                try:
                    if is_dir:
                        # Restore folder: extract all files under zip_prefix
                        source_path.mkdir(parents=True, exist_ok=True)
                        for entry in zf.infolist():
                            if entry.filename.startswith(zip_prefix) and not entry.is_dir():
                                rel = entry.filename[len(zip_prefix):]
                                dst_file = source_path / rel
                                dst_file.parent.mkdir(parents=True, exist_ok=True)
                                with zf.open(entry) as src, open(dst_file, "wb") as dst:
                                    dst.write(src.read())
                        logger.info("Restored folder → {}", source_path)
                    else:
                        # Restore single file
                        source_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(zip_prefix) as src, open(source_path, "wb") as dst:
                            dst.write(src.read())
                        logger.info("Restored file → {}", source_path)
                except Exception as e:
                    msg = f"Error restoring {zip_prefix}: {e}"
                    logger.error(msg)
                    errors.append(msg)

        return errors

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _make_change(entry: zipfile.ZipInfo, dst: Path) -> FileChange:
        src_mtime = datetime(*entry.date_time) if entry.date_time else None
        if dst.exists():
            dst_mtime = datetime.fromtimestamp(dst.stat().st_mtime)
            is_newer = dst_mtime > src_mtime if src_mtime else False
        else:
            dst_mtime = None
            is_newer = False
        return FileChange(
            source=entry.filename,
            destination=dst,
            dest_exists=dst.exists(),
            dest_modified=dst_mtime,
            source_modified=src_mtime,
            is_newer_locally=is_newer,
        )
