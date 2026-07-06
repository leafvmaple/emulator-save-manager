"""No-Intro DAT parsing and the merged CRC index."""

from __future__ import annotations

from app.core.dat_index import guess_platform, load_dat_index, parse_dat

_NES_DAT = """<?xml version="1.0"?>
<datafile>
    <header>
        <name>Nintendo - Nintendo Entertainment System (Headered)</name>
    </header>
    <game name="Super Mario Bros. (World)" id="1">
        <rom name="Super Mario Bros. (World).nes" size="40976"
             crc="AAAA1111"
             header="4E 45 53 1A 02 01 01 00 00 00 00 00 00 00 00 00"/>
    </game>
    <game name="[BIOS] FDS BIOS (Japan)" id="2">
        <rom name="bios.nes" crc="BBBB2222"/>
    </game>
    <game name="Contra (USA)">
        <rom name="Contra (USA).nes" crc="CCCC3333"
             header="4E45531A080101000000000000000000"/>
    </game>
</datafile>
"""

_GBA_DAT = """<?xml version="1.0"?>
<datafile>
    <header><name>Nintendo - Game Boy Advance</name></header>
    <game name="Mother 3 (Japan)">
        <rom name="Mother 3 (Japan).gba" crc="dddd4444"
             header="0102030405060708090A0B0C0D0E0F10"/>
    </game>
</datafile>
"""


def test_parse_dat_entries_headers_and_bios_skip(tmp_path):
    dat = tmp_path / "nes.dat"
    dat.write_text(_NES_DAT, encoding="utf-8")

    platform, entries, headers = parse_dat(dat)
    assert platform == "NES"
    assert entries["AAAA1111"] == ("Super Mario Bros. (World)", 40976)
    # Games without a numeric id still count; BIOS entries never do.
    # Missing size attributes parse as 0 (size check then disabled).
    assert entries["CCCC3333"] == ("Contra (USA)", 0)
    assert "BBBB2222" not in entries
    # Both header spellings (spaced / packed) collected as 16 bytes.
    assert len(headers) == 2
    assert all(len(h) == 16 for h in headers)


def test_headers_only_harvested_from_nes_dats(tmp_path):
    dat = tmp_path / "Nintendo - Game Boy Advance (20260101).dat"
    dat.write_text(_GBA_DAT, encoding="utf-8")

    platform, entries, headers = parse_dat(dat)
    assert platform == "GBA"
    assert "DDDD4444" in entries  # CRCs normalize to upper-case
    assert headers == []  # GBA "headers" are not iNES containers


def test_load_dat_index_merges_files(tmp_path):
    (tmp_path / "a.dat").write_text(_NES_DAT, encoding="utf-8")
    (tmp_path / "b.dat").write_text(_GBA_DAT, encoding="utf-8")

    index = load_dat_index(tmp_path)
    assert index.game_count == 3
    assert index.lookup("aaaa1111").name == "Super Mario Bros. (World)"
    assert index.lookup("aaaa1111").size == 40976
    assert index.lookup("DDDD4444").platform == "GBA"
    assert index.lookup("BBBB2222") is None
    assert len(index.nes_headers) == 2
    assert sorted(index.sources) == ["a.dat", "b.dat"]


def test_load_dat_index_missing_dir_is_empty(tmp_path):
    index = load_dat_index(tmp_path / "absent")
    assert index.game_count == 0
    assert index.lookup("AAAA1111") is None


def test_guess_platform_specificity():
    # SNES must win over the NES substring; 3DS over DS.
    assert guess_platform(
        "Nintendo - Super Nintendo Entertainment System (2026)") == "SNES"
    assert guess_platform(
        "Nintendo - Nintendo Entertainment System (Headered)") == "NES"
    assert guess_platform("Nintendo - Nintendo 3DS (Decrypted)") == "3DS"
    assert guess_platform("Nintendo - Nintendo DS (Decrypted)") == "NDS"
    assert guess_platform("Something Unknown") == ""
