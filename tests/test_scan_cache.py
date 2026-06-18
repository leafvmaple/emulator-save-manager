"""Scan cache — round-trips emulators + saves so the UI isn't empty on launch."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.core import scan_cache
from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave, SaveFile, SaveType


def _sample():
    emulators = [
        EmulatorInfo(
            name="PCSX2",
            install_path=Path("C:/Games/PCSX2"),
            data_path=Path("C:/Users/x/Documents/PCSX2"),
            version="2.0",
            supported_platforms=["PS2", "PS1"],
            is_portable=False,
        )
    ]
    saves = [
        GameSave(
            emulator="PCSX2",
            game_name="Super Robot Taisen OG",
            game_id="Mcd001",
            platform="PS2",
            crc32="083F0E03",
            data_path=Path("C:/Users/x/Documents/PCSX2"),
            save_files=[
                SaveFile(
                    path=Path("C:/Users/x/Documents/PCSX2/memcards/Mcd001.ps2"),
                    save_type=SaveType.MEMCARD,
                    size=8650752,
                    modified=datetime(2025, 9, 12, 13, 11, 5),
                )
            ],
        )
    ]
    return emulators, saves


def test_round_trip(tmp_path):
    emulators, saves = _sample()
    cache = tmp_path / "scan_cache.json"
    scan_cache.save_scan(cache, emulators, saves)
    assert cache.is_file()

    emus2, saves2 = scan_cache.load_scan(cache)
    assert [e.name for e in emus2] == ["PCSX2"]
    assert emus2[0].data_path == Path("C:/Users/x/Documents/PCSX2")
    assert emus2[0].supported_platforms == ["PS2", "PS1"]

    assert len(saves2) == 1
    s = saves2[0]
    assert s.game_id == "Mcd001" and s.crc32 == "083F0E03" and s.platform == "PS2"
    sf = s.save_files[0]
    assert sf.save_type == SaveType.MEMCARD
    assert sf.size == 8650752
    assert sf.modified == datetime(2025, 9, 12, 13, 11, 5)
    assert sf.path.name == "Mcd001.ps2"


def test_missing_file_returns_empty(tmp_path):
    emus, saves = scan_cache.load_scan(tmp_path / "nope.json")
    assert emus == [] and saves == []


def test_corrupt_file_returns_empty(tmp_path):
    bad = tmp_path / "scan_cache.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    assert scan_cache.load_scan(bad) == ([], [])


def test_version_mismatch_ignored(tmp_path):
    cache = tmp_path / "scan_cache.json"
    emulators, saves = _sample()
    scan_cache.save_scan(cache, emulators, saves)
    import json
    data = json.loads(cache.read_text(encoding="utf-8"))
    data["version"] = 999
    cache.write_text(json.dumps(data), encoding="utf-8")
    assert scan_cache.load_scan(cache) == ([], [])


def test_save_is_atomic_no_tmp_left(tmp_path):
    cache = tmp_path / "scan_cache.json"
    scan_cache.save_scan(cache, *_sample())
    assert not (tmp_path / "scan_cache.json.tmp").exists()
