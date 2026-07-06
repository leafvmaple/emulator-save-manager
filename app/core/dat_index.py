"""No-Intro DAT index — canonical-name lookup and known-header table.

Parses the No-Intro XML DAT files the user drops into the DAT directory
(config ``dat_dir``, default ``<data_dir>/dat``) into a CRC32 →
canonical-name index.  Headered NES DATs additionally carry each dump's
16-byte iNES header in the ``header`` attribute; those are collected so
the ROM scanner can re-try a CRC miss with every known header and repair
a non-standard header in place (see :mod:`app.core.rom_library`).

Ported from the proven ``emulator-manager`` ``tools/import_dat.py``;
kept as a runtime parser (with an mtime cache) instead of a build step
so dropping in a new DAT is all the user has to do.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

_NES_HEADER_LEN = 16

#: DAT filename keyword → platform label (ROM_EXTENSIONS vocabulary).
#: Order matters: more specific names first (SNES before NES, GBA/GBC
#: before GB, 3DS before DS).
_DAT_PLATFORM_MAP: list[tuple[str, str]] = [
    ("Super Nintendo Entertainment System", "SNES"),
    ("Nintendo Entertainment System", "NES"),
    ("Famicom", "NES"),
    ("Game Boy Advance", "GBA"),
    ("Game Boy Color", "Game Boy"),
    ("Game Boy", "Game Boy"),
    ("Nintendo 3DS", "3DS"),
    ("Nintendo DSi", "NDS"),
    ("Nintendo DS", "NDS"),
    ("Nintendo 64", "N64"),
    ("GameCube", "GameCube"),
    ("Mega Drive", "Mega Drive"),
    ("Genesis", "Mega Drive"),
    ("Master System", "SMS"),
    ("Game Gear", "Game Gear"),
    ("PC Engine", "PC Engine"),
    ("TurboGrafx", "PC Engine"),
    ("WonderSwan", "WonderSwan"),
]


def guess_platform(dat_name: str) -> str:
    """Guess the platform label from a No-Intro DAT name ('' if unknown)."""
    for keyword, platform in _DAT_PLATFORM_MAP:
        if keyword in dat_name:
            return platform
    return ""


@dataclass
class DatGame:
    """One dump entry from a DAT."""

    name: str
    """Full No-Intro name including region/revision tags."""
    platform: str = ""
    size: int = 0
    """Dump size in bytes (0 when the DAT omits it). CRC32 is only 32
    bits — size is the second factor that makes a match trustworthy."""


@dataclass
class DatIndex:
    """Merged lookup over every loaded DAT file."""

    by_crc: dict[str, DatGame] = field(default_factory=dict)
    nes_headers: list[bytes] = field(default_factory=list)
    """Unique 16-byte iNES headers seen in headered NES DATs."""
    sources: list[str] = field(default_factory=list)

    @property
    def game_count(self) -> int:
        return len(self.by_crc)

    def lookup(self, crc32: str) -> DatGame | None:
        if not crc32:
            return None
        return self.by_crc.get(crc32.upper())


def parse_dat(dat_path: Path) -> tuple[str, dict[str, tuple[str, int]], list[bytes]]:
    """Parse one No-Intro DAT → ``(platform, {crc: (name, size)}, nes_headers)``.

    ``[BIOS]`` entries are skipped.  Header attributes are only harvested
    from NES DATs (they are iNES container headers; other platforms'
    would be meaningless for repair).
    """
    tree = ET.parse(dat_path)
    root = tree.getroot()

    platform = guess_platform(dat_path.stem)
    header_el = root.find("header/name")
    if header_el is not None and header_el.text:
        platform = guess_platform(header_el.text) or platform

    entries: dict[str, tuple[str, int]] = {}
    headers: set[bytes] = set()

    for game_el in root.iter("game"):
        game_name = game_el.get("name", "")
        if not game_name or game_name.startswith("[BIOS]"):
            continue
        for rom_el in game_el.iter("rom"):
            crc32 = (rom_el.get("crc") or "").strip().upper()
            try:
                size = int(rom_el.get("size") or 0)
            except ValueError:
                size = 0
            if crc32 and crc32 not in entries:
                entries[crc32] = (game_name, size)
            if platform == "NES":
                raw_header = (rom_el.get("header") or "").replace(" ", "")
                if raw_header:
                    try:
                        hdr = bytes.fromhex(raw_header)
                    except ValueError:
                        continue
                    if len(hdr) == _NES_HEADER_LEN:
                        headers.add(hdr)

    return platform, entries, sorted(headers)


# Per-file parse cache keyed by (path, mtime, size) — DATs are multi-MB
# XML, no need to re-parse them on every library rescan in a session.
_parse_cache: dict[
    str, tuple[float, int, tuple[str, dict[str, tuple[str, int]], list[bytes]]]
] = {}


def load_dat_index(dat_dir: Path) -> DatIndex:
    """Load and merge every ``*.dat`` under *dat_dir* (missing dir → empty)."""
    index = DatIndex()
    if not dat_dir.is_dir():
        return index

    for dat_path in sorted(dat_dir.glob("*.dat")):
        try:
            stat = dat_path.stat()
        except OSError:
            continue
        key = str(dat_path)
        cached = _parse_cache.get(key)
        if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            platform, entries, headers = cached[2]
        else:
            try:
                platform, entries, headers = parse_dat(dat_path)
            except (ET.ParseError, OSError) as e:
                logger.warning("Skipping unreadable DAT {}: {}", dat_path.name, e)
                continue
            _parse_cache[key] = (stat.st_mtime, stat.st_size, (platform, entries, headers))
            logger.info(
                "Parsed DAT {}: {} entries, {} headers ({})",
                dat_path.name, len(entries), len(headers), platform or "unknown",
            )

        index.sources.append(dat_path.name)
        for crc, (name, size) in entries.items():
            index.by_crc.setdefault(
                crc, DatGame(name=name, platform=platform, size=size))
        for hdr in headers:
            if hdr not in index.nes_headers:
                index.nes_headers.append(hdr)

    if index.sources:
        logger.info(
            "DAT index: {} games, {} NES headers from {} file(s)",
            index.game_count, len(index.nes_headers), len(index.sources),
        )
    return index
