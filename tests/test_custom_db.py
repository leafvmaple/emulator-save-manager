"""Custom game DB — scene-filename parsing, skeleton drafting, scan hits."""

from __future__ import annotations

import json
import zlib

from app.core.custom_db import (
    CustomGame,
    build_skeleton,
    load_custom_db,
    merge_skeleton,
    parse_translation_filename,
    save_custom_db,
)


# ----------------------------------------------------------------------
# Filename convention parsing — real samples from the user's collection
# ----------------------------------------------------------------------

def test_parse_standard_convention():
    p = parse_translation_filename("三国志 (简) (voidboy)(2Mb)")
    assert p == {"name": "三国志", "lang": "Chs", "group": "voidboy",
                 "version": "", "region": ""}


def test_parse_with_version_and_group():
    p = parse_translation_filename("SD高达外传 (繁) (修正版) (Mezaransu)(4Mb)")
    assert p["name"] == "SD高达外传"
    assert p["lang"] == "Cht"
    assert p["version"] == "修正版"
    assert p["group"] == "Mezaransu"


def test_parse_numeric_version_and_compound_group():
    p = parse_translation_filename(
        "口袋妖怪(精灵宝可梦) 皮卡丘 (v1.1)(简)(CKN & 群星)(16Mb)")
    assert p["name"] == "口袋妖怪(精灵宝可梦) 皮卡丘"
    assert p["version"] == "v1.1"
    assert p["lang"] == "Chs"
    assert p["group"] == "CKN & 群星"


def test_parse_parenthesized_title_not_eaten():
    """A tag left of the language tag belongs to the title, not the group."""
    p = parse_translation_filename("人生游戏(超级大富翁)(繁)(修正版)(8Mb)")
    assert p["name"] == "人生游戏(超级大富翁)"
    assert p["lang"] == "Cht"
    assert p["version"] == "修正版"
    assert p["group"] == ""


def test_parse_region_tag():
    p = parse_translation_filename("GB热血！沙滩排球 国雄君(繁)(AXI)(JP)(8Mb)")
    assert p["name"] == "GB热血！沙滩排球 国雄君"
    assert p["lang"] == "Cht"
    assert p["group"] == "AXI"
    assert p["region"] == "JP"


# ----------------------------------------------------------------------
# DB load / save / merge
# ----------------------------------------------------------------------

def test_roundtrip_and_legacy_shape(tmp_path):
    path = tmp_path / "games_custom.json"
    save_custom_db(path, {
        "AAAA1111": CustomGame(name="三国志", base="Sangokushi (Japan)",
                               lang="Chs", group="voidboy"),
    })
    loaded = load_custom_db(path)
    assert loaded["AAAA1111"].base == "Sangokushi (Japan)"

    # Legacy emulator-manager shape ({crc: {name, region}}) loads too;
    # unknown fields are dropped instead of crashing.
    path2 = tmp_path / "legacy.json"
    path2.write_text(
        json.dumps({"BBBB2222": {"name": "Old Entry", "region": "China"}}),
        encoding="utf-8")
    legacy = load_custom_db(path2)
    assert legacy["BBBB2222"].name == "Old Entry"


def test_merge_skeleton_never_touches_existing(tmp_path):
    path = tmp_path / "games_custom.json"
    save_custom_db(path, {"AAAA1111": CustomGame(name="人工校对过的名字")})

    added = merge_skeleton(path, {
        "AAAA1111": CustomGame(name="机器猜的名字"),
        "CCCC3333": CustomGame(name="新条目"),
    })
    assert added == 1
    entries = load_custom_db(path)
    assert entries["AAAA1111"].name == "人工校对过的名字"
    assert entries["CCCC3333"].name == "新条目"


# ----------------------------------------------------------------------
# Skeleton drafting + scan integration
# ----------------------------------------------------------------------

def test_skeleton_drafts_only_unidentified(cfg, tmp_path):
    from app.core.rom_library import RomLibrary, build_report

    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    (roms_dir / "三国志 (简) (voidboy)(2Mb).gb").write_bytes(b"TRANSLATED-GB")
    cfg.set("rom_dirs", [str(roms_dir)])
    lib = RomLibrary(cfg)

    report = build_report(lib.scan(), [])
    drafts = build_skeleton(report.roms)
    crc = f"{zlib.crc32(b'TRANSLATED-GB') & 0xFFFFFFFF:08X}"
    assert drafts[crc].name == "三国志"
    assert drafts[crc].lang == "Chs"
    assert drafts[crc].group == "voidboy"


def test_scan_hits_custom_db_and_reports_base(cfg, tmp_path):
    from app.core.rom_library import RomLibrary, build_report

    content = b"FAN-TRANSLATION-CONTENT"
    crc = f"{zlib.crc32(content) & 0xFFFFFFFF:08X}"
    save_custom_db(cfg.data_dir / "games_custom.json", {
        crc: CustomGame(name="吞食天地 (简)", base="Tenchi wo Kurau (Japan)",
                        lang="Chs", kind="translation"),
    })

    roms_dir = tmp_path / "roms"
    roms_dir.mkdir()
    (roms_dir / "随便什么名字.gb").write_bytes(content)
    cfg.set("rom_dirs", [str(roms_dir)])

    lib = RomLibrary(cfg)
    report = build_report(lib.scan(), [], dat_games=lib.dat_games)

    rom = report.roms[0]
    # Identity follows the content, not the (arbitrary) filename.
    assert rom.custom_name == "吞食天地 (简)"
    assert rom.derived_from == "Tenchi wo Kurau (Japan)"
    assert report.custom_count == 1
    assert report.derived_count == 1
