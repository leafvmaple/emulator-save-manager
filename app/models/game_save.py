"""Data model for game saves."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class SaveType(str, Enum):
    """Type of save file."""

    MEMCARD = "memcard"       # PS2 memory card
    BATTERY = "battery"       # Battery-backed SRAM (NES/SNES/GB)
    SAVESTATE = "savestate"   # Emulator save state
    FOLDER = "folder"         # Folder-type save
    FILE = "file"             # Single file save


@dataclass
class SaveFile:
    """Represents a single save file on disk."""

    path: Path
    """Absolute path to the save file."""

    save_type: SaveType
    """Type of save."""

    size: int = 0
    """File size in bytes."""

    modified: datetime = field(default_factory=datetime.now)
    """Last modification time."""

    def refresh_stat(self) -> None:
        """Update size and modified time from disk."""
        if self.path.exists():
            stat = self.path.stat()
            self.size = stat.st_size
            self.modified = datetime.fromtimestamp(stat.st_mtime)


@dataclass
class GameSave:
    """Represents all save data for a single game in one emulator."""

    emulator: str
    """Name of the emulator that owns this save."""

    game_name: str
    """Human-readable game title."""

    game_id: str
    """Unique game identifier (serial number, ROM filename, etc.)."""

    save_files: list[SaveFile] = field(default_factory=list)
    """List of save files belonging to this game."""

    platform: str = ""
    """Game's platform (e.g. 'PS2', 'NES', 'SNES')."""

    crc32: str = ""
    """CRC32 checksum of the game disc (hex string, e.g. '083F0E03').
    Used for cross-device sync verification — mismatches indicate different game versions."""

    @property
    def total_size(self) -> int:
        """Total size of all save files in bytes."""
        return sum(f.size for f in self.save_files)

    @property
    def last_modified(self) -> datetime | None:
        """Most recent modification time among all save files."""
        if not self.save_files:
            return None
        return max(f.modified for f in self.save_files)

    @property
    def unique_key(self) -> str:
        """Unique key combining emulator and game_id for identification."""
        return f"{self.emulator}:{self.game_id}"
