"""Data model for backup records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.models.game_save import GameSave


@dataclass
class BackupRecord:
    """Represents a versioned backup instance."""

    game_save: GameSave
    """The game save this backup belongs to."""

    backup_time: datetime
    """When the backup was created."""

    backup_path: Path
    """Absolute path to the backup directory."""

    version: int = 1
    """Version number for this backup (auto-incremented)."""

    is_pinned: bool = False
    """Whether this backup is permanently pinned (exempt from auto-rotation)."""

    label: str = ""
    """Optional user-defined label (e.g. 'Before Final Boss')."""

    @property
    def display_time(self) -> str:
        """Formatted backup time for display."""
        return self.backup_time.strftime("%Y/%m/%d %H:%M")

    @property
    def folder_name(self) -> str:
        """Backup folder name derived from time."""
        return self.backup_time.strftime("%Y-%m-%d_%H-%M")


@dataclass
class BackupInfo:
    """Metadata stored inside each backup directory as ``backup_info.json``."""

    title: str
    game_id: str
    emulator: str
    platform: str = ""
    backup_paths: list[dict] = field(default_factory=list)
    is_pinned: bool = False
    label: str = ""
    source_machine: str = ""
    crc32: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "game_id": self.game_id,
            "emulator": self.emulator,
            "platform": self.platform,
            "backup_paths": self.backup_paths,
            "is_pinned": self.is_pinned,
            "label": self.label,
            "source_machine": self.source_machine,
            "crc32": self.crc32,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BackupInfo:
        return cls(
            title=data.get("title", ""),
            game_id=data.get("game_id", ""),
            emulator=data.get("emulator", ""),
            platform=data.get("platform", ""),
            backup_paths=data.get("backup_paths", []),
            is_pinned=data.get("is_pinned", False),
            label=data.get("label", ""),
            source_machine=data.get("source_machine", ""),
            crc32=data.get("crc32", ""),
        )
