"""Restore engine — extracts ZIP backup archives back to original emulator locations.

Restores are **transactional**: every destination that will be touched is
snapshotted first, so if any file fails to write the whole operation is rolled
back to the exact pre-restore state.  A save manager must never leave a save
half-overwritten.
"""

from __future__ import annotations

import json
import shutil
import tempfile
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


@dataclass
class RestoreItem:
    """One selectable unit of a backup (a save file or a folder save).

    Maps to a single ``backup_paths`` entry in the sidecar metadata, so the
    user can choose to restore a subset (e.g. one save slot) rather than the
    whole archive.
    """

    index: int
    """Position in the backup's ``backup_paths`` list — used as the selector."""

    save_type: str
    """Save-type key (``savestate`` / ``memcard`` / ``folder`` / …)."""

    destination: Path
    """Where this item restores to."""

    is_dir: bool
    """True if this item is a folder save."""

    dest_exists: bool = False
    """Whether the destination already exists locally."""

    is_newer_locally: bool = False
    """True if the local copy is newer than the backup (would lose changes)."""

    @property
    def name(self) -> str:
        return self.destination.name


def _resolve_emu_data_path(
    info: dict,
    detected_emulators: list | None = None,
) -> Path | None:
    """Resolve the emulator data_path for ``${EMU_DATA}`` expansion.

    Priority:
    1. Current detected emulator installation with matching name
    2. Stored ``emulator_data_path`` in metadata (resolve its placeholders)
    3. ``None`` — caller will log a warning
    """
    emulator_name = info.get("emulator", "")

    # 1. Try current detected installations
    if detected_emulators:
        for emu in detected_emulators:
            if emu.name == emulator_name:
                return emu.data_path

    # 2. Fallback: stored path from backup time
    stored = info.get("emulator_data_path", "")
    if stored:
        return resolve_path(stored)

    return None


