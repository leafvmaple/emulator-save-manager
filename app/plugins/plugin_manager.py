"""Plugin discovery and management."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Type

from loguru import logger

from app.plugins.base import EmulatorPlugin


class PluginManager:
    """Discovers and manages emulator plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, EmulatorPlugin] = {}

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
                # Look for EmulatorPlugin subclasses in the module
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, EmulatorPlugin)
                        and attr is not EmulatorPlugin
                    ):
                        instance = attr()
                        self.register(instance)
                        logger.info(
                            "Discovered plugin: {} ({})", instance.name, full_module
                        )
            except Exception as e:
                logger.warning("Failed to load plugin {}: {}", full_module, e)

    def register(self, plugin: EmulatorPlugin) -> None:
        """Manually register a plugin instance."""
        self._plugins[plugin.name] = plugin

    def get_plugin(self, name: str) -> EmulatorPlugin | None:
        """Get a registered plugin by name."""
        return self._plugins.get(name)

    def get_all_plugins(self) -> list[EmulatorPlugin]:
        """Return all registered plugins."""
        return list(self._plugins.values())

    def get_plugin_names(self) -> list[str]:
        """Return names of all registered plugins."""
        return list(self._plugins.keys())
