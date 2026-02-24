"""Abstract base class for emulator plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave


class EmulatorPlugin(ABC):
    """Base class that every emulator plugin must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name of the emulator (e.g. 'PCSX2')."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable display name."""
        ...

    @property
    @abstractmethod
    def supported_platforms(self) -> list[str]:
        """List of game platforms this emulator supports."""
        ...

    @abstractmethod
    def detect_installation(self) -> list[EmulatorInfo]:
        """Auto-detect emulator installations on this machine.

        Returns a list because the user might have multiple versions/installs.
        """
        ...

    @abstractmethod
    def scan_saves(
        self,
        emulator_info: EmulatorInfo,
        custom_paths: list[Path] | None = None,
    ) -> list[GameSave]:
        """Scan for game saves given an emulator installation.

        Parameters
        ----------
        emulator_info : EmulatorInfo
            Detected emulator installation info.
        custom_paths : list[Path], optional
            Additional user-specified paths to scan.

        Returns
        -------
        list[GameSave]
            All game saves found.
        """
        ...

    @abstractmethod
    def get_save_directories(self, emulator_info: EmulatorInfo) -> dict[str, Path]:
        """Return a mapping of save-type -> directory path.

        Example: {"memcards": Path(...), "savestates": Path(...)}
        """
        ...
