"""Save-aware rename engine — planning, conflicts, execution, rollback."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.core.backup import BackupManager
from app.core.custom_db import CustomGame
from app.core.rename_engine import RenameEngine, target_stem, sanitize_filename
from app.core.rom_library import RomFile
from app.models.game_save import GameSave, SaveFile, SaveType


def _rom(path: Path, dat_name: str = "", crc32: str = "AAAA1111",
         platform: str = "SNES") -> RomFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(b"ROM")
    return RomFile(
        path=path, size=3, modified=datetime.now(),
        platform=platform, crc32=crc32, dat_name=dat_name,
    )


def _save(root: Path, emulator: str, game_id: str,
          files: dict[str, bytes]) -> GameSave:
    root.mkdir(parents=True, exist_ok=True)
    sfs = []
    for name, content in files.items():
        p = root / name
        p.write_bytes(content)
        sfs.append(SaveFile(path=p, save_type=SaveType.BATTERY,
                            size=len(content), modified=datetime.now()))
    return GameSave(emulator=emulator, game_name=game_id, game_id=game_id,
                    platform="SNES", save_files=sfs)


def _engine(cfg) -> tuple[RenameEngine, BackupManager]:
    bm = BackupManager(cfg)
    return RenameEngine(cfg, bm), bm


# ----------------------------------------------------------------------
# Target naming
# ----------------------------------------------------------------------

def test_target_stem_prefers_dat_name(tmp_path):
    rom = _rom(tmp_path / "ct.sfc", dat_name="Chrono Trigger (USA)")
    assert target_stem(rom, {}) == ("Chrono Trigger (USA)", "dat")


def test_target_stem_custom_with_base_uses_translation_tag(tmp_path):
    rom = _rom(tmp_path / "x.sfc", crc32="BBBB2222")
    custom = {"BBBB2222": CustomGame(
        name="吞食天地", base="Tenchi wo Kurau (Japan)",
        lang="Chs", group="DMG汉化组", version="v1.1")}
    stem, reason = target_stem(rom, custom)
    assert stem == "Tenchi wo Kurau (Japan) [T-Chs DMG汉化组 v1.1]"
    assert reason == "custom"


def test_target_stem_custom_without_base_keeps_chinese_name(tmp_path):
    rom = _rom(tmp_path / "x.gb", crc32="CCCC3333")
    custom = {"CCCC3333": CustomGame(name="彩虹战士", lang="Cht", group="Gowin")}
    stem, _ = target_stem(rom, custom)
    assert stem == "彩虹战士 (Cht) (Gowin)"


def test_target_stem_unidentified_is_empty(tmp_path):
    assert target_stem(_rom(tmp_path / "x.sfc"), {}) == ("", "")


def test_sanitize_strips_illegal_characters():
    assert sanitize_filename('A: "B" <C>|D?*') == "A_ _B_ _C__D__"
    assert "/" not in sanitize_filename("a/b\\c")


# ----------------------------------------------------------------------
# Planning
# ----------------------------------------------------------------------

def test_plan_links_saves_and_backup_chains(cfg, tmp_path):
    engine, bm = _engine(cfg)
    rom = _rom(tmp_path / "roms" / "chrono.sfc", dat_name="Chrono Trigger (USA)")
    save = _save(tmp_path / "saves", "Snes9x", "chrono",
                 {"chrono.srm": b"S", "chrono.000": b"0"})
    bm.create_backup([save])  # existing chain under the old stem

    plan = engine.plan_renames([rom], [save])
    assert len(plan.items) == 1 and not plan.skipped
    item = plan.items[0]
    assert item.new_name == "Chrono Trigger (USA).sfc"
    assert {sr.new_path.name for sr in item.save_renames} == {
        "Chrono Trigger (USA).srm", "Chrono Trigger (USA).000"}
    assert item.backup_emulators == ["Snes9x"]


def test_plan_ignores_serial_keyed_emulators_and_canonical_roms(cfg, tmp_path):
    engine, _ = _engine(cfg)
    rom = _rom(tmp_path / "roms" / "chrono.sfc", dat_name="Chrono Trigger (USA)")
    ps2 = _save(tmp_path / "ps2", "PCSX2", "chrono", {"chrono.ps2": b"M"})
    already = _rom(tmp_path / "roms" / "Final Fantasy VI (USA).sfc",
                   dat_name="Final Fantasy VI (USA)", crc32="DDDD4444")

    plan = engine.plan_renames([rom, already], [ps2])
    assert len(plan.items) == 1
    assert plan.items[0].saves == []          # PCSX2 is serial-keyed
    assert plan.items[0].save_renames == []


def test_plan_skips_conflicts(cfg, tmp_path):
    engine, _ = _engine(cfg)
    roms_dir = tmp_path / "roms"
    rom = _rom(roms_dir / "chrono.sfc", dat_name="Chrono Trigger (USA)")
    (roms_dir / "Chrono Trigger (USA).sfc").write_bytes(b"BLOCKER")

    plan = engine.plan_renames([rom], [])
    assert not plan.items
    assert plan.skipped[0].skip_reason == "target file already exists"

    # Two dumps of the same game → identical target: second one skipped.
    a = _rom(roms_dir / "ct-a.sfc", dat_name="Chrono Trigger II")
    b = _rom(roms_dir / "ct-b.sfc", dat_name="Chrono Trigger II",
             crc32="AAAA1111")
    plan = engine.plan_renames([a, b], [])
    assert len(plan.items) == 1
    assert plan.skipped[0].skip_reason == "duplicate target in plan"


def test_plan_attaches_saves_to_first_duplicate_stem_only(cfg, tmp_path):
    engine, _ = _engine(cfg)
    a = _rom(tmp_path / "d1" / "chrono.sfc", dat_name="Chrono A")
    b = _rom(tmp_path / "d2" / "chrono.sfc", dat_name="Chrono B",
             crc32="EEEE5555")
    save = _save(tmp_path / "saves", "Snes9x", "chrono", {"chrono.srm": b"S"})

    plan = engine.plan_renames([a, b], [save])
    assert len(plan.items) == 2
    assert len(plan.items[0].save_renames) == 1
    assert plan.items[1].save_renames == []


# ----------------------------------------------------------------------
# Execution
# ----------------------------------------------------------------------

def test_execute_renames_rom_saves_and_migrates_chain(cfg, tmp_path):
    engine, bm = _engine(cfg)
    rom = _rom(tmp_path / "roms" / "chrono.sfc", dat_name="Chrono Trigger (USA)")
    save = _save(tmp_path / "saves", "Snes9x", "chrono",
                 {"chrono.srm": b"SRM-DATA", "chrono.000": b"STATE"})
    bm.create_backup([save])

    plan = engine.plan_renames([rom], [save])
    result = engine.execute_plan(plan)

    assert result.errors == []
    assert result.renamed_roms == 1
    assert result.renamed_saves == 2
    assert result.migrated_backups == 1
    # ROM and saves renamed on disk.
    assert (tmp_path / "roms" / "Chrono Trigger (USA).sfc").exists()
    assert not rom.path.exists()
    assert (tmp_path / "saves" / "Chrono Trigger (USA).srm").read_bytes() \
        == b"SRM-DATA"
    # Chain migrated (old chain + pre-rename safety backup), alias resolves.
    records = bm.list_backups("Snes9x", "Chrono Trigger (USA)")
    assert len(records) == 2
    assert bm.resolve_game_id("Snes9x", "chrono") == "Chrono Trigger (USA)"


# ----------------------------------------------------------------------
# Sort-to-library mode
# ----------------------------------------------------------------------

def test_sort_plan_moves_identified_and_leaves_unknown(cfg, tmp_path):
    engine, _ = _engine(cfg)
    lib = tmp_path / "library"
    misnamed = _rom(tmp_path / "inbox" / "chrono.sfc",
                    dat_name="Chrono Trigger (USA)")
    unknown = _rom(tmp_path / "inbox" / "mystery.sfc", crc32="FFFF0000")
    canonical = _rom(tmp_path / "inbox" / "Final Fantasy VI (USA).sfc",
                     dat_name="Final Fantasy VI (USA)", crc32="DDDD4444")

    plan = engine.plan_renames([misnamed, unknown, canonical], [],
                               library_dir=lib)
    # Already-canonical ROMs still move; unidentified ones never do.
    targets = {i.new_rom_path for i in plan.items}
    assert targets == {
        lib / "SNES" / "Chrono Trigger (USA).sfc",
        lib / "SNES" / "Final Fantasy VI (USA).sfc",
    }
    assert not plan.skipped


def test_sort_execute_moves_rom_companions_and_adjacent_saves(cfg, tmp_path):
    engine, bm = _engine(cfg)
    lib = tmp_path / "library"
    inbox = tmp_path / "inbox"
    rom = _rom(inbox / "chrono.sfc", dat_name="Chrono Trigger (USA)")
    (inbox / "chrono.sfc.bak").write_bytes(b"PRISTINE")  # repair original
    adjacent = _save(inbox, "Snes9x", "chrono", {"chrono.srm": b"ADJ"})
    remote = _save(tmp_path / "saves", "Snes9x", "chrono",
                   {"chrono.000": b"STATE"})
    bm.create_backup([adjacent])

    plan = engine.plan_renames(
        [rom], [adjacent, remote], library_dir=lib)
    result = engine.execute_plan(plan)

    assert result.errors == []
    dest = lib / "SNES"
    assert (dest / "Chrono Trigger (USA).sfc").exists()
    # Adjacent save + .bak companion travel with the ROM…
    assert (dest / "Chrono Trigger (USA).srm").read_bytes() == b"ADJ"
    assert (dest / "Chrono Trigger (USA).sfc.bak").read_bytes() == b"PRISTINE"
    # …while the emulator-dir save is renamed where it lives.
    assert (tmp_path / "saves" / "Chrono Trigger (USA).000").exists()
    # Inbox keeps nothing of this game; chain migrated.
    assert list(inbox.iterdir()) == []
    assert bm.list_backups("Snes9x", "Chrono Trigger (USA)")


def test_sort_pure_move_keeps_stem_and_skips_chain_migration(cfg, tmp_path):
    engine, bm = _engine(cfg)
    lib = tmp_path / "library"
    rom = _rom(tmp_path / "inbox" / "Chrono Trigger (USA).sfc",
               dat_name="Chrono Trigger (USA)")
    save = _save(tmp_path / "saves", "Snes9x", "Chrono Trigger (USA)",
                 {"Chrono Trigger (USA).srm": b"S"})

    plan = engine.plan_renames([rom], [save], library_dir=lib)
    assert len(plan.items) == 1
    item = plan.items[0]
    assert item.save_renames == []       # same name, same place
    assert item.backup_emulators == []   # stem unchanged — nothing to migrate

    result = engine.execute_plan(plan)
    assert result.errors == []
    assert (lib / "SNES" / "Chrono Trigger (USA).sfc").exists()
    assert (tmp_path / "saves" / "Chrono Trigger (USA).srm").exists()


def test_sort_duplicate_dump_keeps_one_copy_in_inbox(cfg, tmp_path):
    engine, _ = _engine(cfg)
    lib = tmp_path / "library"
    a = _rom(tmp_path / "inbox" / "1001 - Adventure Island.sfc",
             dat_name="Adventure Island (USA)")
    b = _rom(tmp_path / "inbox" / "Adventure Island (USA) copy.sfc",
             dat_name="Adventure Island (USA)", crc32="AAAA1111")

    plan = engine.plan_renames([a, b], [], library_dir=lib)
    assert len(plan.items) == 1
    assert plan.skipped[0].skip_reason == "duplicate target in plan"

    engine.execute_plan(plan)
    assert (lib / "SNES" / "Adventure Island (USA).sfc").exists()
    assert (tmp_path / "inbox" / "Adventure Island (USA) copy.sfc").exists()


def test_sort_platform_dirname_is_sanitized(cfg, tmp_path):
    engine, _ = _engine(cfg)
    rom = _rom(tmp_path / "inbox" / "game.rvz",
               dat_name="Some Wii Game (USA)", platform="GameCube/Wii")
    plan = engine.plan_renames([rom], [], library_dir=tmp_path / "lib")
    assert plan.items[0].new_rom_path.parent.name == "GameCube_Wii"


def test_execute_rolls_back_saves_when_rom_rename_fails(cfg, tmp_path):
    engine, _ = _engine(cfg)
    rom = _rom(tmp_path / "roms" / "chrono.sfc", dat_name="Chrono Trigger (USA)")
    save = _save(tmp_path / "saves", "Snes9x", "chrono", {"chrono.srm": b"S"})

    plan = engine.plan_renames([rom], [save])
    # Block the ROM target *after* planning to force a mid-item failure.
    (tmp_path / "roms" / "Chrono Trigger (USA).sfc").write_bytes(b"BLOCKER")
    result = engine.execute_plan(plan)

    assert result.renamed_roms == 0
    assert any("ROM rename failed" in e for e in result.errors)
    # Save renames were rolled back — nothing is split.
    assert (tmp_path / "saves" / "chrono.srm").exists()
    assert not (tmp_path / "saves" / "Chrono Trigger (USA).srm").exists()
    assert rom.path.exists()
