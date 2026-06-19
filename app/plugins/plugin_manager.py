"""Plugin discovery and management."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from loguru import logger

from app.plugins.base import EmulatorPlugin, GamePlugin


class PluginManager:
    """Discovers and manages emulator plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, EmulatorPlugin] = {}
        self._game_plugins: dict[str, GamePlugin] = {}

    def discover(self) -> None:
        """Auto-discover all plugins in the ``app.plugins`` package.

        Scans sub-packages for classes that inherit from ``EmulatorPlugin``
        and registers them.
        """
        plugins_dir = Path(__file__).parent
        for finder, module_name, is_pkg in pkgutil.iter_modules([str(plugins_dir)]):
            if module_name in ("base", "plugin_manager", "__init__"):
                continue
            if not is_pkg:
                continue
            full_module = f"app.plugins.{module_name}.plugin"
            try:
                mod = importlib.import_module(full_module)
                # Look for EmulatorPlugin / GamePlugin subclasses in the module.
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, EmulatorPlugin)
                        and attr is not EmulatorPlugin
                        and not getattr(attr, "__abstractmethods__", None)
                    ):
                        instance = attr()
                        self.register(instance)
                        logger.info(
                            "Discovered plugin: {} ({})", instance.name, full_module
                        )
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, GamePlugin)
                        and attr is not GamePlugin
                        and not getattr(attr, "__abstractmethods__", None)
                    ):
                        instance = attr()
                        self.register_game_plugin(instance)
                        logger.info(
                            "Discovered game plugin: {} ({})",
                            instance.name, full_module,
                        )
            except Exception as e:
                logger.warning("Failed to load plugin {}: {}", full_module, e)

    def register(self, plugin: EmulatorPlugin) -> None:
        """Manually register a plugin instance."""
        self._plugins[plugin.name] = plugin

    def register_game_plugin(self, plugin: GamePlugin) -> None:
        """Manually register a platform/ROM plugin instance."""
        self._game_plugins[plugin.name] = plugin
        self._game_plugins[plugin.platform] = plugin

    def get_plugin(self, name: str) -> EmulatorPlugin | None:
        """Get a registered plugin by name."""
        return self._plugins.get(name)

    def get_game_plugin(self, platform: str) -> GamePlugin | None:
        """Get a registered ROM/platform plugin by name or platform ID."""
        return self._game_plugins.get(platform)

    def get_all_plugins(self) -> list[EmulatorPlugin]:
        """Return all registered plugins."""
        return list(self._plugins.values())

    def get_all_game_plugins(self) -> list[GamePlugin]:
        """Return all registered ROM/platform plugins."""
        return list(dict.fromkeys(self._game_plugins.values()))

    def get_plugin_names(self) -> list[str]:
        """Return names of all registered plugins."""
        return list(self._plugins.keys())

    def get_game_plugin_names(self) -> list[str]:
        """Return names of all registered ROM/platform plugins."""
        return [p.name for p in self.get_all_game_plugins()]
