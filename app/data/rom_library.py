"""JSON-backed ROM index."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.models.rom_entry import RomEntry, RomInfo


def _rom_info_from_dict(data: Any) -> RomInfo | None:
    if not isinstance(data, dict):
        return None
    fields = set(RomInfo.__dataclass_fields__)
    return RomInfo(**{k: v for k, v in data.items() if k in fields})


def _entry_from_dict(data: dict[str, Any]) -> RomEntry:
    """Load both current and legacy EmulatorManager ROM entry shapes."""
    raw = dict(data)
    if "rom_path" not in raw and "path" in raw:
        raw["rom_path"] = raw.pop("path")
    raw["rom_info"] = _rom_info_from_dict(raw.get("rom_info"))
    fields = set(RomEntry.__dataclass_fields__)
    return RomEntry(**{k: v for k, v in raw.items() if k in fields})


def _entry_to_dict(entry: RomEntry) -> dict[str, Any]:
    data = asdict(entry)
    if entry.rom_info is None:
        data.pop("rom_info", None)
    return data


class RomLibrary:
    """Manage ``rom_library.json`` under the app data directory."""

    def __init__(self, data_dir: Path, path: Path | None = None) -> None:
        self._data_dir = data_dir
        self._path = path or (data_dir / "rom_library.json")
        self._version = 1
        self._roms: dict[str, RomEntry] = {}

    @property
    def path(self) -> Path:
        return self._path

    @property
    def count(self) -> int:
        return len(self._roms)

    @staticmethod
    def make_key(platform: str, game_id: str) -> str:
        return f"{platform}:{game_id}"

    def load(self) -> None:
        self._roms.clear()
        if not self._path.exists():
            return

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load ROM library {}: {}", self._path, e)
            return

        self._version = int(data.get("version", 1))
        raw_roms = data.get("roms", {})
        if not isinstance(raw_roms, dict):
            return

        for key, raw in raw_roms.items():
            if not isinstance(raw, dict):
                continue
            try:
                entry = _entry_from_dict(raw)
                self._roms[key] = entry
            except TypeError as e:
                logger.debug("Skipping malformed ROM entry {}: {}", key, e)

    def save(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self._version,
            "roms": {
                self.make_key(e.platform, e.game_id): _entry_to_dict(e)
                for e in self._roms.values()
            },
        }
        tmp = self._path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._path)
        except OSError as e:
            logger.warning("Failed to save ROM library {}: {}", self._path, e)
            tmp.unlink(missing_ok=True)

    def clear(self) -> None:
        self._roms.clear()

    def add(self, entry: RomEntry) -> None:
        if not entry.added_at:
            entry.added_at = datetime.now(timezone.utc).isoformat()
        self._roms[self.make_key(entry.platform, entry.game_id)] = entry

    def get(self, platform: str, game_id: str) -> RomEntry | None:
        return self._roms.get(self.make_key(platform, game_id))

    def remove(self, platform: str, game_id: str) -> None:
        self._roms.pop(self.make_key(platform, game_id), None)

    def all_entries(self) -> list[RomEntry]:
        return list(self._roms.values())

    def entries_by_platform(self, platform: str) -> list[RomEntry]:
        return [e for e in self._roms.values() if e.platform == platform]

    def find_by_hash(self, crc32: str) -> list[RomEntry]:
        target = crc32.upper()
        return [e for e in self._roms.values() if e.hash_crc32.upper() == target]