class RestoreManager:
    """Handles restoring game saves from ZIP backup records."""

    def __init__(self) -> None:
        self._scanner = None

    def set_scanner(self, scanner) -> None:  # noqa: ANN001
        """Provide a Scanner so ``${EMU_DATA}`` can resolve via current installations."""
        self._scanner = scanner

    @property
    def _detected_emulators(self) -> list | None:
        if self._scanner is not None:
            emus = self._scanner.detected_emulators
            return emus if emus else None
        return None

    def preview_restore(
        self, record: BackupRecord, indices: set[int] | None = None
    ) -> list[FileChange]:
        """Preview what files will be changed by restoring a backup.

        Returns a list of :class:`FileChange` objects without actually
        writing anything.  If *indices* is given, only those ``backup_paths``
        entries are previewed (selective restore).
        """
        changes: list[FileChange] = []
        zip_path = record.backup_path
        meta_path = zip_path.with_suffix(".json")
        if not zip_path.exists() or not meta_path.exists():
            logger.warning("Backup files not found: {}", zip_path)
            return changes

        with open(meta_path, "r", encoding="utf-8") as f:
            info = json.load(f)

        emu_data_path = _resolve_emu_data_path(info, self._detected_emulators)

        with zipfile.ZipFile(zip_path, "r") as zf:
            names_set = {zi.filename for zi in zf.infolist()}
            for i, bp in enumerate(info.get("backup_paths", [])):
                if indices is not None and i not in indices:
                    continue
                source_path = resolve_path(bp["source"], emu_data_path)
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

    def list_backup_items(self, record: BackupRecord) -> list[RestoreItem]:
        """Enumerate the individually-restorable items in a backup.

        Each item is one ``backup_paths`` entry (a save file or folder save),
        annotated with whether its destination exists locally and whether the
        local copy is newer than the backup.
        """
        items: list[RestoreItem] = []
        zip_path = record.backup_path
        meta_path = zip_path.with_suffix(".json")
        if not zip_path.exists() or not meta_path.exists():
            return items

        with open(meta_path, "r", encoding="utf-8") as f:
            info = json.load(f)

        emu_data_path = _resolve_emu_data_path(info, self._detected_emulators)

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = {zi.filename: zi for zi in zf.infolist()}
            for i, bp in enumerate(info.get("backup_paths", [])):
                source_path = resolve_path(bp["source"], emu_data_path)
                is_dir = bp.get("is_dir", False)
                zip_prefix = bp.get("zip_path", "")

                is_newer = False
                if is_dir:
                    for entry in zf.infolist():
                        if entry.filename.startswith(zip_prefix) and not entry.is_dir():
                            rel = entry.filename[len(zip_prefix):]
                            if self._make_change(entry, source_path / rel).is_newer_locally:
                                is_newer = True
                                break
                elif zip_prefix in names:
                    is_newer = self._make_change(names[zip_prefix], source_path).is_newer_locally

                items.append(RestoreItem(
                    index=i,
                    save_type=bp.get("type", ""),
                    destination=source_path,
                    is_dir=is_dir,
                    dest_exists=source_path.exists(),
                    is_newer_locally=is_newer,
                ))
        return items

    def restore_backup(
        self,
        record: BackupRecord,
        force: bool = False,
        indices: set[int] | None = None,
    ) -> list[str]:
        """Restore files from a ZIP backup to their original locations.

        The operation is all-or-nothing: every destination is snapshotted
        before any write, and if any file fails the whole restore is rolled
        back to the pre-restore state.

        Parameters
        ----------
        record : BackupRecord
            The backup to restore.
        force : bool
            If True, overwrite even when the local file is newer.
        indices : set[int], optional
            If given, only restore those ``backup_paths`` entries (selective
            restore); otherwise restore the whole backup.

        Returns
        -------
        list[str]
            List of error messages (empty on full success).  A non-empty
            result means nothing was changed (rollback succeeded) — unless a
            rollback error is also present.
        """
        zip_path = record.backup_path
        meta_path = zip_path.with_suffix(".json")

        if not zip_path.exists():
            return [f"Backup zip not found: {zip_path}"]
        if not meta_path.exists():
            return [f"Backup metadata not found: {meta_path}"]

        with open(meta_path, "r", encoding="utf-8") as f:
            info = json.load(f)

        emu_data_path = _resolve_emu_data_path(info, self._detected_emulators)

        # Resolve every selected destination up front.
        targets: list[tuple[dict, Path]] = []
        for i, bp in enumerate(info.get("backup_paths", [])):
            if indices is not None and i not in indices:
                continue
            targets.append((bp, resolve_path(bp["source"], emu_data_path)))

        # --- Snapshot phase: copy aside everything we may overwrite ---
        snapshot_dir = Path(tempfile.mkdtemp(prefix="esm_restore_"))
        # (source_path, snapshot_path_or_None, existed_before)
        snapshots: list[tuple[Path, Path | None, bool]] = []
        try:
            for i, (_bp, source_path) in enumerate(targets):
                if source_path.exists():
                    snap = snapshot_dir / str(i)
                    if source_path.is_dir():
                        shutil.copytree(source_path, snap)
                    else:
                        shutil.copy2(source_path, snap)
                    snapshots.append((source_path, snap, True))
                else:
                    snapshots.append((source_path, None, False))
        except Exception as e:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            logger.error("Failed to snapshot before restore: {}", e)
            return [f"Failed to snapshot before restore: {e}"]

        # --- Write phase: any failure triggers a full rollback ---
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for bp, source_path in targets:
                    is_dir = bp.get("is_dir", False)
                    zip_prefix = bp.get("zip_path", "")
                    if is_dir:
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
                        source_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(zip_prefix) as src, open(source_path, "wb") as dst:
                            dst.write(src.read())
                        logger.info("Restored file → {}", source_path)
        except Exception as e:
            msg = f"Restore failed ({e}); rolling back changes"
            logger.error(msg)
            errors = [msg]
            errors.extend(self._rollback(snapshots))
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            return errors

        shutil.rmtree(snapshot_dir, ignore_errors=True)
        return []

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _rollback(
        snapshots: list[tuple[Path, Path | None, bool]],
    ) -> list[str]:
        """Restore destinations to their pre-restore state after a failure.

        For destinations that existed, the snapshot is copied back; for those
        that were newly created during the restore, whatever was written is
        removed.  Processed in reverse order so earlier writes are undone last.
        """
        errors: list[str] = []
        for source_path, snap, existed in reversed(snapshots):
            try:
                # Clear whatever the failed restore left behind.
                if source_path.is_dir():
                    shutil.rmtree(source_path, ignore_errors=True)
                elif source_path.exists():
                    source_path.unlink()

                if existed and snap is not None:
                    if snap.is_dir():
                        shutil.copytree(snap, source_path)
                    else:
                        source_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(snap, source_path)
            except Exception as e:
                errors.append(f"Rollback failed for {source_path}: {e}")
                logger.error("Rollback failed for {}: {}", source_path, e)
        return errors

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
