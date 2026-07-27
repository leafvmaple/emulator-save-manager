"""Save-info plugin discovery, matching and cached extraction."""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
from pathlib import Path

from loguru import logger

from app.saveinfo.base import SaveInfoPlugin, SaveInfoResult


class SaveInfoManager:
    """Discovers save-info plugins and runs them against save files.

    Built-in plugins live as sub-packages of ``app.saveinfo`` with a
    ``plugin.py`` module (mirroring ``app.plugins``).  Users can drop extra
    ``*.py`` plugin files into directories passed to :meth:`discover`
    (default: ``<data_dir>/plugins/save_info``); each file is imported and
    scanned for :class:`SaveInfoPlugin` subclasses.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, SaveInfoPlugin] = {}
        # (path, mtime_ns, size, lang) -> result; session-scoped.
        self._cache: dict[tuple[str, int, int, str], SaveInfoResult] = {}

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, user_dirs: list[Path] | None = None) -> None:
        """Load built-in plugins, then any user plugin files."""
        pkg_dir = Path(__file__).parent
        for _, module_name, is_pkg in pkgutil.iter_modules([str(pkg_dir)]):
            if not is_pkg:
                continue
            full_module = f"app.saveinfo.{module_name}.plugin"
            try:
                mod = importlib.import_module(full_module)
                self._register_from_module(mod, full_module)
            except Exception as e:
                logger.warning("Failed to load save-info plugin {}: {}", full_module, e)

        for d in user_dirs or []:
            self._discover_user_dir(Path(d))

    def _discover_user_dir(self, directory: Path) -> None:
        if not directory.is_dir():
            return
        for py in sorted(directory.glob("*.py")):
            name = f"esm_saveinfo_user_{py.stem}"
            try:
                spec = importlib.util.spec_from_file_location(name, py)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                self._register_from_module(mod, str(py))
            except Exception as e:
                logger.warning("Failed to load user save-info plugin {}: {}", py, e)

    def _register_from_module(self, mod: object, origin: str) -> None:
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, SaveInfoPlugin)
                and attr is not SaveInfoPlugin
            ):
                instance = attr()
                self.register(instance)
                logger.info(
                    "Discovered save-info plugin: {} ({})", instance.plugin_id, origin
                )

    def register(self, plugin: SaveInfoPlugin) -> None:
        self._plugins[plugin.plugin_id] = plugin

    def get_all_plugins(self) -> list[SaveInfoPlugin]:
        return list(self._plugins.values())

    # ------------------------------------------------------------------
    # Matching / extraction
    # ------------------------------------------------------------------

    def candidates_for(self, path: Path, platform: str = "") -> list[SaveInfoPlugin]:
        """Plugins whose cheap pre-filter accepts *path* (no file reads)."""
        try:
            size = path.stat().st_size
        except OSError:
            return []
        out: list[SaveInfoPlugin] = []
        for plugin in self._plugins.values():
            try:
                if plugin.matches(path, size, platform):
                    out.append(plugin)
            except Exception as e:
                logger.warning("save-info matches() failed for {}: {}", plugin.plugin_id, e)
        return out

    def extract(
        self,
        path: Path,
        platform: str = "",
        lang: str | None = None,
    ) -> SaveInfoResult | None:
        """Run the first matching plugin on *path*.

        Returns ``None`` when no plugin pre-matches; otherwise the first
        ``matched`` result, or (all plugins declined) the first decline so
        the UI can show its ``reason``.  Results are cached per file
        content-state (path, mtime, size) and language.
        """
        if lang is None:
            from app.i18n import get_current_language
            lang = get_current_language()

        candidates = self.candidates_for(path, platform)
        if not candidates:
            return None

        try:
            stat = path.stat()
            cache_key = (str(path), stat.st_mtime_ns, stat.st_size, lang)
        except OSError:
            cache_key = None
        if cache_key is not None and cache_key in self._cache:
            return self._cache[cache_key]

        first: SaveInfoResult | None = None
        result: SaveInfoResult | None = None
        for plugin in candidates:
            try:
                r = plugin.extract(path, lang)
            except Exception as e:
                logger.warning("save-info extract() failed for {}: {}", plugin.plugin_id, e)
                r = SaveInfoResult(matched=False, error=str(e))
            r.plugin_id = plugin.plugin_id
            r.plugin_name = plugin.display_name
            if first is None:
                first = r
            if r.matched:
                result = r
                break
        if result is None:
            result = first

        if cache_key is not None and result is not None:
            self._cache[cache_key] = result
        return result

    def clear_cache(self) -> None:
        self._cache.clear()


_manager: SaveInfoManager | None = None


def get_save_info_manager() -> SaveInfoManager:
    """Singleton used by the UI; discovers plugins on first access."""
    global _manager
    if _manager is None:
        _manager = SaveInfoManager()
        user_dirs: list[Path] = []
        try:
            from app.config import Config
            user_dirs.append(Config().data_dir / "plugins" / "save_info")
        except Exception as e:  # config unavailable in some test contexts
            logger.debug("save-info: no user plugin dir ({})", e)
        _manager.discover(user_dirs)
    return _manager
