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
# Multi-container support (zip / 7z / rar, dispatched by magic bytes)
# ----------------------------------------------------------------------

def test_7z_member_crc_and_platform(cfg, tmp_path):
    import io
    import py7zr

    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    content = b"NDS-ROM-CONTENT"
    with py7zr.SevenZipFile(roms_dir / "Electroplankton (Japan).7z", "w") as z:
        z.writef(io.BytesIO(content), "Electroplankton (Japan).nds")

    roms = _library(cfg, roms_dir).scan()
    assert len(roms) == 1
    assert roms[0].crc32 == _crc(content)
    assert roms[0].platform == "NDS"


def test_zip_disguised_as_7z_is_dispatched_by_magic(cfg, tmp_path):
    """Extensions lie (a real NDS set ships RAR named .7z) — the reverse
    case, a zip named .7z, must be identified through its magic bytes."""
    import zipfile

    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    content = b"GBA-ROM"
    with zipfile.ZipFile(roms_dir / "fake.7z", "w") as zf:
        zf.writestr("Mother 3 (Japan).gba", content)

    roms = _library(cfg, roms_dir).scan()
    assert roms[0].crc32 == _crc(content)
    assert roms[0].platform == "GBA"


def test_unknown_archive_magic_is_tolerated_and_not_cached(cfg, tmp_path):
    import json

    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    bad = roms_dir / "garbage.rar"
    bad.write_bytes(b"NOT-AN-ARCHIVE-AT-ALL")

    roms = _library(cfg, roms_dir).scan()
    assert len(roms) == 1
    assert roms[0].crc32 == ""
    assert roms[0].platform == "Unknown"

    # Failures must not become sticky: a transient NAS error would
    # otherwise be cached as a permanent blank until the file changes.
    cache_path = cfg.data_dir / "rom_hash_cache.json"
    if cache_path.exists():
        entries = json.loads(cache_path.read_text(encoding="utf-8"))["entries"]
        assert str(bad) not in entries


def test_cached_failure_entry_is_rehashed(cfg, tmp_path):
    """Failure entries cached by older versions must not stay sticky."""
    import json
    import zipfile

    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    content = b"NES-ROM"
    p = roms_dir / "game.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("game.nes", content)

    stat = p.stat()
    cache_path = cfg.data_dir / "rom_hash_cache.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "version": 1,
        "entries": {str(p): {
            "size": stat.st_size, "mtime": int(stat.st_mtime),
            "crc32": "", "platform": "Unknown",
        }},
    }), encoding="utf-8")

    roms = _library(cfg, roms_dir).scan()
    assert roms[0].crc32 == _crc(content)
    assert roms[0].platform == "NES"


def test_encrypted_7z_needs_configured_password(cfg, tmp_path):
    """Header-encrypted archives list only when a password matches."""
    import io
    import py7zr

    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    content = b"SECRET-NDS-ROM"
    with py7zr.SevenZipFile(roms_dir / "ff3.7z", "w", password="oldmanemu.net",
                            header_encryption=True) as z:
        z.writef(io.BytesIO(content), "Final Fantasy III (Japan).nds")

    # Without the password: degrades to unidentified, never crashes.
    lib = _library(cfg, roms_dir)
    roms = lib.scan()
    assert roms[0].crc32 == ""

    # With it: member CRC resolves normally.
    cfg.set("archive_passwords", ["wrong-guess", "oldmanemu.net"])
    roms = lib.scan()
    assert roms[0].crc32 == _crc(content)
    assert roms[0].platform == "NDS"


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


def test_zip_nes_header_repair_end_to_end(cfg, tmp_path):
    import zipfile

    body = b"PRG" * 1000
    good_crc = _write_nes_dat(cfg, body)
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    zp = roms_dir / "Test Game (USA).zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Test Game (USA).nes", _WRONG_HEADER + body)
        zf.writestr("readme.txt", b"scene notes")
    original_zip = zp.read_bytes()

    lib = _library(cfg, roms_dir)
    roms = lib.scan()

    assert roms[0].crc32 == good_crc
    assert roms[0].repaired is True
    assert roms[0].dat_name == "Test Game (USA)"
    # Whole archive backed up; member repaired; sibling member intact.
    assert (roms_dir / "Test Game (USA).zip.bak").read_bytes() == original_zip
    with zipfile.ZipFile(zp, "r") as zf:
        assert zf.read("Test Game (USA).nes") == _CANON_HEADER + body
        assert zf.read("readme.txt") == b"scene notes"

    # Rescan: central-directory CRC now direct-hits the DAT.
    roms2 = lib.scan()
    assert roms2[0].repaired is False
    assert roms2[0].dat_name == "Test Game (USA)"


