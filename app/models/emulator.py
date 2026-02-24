"""Data model for emulator information."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EmulatorInfo:
    """Represents a detected emulator installation."""

    name: str
    """Display name of the emulator (e.g. 'PCSX2', 'Mesen2')."""

    install_path: Path
    """Path to the emulator executable or install directory."""

    data_path: Path
    """Path to the emulator's data/config directory."""

    version: str = ""
    """Detected emulator version string, if available."""

    supported_platforms: list[str] = field(default_factory=list)
    """Game platforms this emulator supports (e.g. ['PS2'], ['NES','SNES','GB'])."""

    is_portable: bool = False
    """Whether the emulator is running in portable mode."""
