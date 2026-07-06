"""Read-only ROM library scanner (roadmap ROM Stage A).

Scans user-configured ROM directories, hashes each file with CRC32
(the checksum No-Intro DATs key on) behind an mtime/size cache, groups
duplicate dumps by content, and links ROMs to scanned saves by filename
stem — the same key the cartridge-family plugins use as ``game_id``.

Strictly read-only: nothing here moves, renames or deletes a file.
The save-aware rename/normalize work is Stage B (see ROADMAP.md).
"""

from __future__ import annotations

import json
import os
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from loguru import logger

from app.config import Config
from app.models.game_save import GameSave

CACHE_VERSION = 1

#: Lower-case extension -> platform display label.  ``.zip`` is handled
#: separately as a container (the inner ROM's extension decides).
ROM_EXTENSIONS: dict[str, str] = {
    ".nes": "NES",
    ".fds": "NES",
    ".sfc": "SNES",
    ".smc": "SNES",
    ".gb": "Game Boy",
    ".gbc": "Game Boy",
    ".gba": "GBA",
    ".nds": "NDS",
    ".dsi": "NDS",
    ".3ds": "3DS",
    ".cci": "3DS",
    ".cxi": "3DS",
    ".n64": "N64",
    ".z64": "N64",
    ".v64": "N64",
    ".gcm": "GameCube",
    ".gcz": "GameCube",
    ".rvz": "GameCube/Wii",
    ".wbfs": "Wii",
    ".pce": "PC Engine",
    ".sms": "SMS",
    ".gg": "Game Gear",
    ".md": "Mega Drive",
    ".gen": "Mega Drive",
    ".smd": "Mega Drive",
    ".ws": "WonderSwan",
    ".wsc": "WonderSwan",
    ".chd": "Disc",
    ".cso": "Disc",
    ".iso": "Disc",
}

#: Plugins whose ``game_id`` is the ROM filename stem.  Stem-matching
#: (and save-orphaning on rename) is only meaningful for these; the
#: serial/title-id keyed plugins (PCSX2, Citra, Dolphin) are excluded
#: so their saves don't show up as noise in "saves without ROMs".
FILENAME_KEYED_EMULATORS = frozenset({"Snes9x", "Mesen", "melonDS", "RetroArch"})

_HASH_CHUNK = 1024 * 1024


@dataclass
class RomFile:
    """A single ROM file (or single-ROM archive) found in the library."""

    path: Path
    size: int
    modified: datetime
    platform: str
    crc32: str = ""
    """CRC32 of the ROM content (upper-case hex, '' when unhashable).
    For ``.zip`` archives this is the inner ROM's CRC from the central
    directory — the value No-Intro DATs record — not the archive's."""

    @property
    def stem(self) -> str:
        """Filename without extension — what filename-keyed plugins use."""
        return self.path.stem


@dataclass
class RomLibraryReport:
    """Analysis of a scanned library against the current save scan."""

    roms: list[RomFile] = field(default_factory=list)
    duplicate_groups: list[list[RomFile]] = field(default_factory=list)
    """Groups of 2+ ROMs sharing the same content CRC32."""
    matched: dict[Path, GameSave] = field(default_factory=dict)
    """ROM path -> the GameSave whose game_id equals the ROM stem."""
    saves_without_roms: list[GameSave] = field(default_factory=list)
    """Filename-keyed saves with no ROM in the library (moved/renamed?)."""

    @property
    def roms_without_saves(self) -> list[RomFile]:
        return [r for r in self.roms if r.path not in self.matched]

    @property
    def duplicate_count(self) -> int:
        """Number of redundant files (each group counts size-1)."""
        return sum(len(g) - 1 for g in self.duplicate_groups)