def test_derived_version_detection_via_embedded_identity(cfg, tmp_path):
    import zlib as _zlib
    from tests.test_rom_headers import gba_rom

    official = gba_rom(body=b"OFFICIAL-BODY")
    translation = gba_rom(body=b"FAN-TRANSLATED-BODY")
    crc = f"{_zlib.crc32(official) & 0xFFFFFFFF:08X}"

    dat_dir = cfg.data_dir / "dat"
    dat_dir.mkdir(parents=True, exist_ok=True)
    (dat_dir / "gba.dat").write_text(
        '<?xml version="1.0"?>\n<datafile>'
        "<header><name>Nintendo - Game Boy Advance</name></header>"
        f'<game name="Mother 3 (Japan)"><rom name="Mother 3 (Japan).gba" '
        f'crc="{crc}"/></game></datafile>',
        encoding="utf-8",
    )
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    (roms_dir / "Mother 3 (Japan).gba").write_bytes(official)
    (roms_dir / "母亲3汉化版.gba").write_bytes(translation)

    lib = _library(cfg, roms_dir)
    from app.core.rom_library import build_report
    report = build_report(lib.scan(), [], dat_games=lib.dat_games)

    by_name = {r.path.name: r for r in report.roms}
    hack = by_name["母亲3汉化版.gba"]
    assert hack.dat_name == ""
    assert hack.rom_id == "A3UJ"
    assert hack.derived_from == "Mother 3 (Japan)"
    assert by_name["Mother 3 (Japan).gba"].derived_from == ""
    assert report.derived_count == 1


def test_repair_rejects_cross_platform_crc_collision(cfg, tmp_path):
    """Field incident: repaired CRC collided with a GBC entry.

    The repair tries ~1000 headers per file against a 32-bit hash, so a
    raw multi-platform lookup will eventually collide. A candidate whose
    platform is not NES must be rejected and the file left untouched.
    """
    body = b"PRG" * 1000
    colliding_crc = _crc(_CANON_HEADER + body)
    dat_dir = cfg.data_dir / "dat"
    dat_dir.mkdir(parents=True, exist_ok=True)
    # NES DAT supplies the known header but no matching entry…
    (dat_dir / "nes.dat").write_text(
        '<?xml version="1.0"?><datafile>'
        "<header><name>Nintendo - Nintendo Entertainment System (Headered)"
        "</name></header>"
        f'<game name="Unrelated"><rom name="u.nes" crc="12345678" '
        f'header="{_CANON_HEADER.hex().upper()}"/></game></datafile>',
        encoding="utf-8",
    )
    # …while the colliding CRC belongs to a Game Boy game.
    (dat_dir / "gb.dat").write_text(
        '<?xml version="1.0"?><datafile>'
        "<header><name>Nintendo - Game Boy</name></header>"
        f'<game name="Colliding GB Game"><rom name="c.gb" '
        f'crc="{colliding_crc}"/></game></datafile>',
        encoding="utf-8",
    )
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    rom = roms_dir / "game.nes"
    content = _WRONG_HEADER + body
    rom.write_bytes(content)

    roms = _library(cfg, roms_dir).scan()
    assert rom.read_bytes() == content        # untouched
    assert not (roms_dir / "game.nes.bak").exists()
    assert roms[0].repaired is False
    assert roms[0].dat_name == ""


def test_repair_rejects_size_mismatch(cfg, tmp_path):
    body = b"PRG" * 1000
    good_crc = _crc(_CANON_HEADER + body)
    wrong_size = 16 + len(body) + 1
    dat_dir = cfg.data_dir / "dat"
    dat_dir.mkdir(parents=True, exist_ok=True)
    (dat_dir / "nes.dat").write_text(
        '<?xml version="1.0"?><datafile>'
        "<header><name>Nintendo - Nintendo Entertainment System (Headered)"
        "</name></header>"
        f'<game name="Wrong Size Game"><rom name="w.nes" crc="{good_crc}" '
        f'size="{wrong_size}" header="{_CANON_HEADER.hex().upper()}"/>'
        "</game></datafile>",
        encoding="utf-8",
    )
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    rom = roms_dir / "game.nes"
    content = _WRONG_HEADER + body
    rom.write_bytes(content)

    roms = _library(cfg, roms_dir).scan()
    assert rom.read_bytes() == content
    assert roms[0].repaired is False


def test_direct_lookup_rejects_platform_mismatch(cfg, tmp_path):
    """A .gb file whose CRC collides with an NES entry is not 'verified'."""
    content = b"GB-CONTENT"
    dat_dir = cfg.data_dir / "dat"
    dat_dir.mkdir(parents=True, exist_ok=True)
    (dat_dir / "nes.dat").write_text(
        '<?xml version="1.0"?><datafile>'
        "<header><name>Nintendo - Nintendo Entertainment System (Headered)"
        "</name></header>"
        f'<game name="NES Game"><rom name="n.nes" crc="{_crc(content)}"/>'
        "</game></datafile>",
        encoding="utf-8",
    )
    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    (roms_dir / "game.gb").write_bytes(content)

    roms = _library(cfg, roms_dir).scan()
    assert roms[0].platform == "Game Boy"
    assert roms[0].dat_name == ""


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
