"""Conflict detection and resolution for multi-device sync."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from loguru import logger


class ConflictResolution(str, Enum):
    """How a conflict should be resolved."""

    USE_LOCAL = "use_local"
    USE_REMOTE = "use_remote"
    KEEP_BOTH = "keep_both"
    SKIP = "skip"


@dataclass
class ConflictInfo:
    """Details of a single file conflict between local and remote versions."""

    game_id: str
    emulator: str
    relative_path: str
    local_path: Path
    remote_path: Path
    local_mtime: datetime
    remote_mtime: datetime
    local_hash: str
    remote_hash: str
    remote_machine: str = ""

    @property
    def is_real_conflict(self) -> bool:
        """A conflict is real only when both sides changed (hashes differ)."""
        return self.local_hash != self.remote_hash


def file_sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except Exception as e:
        logger.debug("Hash failed for {}: {}", path, e)
        return ""
    return h.hexdigest()


def dir_sha256(dir_path: Path) -> str:
    """Compute a combined SHA-256 hash over all files in a directory."""
    h = hashlib.sha256()
    try:
        for f in sorted(dir_path.rglob("*")):
            if f.is_file():
                with open(f, "rb") as fh:
                    for chunk in iter(lambda: fh.read(8192), b""):
                        h.update(chunk)
    except Exception as e:
        logger.debug("Dir hash failed for {}: {}", dir_path, e)
        return ""
    return h.hexdigest()


class ConflictDetector:
    """Detects conflicts between local and remote save files."""

    def detect(
        self,
        local_path: Path,
        remote_path: Path,
        game_id: str = "",
        emulator: str = "",
        remote_machine: str = "",
    ) -> ConflictInfo | None:
        """Compare local and remote files/directories.

        Returns ``None`` if there is no conflict (one side missing, or identical).
        Returns a :class:`ConflictInfo` if both sides exist and differ.
        """
        if not local_path.exists() or not remote_path.exists():
            return None

        # Compute hashes
        if local_path.is_dir():
            local_hash = dir_sha256(local_path)
            remote_hash = dir_sha256(remote_path)
            local_mtime = datetime.fromtimestamp(
                max((f.stat().st_mtime for f in local_path.rglob("*") if f.is_file()), default=0)
            )
            remote_mtime = datetime.fromtimestamp(
                max((f.stat().st_mtime for f in remote_path.rglob("*") if f.is_file()), default=0)
            )
        else:
            local_hash = file_sha256(local_path)
            remote_hash = file_sha256(remote_path)
            local_mtime = datetime.fromtimestamp(local_path.stat().st_mtime)
            remote_mtime = datetime.fromtimestamp(remote_path.stat().st_mtime)

        if local_hash == remote_hash:
            return None  # identical — no conflict

        return ConflictInfo(
            game_id=game_id,
            emulator=emulator,
            relative_path=str(local_path.name),
            local_path=local_path,
            remote_path=remote_path,
            local_mtime=local_mtime,
            remote_mtime=remote_mtime,
            local_hash=local_hash,
            remote_hash=remote_hash,
            remote_machine=remote_machine,
        )

    def auto_resolve(self, conflict: ConflictInfo) -> ConflictResolution:
        """Apply a simple auto-resolution heuristic.

        If only one side is newer, use that side.
        If both modified at the same time but hashes differ, return SKIP
        (needs user intervention).
        """
        if conflict.local_mtime > conflict.remote_mtime:
            return ConflictResolution.USE_LOCAL
        elif conflict.remote_mtime > conflict.local_mtime:
            return ConflictResolution.USE_REMOTE
        else:
            return ConflictResolution.SKIP
