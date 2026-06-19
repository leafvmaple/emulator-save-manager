"""Resolve save display names through ROM library indexes."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import Config
from app.data.rom_library import RomLibrary
from app.models.game_save import GameSave
from app.models.rom_entry import RomEntry


_COPY_SUFFIX_RE = re.compile(
    r"""
    (?:
        \s*-\s*(?:copy|副本)(?:\s*\(\d+\))?
      | \s*\((?:copy|副本)(?:\s*\d+)?\)
    )$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_ROM_EXTS = {
    ".nds", ".dsi", ".ids", ".srl",
    ".gba", ".agb", ".mb",
    ".nes", ".fds", ".unf",
    ".sfc", ".smc",
    ".gb", ".gbc",
    ".pce", ".sms", ".gg", ".sg",
    ".ws", ".wsc",
    ".3ds", ".cci", ".cxi",
    ".zip", ".7z",
}

_PLATFORM_ALIASES = {
    "nds": "nds",
    "nintendo ds": "nds",
    "melonds": "nds",
    "gba": "gba",
    "game boy advance": "gba",
    "nes": "nes",
    "nintendo entertainment system": "nes",
    "snes": "snes",
    "super nintendo": "snes",
    "super nintendo entertainment system": "snes",
    "snes9x": "snes",
    "game boy": "gb",
    "gb": "gb",
    "gbc": "gb",
    "pc engine": "pce",
    "pce": "pce",
    "sms": "sms",
    "master system": "sms",
    "wonderswan": "ws",
    "3ds": "n3ds",
    "n3ds": "n3ds",
    "citra": "n3ds",
}


@dataclass(frozen=True)
class RomIdentity:
    """ROM metadata matched to a save."""

    display_name: str
    rom_game_id: str
    platform: str
    rom_path: Path | None = None
    source: Path | None = None


class RomIdentityResolver:
    """Resolve save display names from current and legacy ROM libraries."""

    def __init__(self, config: Config, extra_libraries: list[Path] | None = None) -> None:
        self._config = config
        self._library_paths = self._build_library_paths(extra_libraries or [])
        self._loaded = False
        self._by_id: dict[tuple[str, str], list[tuple[RomEntry, Path]]] = {}
        self._by_stem: dict[tuple[str, str], list[tuple[RomEntry, Path]]] = {}

    def resolve_save(self, save: GameSave) -> RomIdentity | None:
        """Return a matching ROM identity for *save*, if one is known."""
        self._ensure_loaded()
        platform = _platform_key(save.platform)
        names = _candidate_names(save)

        for name in names:
            entry = self._unique_lookup(self._by_id, platform, name)
            if entry is not None:
                return _identity_from_entry(entry)

        for name in names:
            normalized = normalize_rom_stem(name)
            entry = self._unique_lookup(self._by_stem, platform, normalized)
            if entry is not None:
                return _identity_from_entry(entry)

        return None

    def apply_to_saves(self, saves: list[GameSave]) -> None:
        """Update ``game_name`` for saves whose ROM identity is known."""
        for save in saves:
            identity = self.resolve_save(save)
            if identity and identity.display_name:
                save.game_name = identity.display_name

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        for path in self._library_paths:
            library = RomLibrary(path.parent, path=path)
            library.load()
            for entry in library.all_entries():
                self._add_entry(entry, path)

    def _add_entry(self, entry: RomEntry, source: Path) -> None:
        platform = _platform_key(entry.platform)
        ids = {
            entry.game_id,
            entry.hash_crc32,
        }
        if entry.rom_info is not None:
            ids.add(entry.rom_info.title_id)
            ids.update(entry.rom_info.dat_crc32)

        for value in ids:
            key = str(value).strip().lower()
            if not key:
                continue
            self._by_id.setdefault((platform, key), []).append((entry, source))
            self._by_id.setdefault(("", key), []).append((entry, source))

        stem = normalize_rom_stem(entry.file_stem)
        if stem:
            key = stem.lower()
            self._by_stem.setdefault((platform, key), []).append((entry, source))
            self._by_stem.setdefault(("", key), []).append((entry, source))

    def _unique_lookup(
        self,
        index: dict[tuple[str, str], list[tuple[RomEntry, Path]]],
        platform: str,
        value: str,
    ) -> tuple[RomEntry, Path] | None:
        key = value.strip().lower()
        if not key:
            return None

        candidates = [platform] if platform else []
        candidates.append("")
        for plat in candidates:
            match = _unique(index.get((plat, key), []))
            if match is not None:
                return match
        return None

    def _build_library_paths(self, extra_libraries: list[Path]) -> list[Path]:
        paths = [self._config.data_dir / "rom_library.json"]

        for env_name in ("EMULATOR_SAVE_MANAGER_ROM_LIBRARY", "EMULATOR_MANAGER_ROM_LIBRARY"):
            raw = os.environ.get(env_name, "").strip()
            if raw:
                paths.append(Path(raw))

        legacy_data = os.environ.get("EMULATOR_MANAGER_DATA_DIR", "").strip()
        if legacy_data:
            paths.append(Path(legacy_data) / "rom_library.json")
        else:
            try:
                from app.core.path_resolver import get_documents_dir

                paths.append(get_documents_dir() / "EmulatorManager" / "rom_library.json")
            except Exception:
                paths.append(Path.home() / "Documents" / "EmulatorManager" / "rom_library.json")

        paths.extend(extra_libraries)

        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path)
            if key not in seen and path.exists():
                seen.add(key)
                unique.append(path)
        return unique


def normalize_rom_stem(name: str) -> str:
    """Normalize common duplicate suffixes without changing real title text."""
    text = str(name).strip()
    path = Path(text)
    current = path.stem if path.suffix.lower() in _ROM_EXTS else text

    previous = ""
    while current and current != previous:
        previous = current
        current = _COPY_SUFFIX_RE.sub("", current).strip()
    return current or text


def _candidate_names(save: GameSave) -> list[str]:
    values = [save.game_id, save.game_name]
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = str(value).strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _identity_from_entry(item: tuple[RomEntry, Path]) -> RomIdentity:
    entry, source = item
    return RomIdentity(
        display_name=entry.display_name,
        rom_game_id=entry.game_id,
        platform=_platform_key(entry.platform),
        rom_path=entry.path,
        source=source,
    )


def _platform_key(platform: str) -> str:
    key = str(platform).strip().lower()
    return _PLATFORM_ALIASES.get(key, key)


def _unique(items: list[tuple[RomEntry, Path]]) -> tuple[RomEntry, Path] | None:
    if not items:
        return None
    ids = {(item.game_id, item.rom_path) for item, _ in items}
    if len(ids) == 1:
        return items[0]
    return None
