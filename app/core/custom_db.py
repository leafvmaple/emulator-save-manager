"""Custom game DB — CRC-keyed identities for ROMs no DAT will ever match.

Fan translations, ROM hacks and unlicensed originals miss every official
DAT by definition.  This module manages ``games_custom.json`` under the
app data directory (same concept as the proven ``emulator-manager``
custom DB, extended schema): once a CRC is registered, the ROM is fully
identified forever — rename or move it and the identity follows the
content, not the filename.

Curation is bootstrapped, not typed from scratch: ``build_skeleton``
turns the unidentified part of a scan into draft entries, pre-filling
what the Chinese-scene filename convention already encodes
(``中文名 (简/繁) (版本) (组名) (容量)``) plus the embedded cartridge
title as a base-game hint.  The user reviews the JSON, fixes the guesses
and rescans.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from loguru import logger

DB_FILENAME = "games_custom.json"
DB_VERSION = 1


@dataclass
class CustomGame:
    """One curated (or drafted) entry, keyed by content CRC32."""

    name: str = ""
    """Display name, e.g. the Chinese title."""
    base: str = ""
    """Canonical (No-Intro) name of the base game, '' for originals."""
    lang: str = ""
    """Translation language tag, e.g. 'Chs' / 'Cht'."""
    group: str = ""
    """Translation / release group."""
    version: str = ""
    kind: str = ""
    """'translation' | 'original' | 'hack' | '' (unreviewed)."""
    note: str = ""
    header_title: str = ""
    """Embedded cartridge title at draft time — a base-game hint."""


def load_custom_db(path: Path) -> dict[str, CustomGame]:
    """Load ``{CRC32: CustomGame}`` from *path* (missing/invalid → empty).

    Unknown fields are dropped so both this schema and the legacy
    ``emulator-manager`` ``{crc: {name, region}}`` shape load cleanly.
    """
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("Failed to load custom DB {}: {}", path, e)
        return {}

    raw = data.get("entries", data) if isinstance(data, dict) else {}
    known = {f.name for f in fields(CustomGame)}
    result: dict[str, CustomGame] = {}
    for crc, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        result[str(crc).upper()] = CustomGame(
            **{k: str(v) for k, v in entry.items() if k in known})
    return result


def save_custom_db(path: Path, entries: dict[str, CustomGame]) -> None:
    """Persist the DB atomically, entries sorted by CRC for diffability."""
    data = {
        "version": DB_VERSION,
        "entries": {
            crc: {k: v for k, v in asdict(entries[crc]).items() if v}
            for crc in sorted(entries)
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Failed to save custom DB {}: {}", path, e)
        try:
            tmp.unlink()
        except OSError:
            pass


# ----------------------------------------------------------------------
# Filename convention parsing
# ----------------------------------------------------------------------

_TRAILING_TAG_RE = re.compile(r"[(（\[]([^)）\]]*)[)）\]]\s*$")
_SIZE_RE = re.compile(r"\d+(\.\d+)?\s*[Mm][Bb]?$")
_VERSION_RE = re.compile(r"[vV]?\d+(\.\d+)+$|[vV]\d+$")
_VERSION_WORDS = {"修正版", "加強版", "加强版", "改良版", "完全版", "测试版", "測試版"}
_LANG_TAGS = {
    "简": "Chs", "簡": "Chs", "简体": "Chs", "簡體": "Chs", "chs": "Chs",
    "繁": "Cht", "繁体": "Cht", "繁體": "Cht", "cht": "Cht",
}
_REGION_TAGS = {"JP", "JPN", "J", "US", "USA", "U", "EU", "EUR", "E", "KR", "TW", "CN"}


def parse_translation_filename(stem: str) -> dict[str, str]:
    """Split a scene-convention filename into name / lang / group / version.

    Walks parenthesized tags from the *end*: size tags are dropped, then
    group → version/lang/region in the order the convention uses
    (``名字 (lang) (version) (group) (size)``).  An unrecognized tag
    found after the language tag belongs to the title (e.g.
    ``人生游戏(超级大富翁)(繁)``) and parsing stops there.  Heuristic by
    design — the result seeds a skeleton for human review.
    """
    rest = stem.strip()
    result = {"name": "", "lang": "", "group": "", "version": "", "region": ""}

    while True:
        m = _TRAILING_TAG_RE.search(rest)
        if m is None:
            break
        tag = m.group(1).strip()
        low = tag.lower()
        if _SIZE_RE.fullmatch(tag):
            pass
        elif low in _LANG_TAGS and not result["lang"]:
            result["lang"] = _LANG_TAGS[low]
        elif (_VERSION_RE.fullmatch(tag) or tag in _VERSION_WORDS) \
                and not result["version"]:
            result["version"] = tag
        elif tag.upper() in _REGION_TAGS and not result["region"]:
            result["region"] = tag.upper()
        elif not result["lang"] and not result["group"]:
            # Convention puts the group to the right of the language tag;
            # an unknown tag seen before (right of) the lang is the group.
            result["group"] = tag
        else:
            break  # unknown tag left of the lang — part of the title
        rest = rest[:m.start()].strip()

    result["name"] = rest
    return result


# ----------------------------------------------------------------------
# Skeleton generation
# ----------------------------------------------------------------------

def build_skeleton(roms) -> dict[str, CustomGame]:
    """Draft entries for scanned ROMs no DAT / custom entry identified.

    ``roms`` is the scan's ``list[RomFile]``; already-identified files
    and unhashable ones are skipped.
    """
    drafts: dict[str, CustomGame] = {}
    for rom in roms:
        if not rom.crc32 or rom.dat_name or rom.custom_name:
            continue
        parsed = parse_translation_filename(rom.stem)
        drafts[rom.crc32] = CustomGame(
            name=parsed["name"] or rom.stem,
            lang=parsed["lang"],
            group=parsed["group"],
            version=parsed["version"],
            header_title=rom.rom_title,
        )
    return drafts


def merge_skeleton(path: Path, drafts: dict[str, CustomGame]) -> int:
    """Add *drafts* to the DB at *path*, never touching existing entries.

    Returns the number of entries actually added.
    """
    entries = load_custom_db(path)
    added = 0
    for crc, draft in drafts.items():
        if crc not in entries:
            entries[crc] = draft
            added += 1
    if added:
        save_custom_db(path, entries)
        logger.info("Custom DB: {} skeleton entries added to {}", added, path)
    return added
