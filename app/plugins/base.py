"""Abstract base class for emulator plugins."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from loguru import logger

from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave


class EmulatorPlugin(ABC):
    """Base class that every emulator plugin must implement.

    Game-name resolution
    ~~~~~~~~~~~~~~~~~~~~
    If the plugin ships a ``game_names.json`` in its package directory,
    the base class loads it automatically and provides
    :meth:`resolve_display_names`.

    Expected format::

        {
            "GAME_ID": {
                "en_US": "English Name",
                "zh_CN": "中文名",
                "ja_JP": "日本語名"
            },
            ...
        }

    Only the ``en_US`` key is required; other locales are optional and
    fall back to ``en_US`` when missing.
    """

    # Lazy-loaded class-level game-name table — shared by all instances
    # of the same concrete plugin class.
    _game_names: dict[str, dict[str, str]] | None = None

    # ------------------------------------------------------------------
    # Game-name table
    # ------------------------------------------------------------------

    @classmethod
    def _plugin_dir(cls) -> Path:
        """Return filesystem path of the concrete plugin's package."""
        import inspect
        return Path(inspect.getfile(cls)).parent

    @classmethod
    def _load_game_names(cls) -> dict[str, dict[str, str]]:
        """Load ``game_names.json`` from the plugin directory (if present)."""
        if cls._game_names is not None:
            return cls._game_names

        table_path = cls._plugin_dir() / "game_names.json"
        if not table_path.is_file():
            cls._game_names = {}
            return cls._game_names

        try:
            with open(table_path, "r", encoding="utf-8") as f:
                cls._game_names = json.load(f)
            logger.info(
                "Loaded {} game-name entries from {}",
                len(cls._game_names), table_path.name,
            )
        except Exception as e:
            logger.warning("Failed to load {}: {}", table_path, e)
            cls._game_names = {}

        return cls._game_names

    def get_display_name(self, game_id: str, lang: str = "en_US") -> str | None:
        """Look up a display name for *game_id* in the local table.

        Returns ``None`` if the game is not found.
        """
        table = self._load_game_names()
        entry = table.get(game_id) or table.get(game_id.upper())
        if entry is None:
            return None
        return entry.get(lang) or entry.get("en_US")

    def resolve_display_names(self, saves: list[GameSave]) -> None:
        """Set human-readable ``game_name`` for saves using the local table.

        Subclasses may override to add custom logic (e.g. stripping suffixes
        before lookup).  The default implementation handles the common case.
        """
        from app.i18n import get_current_language
        lang = get_current_language()
        table = self._load_game_names()
        if not table:
            return
        for save in saves:
            display = self.get_display_name(save.game_id, lang)
            if display:
                save.game_name = display

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

    def get_cover_urls(self, game_id: str) -> list[str]:
        """Return candidate cover-art download URLs for *game_id*.

        Override in subclasses that can resolve game IDs to remote
        cover / icon image URLs (e.g. via GameTDB).  Multiple URLs may
        be returned so the caller can try them in order (e.g. different
        region variants).  The default implementation returns an empty
        list (no cover available).
        """
        return []
