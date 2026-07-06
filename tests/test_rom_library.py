"""ROM library scanner (Stage A) — hashing, caching, duplicate/link analysis."""

from __future__ import annotations

import json
import zipfile
import zlib

from app.core.rom_library import RomLibrary, build_report
from app.models.game_save import GameSave


def _crc(data: bytes) -> str:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"


def _library(cfg, *dirs) -> RomLibrary:
    cfg.set("rom_dirs", [str(d) for d in dirs])
    return RomLibrary(cfg)


def test_scan_finds_roms_and_skips_unknown_extensions(cfg, tmp_path):
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    (roms_dir / "Chrono Trigger (USA).sfc").write_bytes(b"SNES-ROM")
    (roms_dir / "notes.txt").write_bytes(b"not a rom")
    (roms_dir / "nested").mkdir()
    (roms_dir / "nested" / "Mother 3 (Japan).gba").write_bytes(b"GBA-ROM")

    roms = _library(cfg, roms_dir).scan()
    assert sorted(r.path.name for r in roms) == [
        "Chrono Trigger (USA).sfc", "Mother 3 (Japan).gba",
    ]
    by_name = {r.path.name: r for r in roms}
    assert by_name["Chrono Trigger (USA).sfc"].platform == "SNES"
    assert by_name["Chrono Trigger (USA).sfc"].crc32 == _crc(b"SNES-ROM")
    assert by_name["Mother 3 (Japan).gba"].platform == "GBA"


def test_zip_uses_inner_member_crc_and_platform(cfg, tmp_path):
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    inner = b"NES-ROM-CONTENT"
    with zipfile.ZipFile(roms_dir / "Kirby (USA).zip", "w") as zf:
        zf.writestr("Kirby's Adventure (USA).nes", inner)
        zf.writestr("readme.txt", b"scene notes")

    roms = _library(cfg, roms_dir).scan()
    assert len(roms) == 1
    # CRC comes from the .nes member (what No-Intro records), not the zip.
    assert roms[0].crc32 == _crc(inner)
    assert roms[0].platform == "NES"
    assert roms[0].stem == "Kirby (USA)"


def test_rescan_reuses_cache_for_unchanged_files(cfg, tmp_path):
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    rom = roms_dir / "game.nes"
    rom.write_bytes(b"DATA")

    lib = _library(cfg, roms_dir)
    lib.scan()

    # Poison the cached CRC while keeping size/mtime valid: a cache hit
    # returns the sentinel, a rehash would return the real CRC.
    cache_path = cfg.data_dir / "rom_hash_cache.json"
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    entry = data["entries"][str(rom)]
    entry["crc32"] = "SENTINEL"
    cache_path.write_text(json.dumps(data), encoding="utf-8")

    roms = lib.scan()
    assert roms[0].crc32 == "SENTINEL"


def test_modified_file_is_rehashed(cfg, tmp_path):
    import os
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    rom = roms_dir / "game.nes"
    rom.write_bytes(b"OLD")
    lib = _library(cfg, roms_dir)
    lib.scan()

    rom.write_bytes(b"NEW-LONGER")  # size change invalidates the entry
    os.utime(rom)
    roms = lib.scan()
    assert roms[0].crc32 == _crc(b"NEW-LONGER")


def test_overlapping_dirs_deduplicate(cfg, tmp_path):
    roms_dir = tmp_path / "roms"
    sub = roms_dir / "sub"
    sub.mkdir(parents=True)
    (sub / "game.gb").write_bytes(b"GB")

    roms = _library(cfg, roms_dir, sub).scan()
    assert len(roms) == 1


def test_scan_cancellation_stops_early(cfg, tmp_path):
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    for i in range(5):
        (roms_dir / f"game{i}.nes").write_bytes(bytes([i]))

    calls = iter([False, False, True, True, True])
    roms = _library(cfg, roms_dir).scan(should_cancel=lambda: next(calls))
    assert len(roms) == 2


def test_report_groups_duplicates_by_content(cfg, tmp_path):
    d1 = tmp_path / "roms" / "a"
    d2 = tmp_path / "roms" / "b"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    (d1 / "Zelda (USA).sfc").write_bytes(b"SAME")
    (d2 / "zelda-backup.sfc").write_bytes(b"SAME")
    (d1 / "Metroid (USA).sfc").write_bytes(b"OTHER")

    roms = _library(cfg, tmp_path / "roms").scan()
    report = build_report(roms, [])
    assert len(report.duplicate_groups) == 1
    assert {r.path.name for r in report.duplicate_groups[0]} == {
        "Zelda (USA).sfc", "zelda-backup.sfc",
    }
    assert report.duplicate_count == 1


