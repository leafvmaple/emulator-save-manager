"""Application configuration management."""

import json
import platform
import uuid
from pathlib import Path
from typing import Any, Optional

from loguru import logger


def _default_data_dir() -> Path:
    """Return the default data directory for the application."""
    if platform.system() == "Windows":
        return Path.home() / "Documents" / "EmulatorSaveManager"
    elif platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "EmulatorSaveManager"
    else:
        return Path.home() / ".config" / "EmulatorSaveManager"


_DEFAULT_CONFIG: dict[str, Any] = {
    "language": "zh_CN",
    "theme": "auto",
    "backup_path": "",
    "sync_folder": "",
    "max_backups": 5,
    "machine_id": "",
    "emulators": {},
    "auto_scan_on_start": False,
    "auto_sync_on_start": False,
}


class Config:
    """Singleton application configuration."""

    _instance: Optional["Config"] = None
    _data: dict[str, Any]
    _path: Path
    _data_dir: Path

    def __new__(cls, config_path: Optional[Path] = None) -> "Config":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config_path: Optional[Path] = None) -> None:
        if self._initialized:  # type: ignore[has-type]
            return
        self._initialized = True
        self._data_dir = _default_data_dir()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._path = config_path or (self._data_dir / "config.json")
        self._data = dict(_DEFAULT_CONFIG)
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def backup_path(self) -> Path:
        p = self._data.get("backup_path", "")
        if p:
            return Path(p)
        return self._data_dir / "backups"

    @property
    def sync_folder(self) -> Path:
        p = self._data.get("sync_folder", "")
        return Path(p) if p else Path()

    @property
    def max_backups(self) -> int:
        return int(self._data.get("max_backups", 5))

    @property
    def machine_id(self) -> str:
        return self._data["machine_id"]

    @property
    def language(self) -> str:
        return self._data.get("language", "zh_CN")

    @property
    def theme(self) -> str:
        return self._data.get("theme", "auto")

    @property
    def auto_scan_on_start(self) -> bool:
        return bool(self._data.get("auto_scan_on_start", False))

    @property
    def auto_sync_on_start(self) -> bool:
        return bool(self._data.get("auto_sync_on_start", False))

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()

    def get_emulator_paths(self, emulator_name: str) -> list[str]:
        """Get user-configured custom save scan paths for a specific emulator."""
        emu_cfg = self._data.get("emulators", {})
        return emu_cfg.get(emulator_name, {}).get("custom_paths", [])

    def set_emulator_paths(self, emulator_name: str, paths: list[str]) -> None:
        if "emulators" not in self._data:
            self._data["emulators"] = {}
        if emulator_name not in self._data["emulators"]:
            self._data["emulators"][emulator_name] = {}
        self._data["emulators"][emulator_name]["custom_paths"] = paths
        self._save()

    def get_emulator_install_paths(self, emulator_name: str) -> list[str]:
        """Get user-configured install / data paths for an emulator.

        These are passed to ``detect_installation()`` so plugins can
        locate emulators in non-standard directories.
        """
        emu_cfg = self._data.get("emulators", {})
        return emu_cfg.get(emulator_name, {}).get("install_paths", [])

    def set_emulator_install_paths(self, emulator_name: str, paths: list[str]) -> None:
        if "emulators" not in self._data:
            self._data["emulators"] = {}
        if emulator_name not in self._data["emulators"]:
            self._data["emulators"][emulator_name] = {}
        self._data["emulators"][emulator_name]["install_paths"] = paths
        self._save()

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._data.update(saved)
                logger.info("Configuration loaded from {}", self._path)
            except Exception as e:
                logger.warning("Failed to load config, using defaults: {}", e)
        # Ensure machine_id is set
        if not self._data.get("machine_id"):
            self._data["machine_id"] = uuid.uuid4().hex[:12]
            self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save config: {}", e)

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)."""
        cls._instance = None
