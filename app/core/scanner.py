"""Scan dispatcher — coordinates plugin-based emulator detection and save scanning."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from app.config import Config
from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave
from app.plugins.plugin_manager import PluginManager


class Scanner:
    """High-level scanner that uses plugins to detect emulators and scan saves."""

    def __init__(self, plugin_manager: PluginManager, config: Config) -> None:
        self._pm = plugin_manager
        self._cfg = config
        self._detected_emulators: list[EmulatorInfo] = []
        self._scanned_saves: list[GameSave] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def detected_emulators(self) -> list[EmulatorInfo]:
        return list(self._detected_emulators)

    @property
    def scanned_saves(self) -> list[GameSave]:
        return list(self._scanned_saves)

    def detect_all_emulators(self) -> list[EmulatorInfo]:
        """Run detection across all registered plugins."""
        self._detected_emulators.clear()
        for plugin in self._pm.get_all_plugins():
            try:
                logger.info("Detecting {}…", plugin.display_name)
                extra = [
                    Path(p)
                    for p in self._cfg.get_emulator_install_paths(plugin.name)
                    if p
                ]
                installations = plugin.detect_installation(extra or None)
                self._detected_emulators.extend(installations)
            except Exception as e:
                logger.error("Error detecting {}: {}", plugin.name, e)
        logger.info("Total emulators detected: {}", len(self._detected_emulators))
        return self.detected_emulators

    def scan_all_saves(self) -> list[GameSave]:
        """Scan saves for all detected emulators."""
        self._scanned_saves.clear()
        for emu_info in self._detected_emulators:
            plugin = self._pm.get_plugin(emu_info.name)
            if plugin is None:
                continue
            try:
                custom_paths = [
                    Path(p)
                    for p in self._cfg.get_emulator_paths(emu_info.name)
                ]
                logger.info("Scanning saves for {} at {}", emu_info.name, emu_info.data_path)
                saves = plugin.scan_saves(emu_info, custom_paths or None)
                self._scanned_saves.extend(saves)
            except Exception as e:
                logger.error("Error scanning saves for {}: {}", emu_info.name, e)
        logger.info("Total game saves found: {}", len(self._scanned_saves))
        return self.scanned_saves

    def full_scan(self) -> tuple[list[EmulatorInfo], list[GameSave]]:
        """Run detection + scanning in one call."""
        emulators = self.detect_all_emulators()
        saves = self.scan_all_saves()
        return emulators, saves
