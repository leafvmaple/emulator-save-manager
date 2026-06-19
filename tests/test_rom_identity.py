"""ROM library and save identity resolution."""

from __future__ import annotations

import json

from app.core.rom_identity import RomIdentityResolver, normalize_rom_stem
from app.data.rom_library import RomLibrary
from app.models.game_save import GameSave
from app.models.rom_entry import RomEntry, RomInfo


def test_rom_library_loads_legacy_emulator_manager_shape(tmp_path):
    path = tmp_path / "rom_library.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "roms": {
                    "nds:NTR-IPKJ": {
                        "path": "D:/Roms/Pokemon.nds",
                        "platform": "nds",
                        "game_id": "NTR-IPKJ",
                        "rom_info": {"title_name_zh": "宝可梦 金"},
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    lib = RomLibrary(tmp_path)
    lib.load()
    entry = lib.get("nds", "NTR-IPKJ")

    assert entry is not None
    assert entry.rom_path == "D:/Roms/Pokemon.nds"
    assert entry.display_name == "宝可梦 金"


def test_normalize_rom_stem_removes_copy_suffix():
    assert normalize_rom_stem("Game - Copy") == "Game"
    assert normalize_rom_stem("Game - Copy (2)") == "Game"
    assert normalize_rom_stem("Game - 副本") == "Game"
    assert normalize_rom_stem("Game (Japan)") == "Game (Japan)"


def test_rom_identity_updates_display_name_without_changing_save_id(cfg, tmp_path):
    lib = RomLibrary(cfg.data_dir)
    lib.add(
        RomEntry(
            rom_path=str(tmp_path / "口袋妖怪.nds"),
            platform="nds",
            game_id="NTR-IPKJ",
            rom_info=RomInfo(title_name_zh="宝可梦 金", title_id="NTR-IPKJ"),
        )
    )
    lib.save()

    save = GameSave(
        emulator="melonDS",
        game_name="口袋妖怪 - Copy",
        game_id="口袋妖怪 - Copy",
        platform="NDS",
    )

    RomIdentityResolver(cfg).apply_to_saves([save])

    assert save.game_id == "口袋妖怪 - Copy"
    assert save.game_name == "宝可梦 金"


def test_rom_identity_ignores_ambiguous_filename_matches(cfg, tmp_path):
    lib = RomLibrary(cfg.data_dir)
    lib.add(
        RomEntry(
            rom_path=str(tmp_path / "Same.nds"),
            platform="nds",
            game_id="NTR-AAAA",
            rom_info=RomInfo(title_name_zh="第一个"),
        )
    )
    lib.add(
        RomEntry(
            rom_path=str(tmp_path / "Same.nds"),
            platform="nds",
            game_id="NTR-BBBB",
            rom_info=RomInfo(title_name_zh="第二个"),
        )
    )
    lib.save()

    save = GameSave(
        emulator="melonDS",
        game_name="Same",
        game_id="Same",
        platform="NDS",
    )

    RomIdentityResolver(cfg).apply_to_saves([save])

    assert save.game_name == "Same"