class RomLibrary:
    """Scans configured ROM directories with a persistent hash cache."""

    def __init__(self, config: Config) -> None:
        self._cfg = config

    @property
    def rom_dirs(self) -> list[Path]:
        return [Path(p) for p in self._cfg.rom_dirs if p]

    @property
    def _cache_path(self) -> Path:
        return self._cfg.data_dir / "rom_hash_cache.json"

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan(
        self,
        should_cancel: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[RomFile]:
        """Enumerate + hash all ROM files under the configured directories.

        Unchanged files (same size + mtime) reuse their cached CRC32, so
        rescans only hash new or modified ROMs.  *progress* is called as
        ``progress(done, total)`` per file.
        """
        candidates: list[Path] = []
        seen: set[Path] = set()
        for d in self.rom_dirs:
            if not d.is_dir():
                logger.warning("ROM directory does not exist: {}", d)
                continue
            for f in sorted(d.rglob("*")):
                ext = f.suffix.lower()
                if ext not in ROM_EXTENSIONS and ext != ".zip":
                    continue
                if not f.is_file():
                    continue
                try:
                    resolved = f.resolve()
                except OSError:
                    resolved = f
                if resolved in seen:  # overlapping configured dirs
                    continue
                seen.add(resolved)
                candidates.append(f)

        cache = self._load_cache()
        roms: list[RomFile] = []
        dirty = False
        total = len(candidates)
        for i, f in enumerate(candidates, start=1):
            if should_cancel and should_cancel():
                logger.info("ROM scan cancelled ({}/{})", i - 1, total)
                break
            if progress:
                progress(i, total)
            try:
                stat = f.stat()
            except OSError:
                continue

            key = str(f)
            entry = cache.get(key)
            if (
                entry
                and entry.get("size") == stat.st_size
                and entry.get("mtime") == int(stat.st_mtime)
            ):
                crc = entry.get("crc32", "")
                platform = entry.get("platform", "") or "Unknown"
            else:
                crc, platform = self._hash_file(f)
                cache[key] = {
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                    "crc32": crc,
                    "platform": platform,
                }
                dirty = True

            roms.append(RomFile(
                path=f,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
                platform=platform,
                crc32=crc,
            ))

        if dirty:
            self._save_cache(cache)
        logger.info("ROM scan found {} files in {} dirs", len(roms), len(self.rom_dirs))
        return roms

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_file(f: Path) -> tuple[str, str]:
        """Return ``(crc32_hex, platform)`` for a ROM file.

        Zip archives are not decompressed: the central directory already
        stores each member's CRC32, which is exactly what No-Intro DATs
        record.  The largest ROM-extension member represents the archive.
        """
        ext = f.suffix.lower()
        if ext == ".zip":
            try:
                with zipfile.ZipFile(f, "r") as zf:
                    members = [m for m in zf.infolist() if not m.is_dir()]
                rom_members = [
                    m for m in members
                    if Path(m.filename).suffix.lower() in ROM_EXTENSIONS
                ]
                pool = rom_members or members
                if not pool:
                    return "", "Unknown"
                best = max(pool, key=lambda m: m.file_size)
                platform = ROM_EXTENSIONS.get(
                    Path(best.filename).suffix.lower(), "Unknown")
                return f"{best.CRC:08X}", platform
            except (OSError, zipfile.BadZipFile) as e:
                logger.warning("Unreadable zip {}: {}", f, e)
                return "", "Unknown"

        platform = ROM_EXTENSIONS.get(ext, "Unknown")
        crc = 0
        try:
            with open(f, "rb") as fh:
                while chunk := fh.read(_HASH_CHUNK):
                    crc = zlib.crc32(chunk, crc)
        except OSError as e:
            logger.warning("Failed to hash {}: {}", f, e)
            return "", platform
        return f"{crc & 0xFFFFFFFF:08X}", platform

    def _load_cache(self) -> dict[str, dict]:
        path = self._cache_path
        if not path.is_file():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != CACHE_VERSION:
                return {}
            entries = data.get("entries", {})
            if isinstance(entries, dict):
                return entries
        except (OSError, ValueError) as e:
            logger.warning("Could not read ROM hash cache {}: {}", path, e)
        return {}

    def _save_cache(self, entries: dict[str, dict]) -> None:
        path = self._cache_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": CACHE_VERSION, "entries": entries}, f,
                          ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError as e:
            logger.warning("Could not write ROM hash cache {}: {}", path, e)


# ----------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------

def build_report(
    roms: list[RomFile],
    saves: list[GameSave],
) -> RomLibraryReport:
    """Cross-reference a ROM scan with the current save scan.

    Duplicates are grouped by content CRC32.  Save linking matches the
    ROM filename stem against ``game_id`` (case-insensitive) for the
    filename-keyed emulators only.
    """
    report = RomLibraryReport(roms=list(roms))

    by_crc: dict[str, list[RomFile]] = {}
    for rom in roms:
        if rom.crc32:
            by_crc.setdefault(rom.crc32, []).append(rom)
    report.duplicate_groups = [
        group for group in by_crc.values() if len(group) > 1
    ]

    by_stem: dict[str, list[RomFile]] = {}
    for rom in roms:
        by_stem.setdefault(rom.stem.lower(), []).append(rom)

    linked_keys: set[str] = set()
    for save in saves:
        if save.emulator not in FILENAME_KEYED_EMULATORS:
            continue
        for rom in by_stem.get(save.game_id.lower(), []):
            report.matched[rom.path] = save
            linked_keys.add(save.unique_key)

    report.saves_without_roms = [
        s for s in saves
        if s.emulator in FILENAME_KEYED_EMULATORS
        and s.unique_key not in linked_keys
    ]
    return report
