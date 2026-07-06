"""Game-id rename migration — alias table + backup-chain moves.

Cartridge-family plugins key backups by ROM filename, so renaming a ROM
must migrate the chain and keep old references resolving.
"""

from __future__ import annotations

import json

from app.core.backup import BackupManager


def _meta_game_ids(bm: BackupManager, emulator: str, game_id: str) -> set[str]:
    game_dir = bm.backup_root / emulator / game_id
    ids = set()
    for meta in game_dir.glob("*.json"):
        data = json.loads(meta.read_text(encoding="utf-8"))
        ids.add(data.get("game_id"))
    return ids


def test_rename_moves_chain_and_rewrites_meta(cfg, make_game_save, tmp_path):
    gs = make_game_save(tmp_path / "s", emulator="Snes9x", game_id="old rom name")
    bm = BackupManager(cfg)
    bm.create_backup([gs])
    bm.create_backup([gs])

    moved = bm.rename_game("Snes9x", "old rom name", "New Rom (USA)")
    assert moved == 2

    old_dir = bm.backup_root / "Snes9x" / "old rom name"
    new_dir = bm.backup_root / "Snes9x" / "New Rom (USA)"
    assert not old_dir.exists()
    assert len(list(new_dir.glob("*.zip"))) == 2
    # Sidecar metadata now carries the canonical id
    assert _meta_game_ids(bm, "Snes9x", "New Rom (USA)") == {"New Rom (USA)"}


def test_lookup_by_old_id_follows_alias(cfg, make_game_save, tmp_path):
    gs = make_game_save(tmp_path / "s", emulator="Snes9x", game_id="oldname")
    bm = BackupManager(cfg)
    bm.create_backup([gs])
    bm.rename_game("Snes9x", "oldname", "newname")

    # Stale references (old scans, sync records) still find the chain.
    records = bm.list_backups("Snes9x", "oldname")
    assert len(records) == 1
    assert records[0].game_save.game_id == "newname"


def test_create_backup_with_stale_id_lands_in_canonical_dir(
    cfg, make_game_save, tmp_path
):
    gs = make_game_save(tmp_path / "s", emulator="Snes9x", game_id="oldname")
    bm = BackupManager(cfg)
    bm.create_backup([gs])
    bm.rename_game("Snes9x", "oldname", "newname")

    # A backup created from a pre-rename GameSave must not resurrect
    # the old directory.
    bm.create_backup([gs])
    assert not (bm.backup_root / "Snes9x" / "oldname").exists()
    assert len(bm.list_backups("Snes9x", "newname")) == 2


def test_chained_renames_compress_to_canonical(cfg, make_game_save, tmp_path):
    gs = make_game_save(tmp_path / "s", emulator="Snes9x", game_id="a")
    bm = BackupManager(cfg)
    bm.create_backup([gs])
    bm.rename_game("Snes9x", "a", "b")
    bm.rename_game("Snes9x", "b", "c")

    assert bm.resolve_game_id("Snes9x", "a") == "c"
    assert bm.resolve_game_id("Snes9x", "b") == "c"
    # Values are re-pointed on each rename, so resolution is one hop.
    aliases = json.loads(
        (bm.backup_root / "aliases.json").read_text(encoding="utf-8"))
    assert aliases["Snes9x:a"] == "c"


def test_rename_back_does_not_cycle(cfg, make_game_save, tmp_path):
    gs = make_game_save(tmp_path / "s", emulator="Snes9x", game_id="a")
    bm = BackupManager(cfg)
    bm.create_backup([gs])
    bm.rename_game("Snes9x", "a", "b")
    bm.rename_game("Snes9x", "b", "a")

    # a is canonical again; resolving either id terminates.
    assert bm.resolve_game_id("Snes9x", "b") == "a"
    assert bm.resolve_game_id("Snes9x", "a") == "a"
    assert len(bm.list_backups("Snes9x", "a")) == 1


def test_rename_merges_into_existing_target(cfg, make_game_save, tmp_path):
    bm = BackupManager(cfg)
    gs_old = make_game_save(tmp_path / "s1", emulator="Snes9x", game_id="old")
    gs_new = make_game_save(tmp_path / "s2", emulator="Snes9x", game_id="new")
    bm.create_backup([gs_old])
    bm.create_backup([gs_new])

    bm.rename_game("Snes9x", "old", "new")
    assert len(bm.list_backups("Snes9x", "new")) == 2


def test_rename_noop_cases(cfg, make_game_save, tmp_path):
    gs = make_game_save(tmp_path / "s", emulator="Snes9x", game_id="same")
    bm = BackupManager(cfg)
    bm.create_backup([gs])

    assert bm.rename_game("Snes9x", "same", "same") == 0
    assert bm.rename_game("Snes9x", "same", "") == 0
    assert not (bm.backup_root / "aliases.json").exists()


def test_aliases_are_scoped_per_emulator(cfg, make_game_save, tmp_path):
    bm = BackupManager(cfg)
    gs = make_game_save(tmp_path / "s", emulator="Snes9x", game_id="shared")
    bm.create_backup([gs])
    bm.rename_game("Snes9x", "shared", "renamed")

    # The same id under another emulator is untouched.
    assert bm.resolve_game_id("Mesen", "shared") == "shared"
