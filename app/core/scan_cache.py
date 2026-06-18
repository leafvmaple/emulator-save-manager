"""Persist the last scan result so the UI isn't empty on a fresh launch.

This is a best-effort snapshot: the stored paths / sizes / timestamps are from
when the scan ran and may be stale (files moved or changed since).  It exists
purely so the scan / backup / home views show the previous session's findings
immediately; a re-scan overwrites it with fresh data.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave, SaveFile, SaveType

CACHE_VERSION = 1


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------

def _emu_to_dict(e: EmulatorInfo) -> dict:
    return {
        "name": e.name,
        "install_path": str(e.install_path),
        "data_path": str(e.data_path),
        "version": e.version,
        "supported_platforms": list(e.supported_platforms),
        "is_portable": e.is_portable,
    }


def _emu_from_dict(d: dict) -> EmulatorInfo:
    return EmulatorInfo(
        name=d["name"],
        install_path=Path(d.get("install_path", "")),
        data_path=Path(d.get("data_path", "")),
        version=d.get("version", ""),
        supported_platforms=list(d.get("supported_platforms", [])),
        is_portable=bool(d.get("is_portable", False)),
    )


def _file_to_dict(f: SaveFile) -> dict:
    return {
        "path": str(f.path),
        "save_type": f.save_type.value,
        "size": f.size,
        "modified": f.modified.isoformat() if f.modified else None,
    }


def _file_from_dict(d: dict) -> SaveFile:
    mod = d.get("modified")
    return SaveFile(
        path=Path(d["path"]),
        save_type=SaveType(d["save_type"]),
        size=int(d.get("size", 0)),
        modified=datetime.fromisoformat(mod) if mod else datetime.now(),
    )


def _save_to_dict(s: GameSave) -> dict:
    return {
        "emulator": s.emulator,
        "game_name": s.game_name,
        "game_id": s.game_id,
        "platform": s.platform,
        "crc32": s.crc32,
        "data_path": str(s.data_path) if s.data_path else None,
        "save_files": [_file_to_dict(f) for f in s.save_files],
    }


def _save_from_dict(d: dict) -> GameSave:
    return GameSave(
        emulator=d["emulator"],
        game_name=d.get("game_name", ""),
        game_id=d.get("game_id", ""),
        platform=d.get("platform", ""),
        crc32=d.get("crc32", ""),
        data_path=Path(d["data_path"]) if d.get("data_path") else None,
        save_files=[_file_from_dict(f) for f in d.get("save_files", [])],
    )


# ----------------------------------------------------------------------
# Disk I/O
# ----------------------------------------------------------------------

def save_scan(
    path: Path,
    emulators: list[EmulatorInfo],
    saves: list[GameSave],
) -> None:
    """Atomically write the scan snapshot to *path* (errors are logged, not raised)."""
    data = {
        "version": CACHE_VERSION,
        "emulators": [_emu_to_dict(e) for e in emulators],
        "saves": [_save_to_dict(s) for s in saves],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("Could not write scan cache {}: {}", path, e)


def load_scan(path: Path) -> tuple[list[EmulatorInfo], list[GameSave]]:
    """Load a scan snapshot from *path*.  Returns empty lists if absent/invalid."""
    if not path.is_file():
        return [], []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != CACHE_VERSION:
            return [], []
        emulators = [_emu_from_dict(d) for d in data.get("emulators", [])]
        saves = [_save_from_dict(d) for d in data.get("saves", [])]
        return emulators, saves
    except (OSError, ValueError, KeyError) as e:
        logger.warning("Could not read scan cache {}: {}", path, e)
        return [], []