def test_report_links_roms_to_filename_keyed_saves(cfg, tmp_path):
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    (roms_dir / "Chrono Trigger (USA).sfc").write_bytes(b"CT")
    (roms_dir / "Orphan Game.sfc").write_bytes(b"ORPHAN")
    roms = _library(cfg, roms_dir).scan()

    linked = GameSave(emulator="Snes9x", game_name="Chrono Trigger",
                      game_id="chrono trigger (usa)", platform="SNES")
    orphan_save = GameSave(emulator="Snes9x", game_name="Renamed Away",
                           game_id="Old Rom Name", platform="SNES")
    serial_keyed = GameSave(emulator="PCSX2", game_name="FFX",
                            game_id="SLUS-20312", platform="PS2")

    report = build_report(roms, [linked, orphan_save, serial_keyed])

    # Stem matching is case-insensitive.
    assert report.matched[roms_dir / "Chrono Trigger (USA).sfc"] is linked
    assert [r.path.name for r in report.roms_without_saves] == ["Orphan Game.sfc"]
    # Serial-keyed emulators never count as "save without ROM" noise.
    assert report.saves_without_roms == [orphan_save]


def test_missing_rom_dir_is_tolerated(cfg, tmp_path):
    roms = _library(cfg, tmp_path / "does-not-exist").scan()
    assert roms == []


# ----------------------------------------------------------------------
# DAT verification + convergent NES header repair
# ----------------------------------------------------------------------

_CANON_HEADER = bytes.fromhex("4E45531A020101000000000000000000")
_WRONG_HEADER = bytes.fromhex("4E45531A08010100000000000000000A")


def _write_nes_dat(cfg, body: bytes) -> str:
    """Register `_CANON_HEADER + body` in a DAT under the default dat_dir."""
    good_crc = _crc(_CANON_HEADER + body)
    dat_dir = cfg.data_dir / "dat"
    dat_dir.mkdir(parents=True, exist_ok=True)
    (dat_dir / "nes.dat").write_text(
        '<?xml version="1.0"?>\n<datafile>'
        "<header><name>Nintendo - Nintendo Entertainment System (Headered)"
        "</name></header>"
        f'<game name="Test Game (USA)"><rom name="Test Game (USA).nes" '
        f'crc="{good_crc}" header="{_CANON_HEADER.hex().upper()}"/></game>'
        "</datafile>",
        encoding="utf-8",
    )
    return good_crc


def test_dat_verification_marks_canonical_dump(cfg, tmp_path):
    body = b"PRG" * 1000
    good_crc = _write_nes_dat(cfg, body)
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    (roms_dir / "Test Game (USA).nes").write_bytes(_CANON_HEADER + body)

    lib = _library(cfg, roms_dir)
    roms = lib.scan()
    assert lib.dat_games == 1
    assert roms[0].crc32 == good_crc
    assert roms[0].dat_name == "Test Game (USA)"
    assert roms[0].repaired is False

    from app.core.rom_library import build_report
    report = build_report(roms, [], dat_games=lib.dat_games)
    assert report.verified_count == 1
    assert report.repaired_count == 0


def test_nes_header_repair_end_to_end(cfg, tmp_path):
    body = b"PRG" * 1000
    good_crc = _write_nes_dat(cfg, body)
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    rom = roms_dir / "test game.nes"
    original = _WRONG_HEADER + body
    rom.write_bytes(original)

    lib = _library(cfg, roms_dir)
    roms = lib.scan()

    # File converged to the canonical dump, byte for byte.
    assert rom.read_bytes() == _CANON_HEADER + body
    assert roms[0].crc32 == good_crc
    assert roms[0].repaired is True
    assert roms[0].dat_name == "Test Game (USA)"
    # Pristine original preserved next to it.
    bak = roms_dir / "test game.nes.bak"
    assert bak.read_bytes() == original

    # Rescan: direct DAT hit from cache, no second repair, .bak untouched.
    roms2 = lib.scan()
    assert roms2[0].repaired is False
    assert roms2[0].dat_name == "Test Game (USA)"
    assert bak.read_bytes() == original
    # The .bak sibling is never picked up as a library entry.
    assert len(roms2) == 1


def test_repair_never_overwrites_existing_bak(cfg, tmp_path):
    body = b"PRG" * 1000
    _write_nes_dat(cfg, body)
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    rom = roms_dir / "game.nes"
    rom.write_bytes(_WRONG_HEADER + body)
    sentinel = b"PRISTINE-FIRST-BACKUP"
    (roms_dir / "game.nes.bak").write_bytes(sentinel)

    _library(cfg, roms_dir).scan()
    # First backup wins — repair must not clobber it.
    assert (roms_dir / "game.nes.bak").read_bytes() == sentinel
    assert rom.read_bytes() == _CANON_HEADER + body


def test_unmatched_nes_file_is_left_untouched(cfg, tmp_path):
    _write_nes_dat(cfg, b"PRG" * 1000)
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    rom = roms_dir / "homebrew.nes"
    content = _WRONG_HEADER + b"COMPLETELY-DIFFERENT-BODY"
    rom.write_bytes(content)

    roms = _library(cfg, roms_dir).scan()
    assert rom.read_bytes() == content
    assert not (roms_dir / "homebrew.nes.bak").exists()
    assert roms[0].dat_name == ""
    assert roms[0].repaired is False
