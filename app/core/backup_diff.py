"""Diff two backups of the same game — what changed between versions."""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from app.core.state_thumbnail import THUMBNAIL_DIR
from app.models.backup_record import BackupRecord

ADDED = "added"
REMOVED = "removed"
MODIFIED = "modified"
UNCHANGED = "unchanged"


@dataclass
class FileDiff:
    """One file's status between two backups."""

    name: str
    """Archive-relative name (e.g. ``savestate/slot0.p2s``)."""

    status: str
    """One of ``added`` / ``removed`` / ``modified`` / ``unchanged``."""

    size_old: int | None = None
    size_new: int | None = None


@dataclass
class BackupDiff:
    """The per-file diff from an *old* backup to a *new* one."""

    old: BackupRecord
    new: BackupRecord
    files: list[FileDiff] = field(default_factory=list)

    @property
    def added(self) -> list[FileDiff]:
        return [f for f in self.files if f.status == ADDED]

    @property
    def removed(self) -> list[FileDiff]:
        return [f for f in self.files if f.status == REMOVED]

    @property
    def modified(self) -> list[FileDiff]:
        return [f for f in self.files if f.status == MODIFIED]

    @property
    def unchanged(self) -> list[FileDiff]:
        return [f for f in self.files if f.status == UNCHANGED]

    @property
    def changed(self) -> list[FileDiff]:
        return [f for f in self.files if f.status != UNCHANGED]

    @property
    def has_changes(self) -> bool:
        return bool(self.changed)


def _entry_map(zip_path: Path) -> dict[str, tuple[str, int]]:
    """Map each file entry's name → (content sha256, size) inside a backup zip."""
    out: dict[str, tuple[str, int]] = {}
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for zi in zf.infolist():
                if zi.is_dir():
                    continue
                if zi.filename.startswith(f"{THUMBNAIL_DIR}/"):
                    continue
                data = zf.read(zi.filename)
                out[zi.filename] = (hashlib.sha256(data).hexdigest(), len(data))
    except Exception as e:
        logger.warning("Failed to read backup for diff {}: {}", zip_path, e)
    return out


def diff_backups(old: BackupRecord, new: BackupRecord) -> BackupDiff:
    """Compute the per-file diff between two backups (``old`` → ``new``).

    Files present in *new* but not *old* are ``added``; present in *old* but
    not *new* are ``removed``; present in both are ``modified`` when their
    bytes differ, else ``unchanged``.
    """
    map_old = _entry_map(old.backup_path)
    map_new = _entry_map(new.backup_path)

    files: list[FileDiff] = []
    for name in sorted(set(map_old) | set(map_new)):
        in_old, in_new = name in map_old, name in map_new
        if in_old and in_new:
            (h_old, s_old), (h_new, s_new) = map_old[name], map_new[name]
            status = UNCHANGED if h_old == h_new else MODIFIED
            files.append(FileDiff(name, status, s_old, s_new))
        elif in_new:
            files.append(FileDiff(name, ADDED, None, map_new[name][1]))
        else:
            files.append(FileDiff(name, REMOVED, map_old[name][1], None))

    return BackupDiff(old=old, new=new, files=files)
