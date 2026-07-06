"""Save-aware ROM rename engine (roadmap ROM Stage B).

Renaming a ROM breaks two things generic ROM managers ignore: the
emulator's own save lookup (cartridge-family emulators match saves by
ROM filename stem) and this app's backup chains (keyed by
``game_id`` = stem).  This engine renames all three in one planned
transaction:

    plan_renames(roms, saves)  ->  RenamePlan     (pure dry-run)
    execute_plan(plan)         ->  RenameResult   (per-game, isolated)

Target names come from authoritative identity only: the DAT canonical
name for verified dumps, or the curated custom-DB entry for
translations / hacks (``base [T-lang group version]`` when the base
game is known, else ``name (lang) (group) (version)``).  Unidentified
ROMs are never renamed.

Per game the execution order is: safety backup of the live saves →
rename save files → rename the ROM → migrate backup chains and record
the game-id alias.  A failure at any step rolls back this game's
completed save renames and skips its remaining steps, so ROM and saves
never end up split.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from app.config import Config
from app.core.backup import BackupManager
from app.core.custom_db import CustomGame, load_custom_db
from app.core.rom_library import FILENAME_KEYED_EMULATORS, RomFile
from app.models.game_save import GameSave

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """Make *name* legal on every platform we ship to (notably Windows)."""
    cleaned = _ILLEGAL_CHARS.sub("_", name).strip(" .")
    return cleaned


def target_stem(rom: RomFile, custom: dict[str, CustomGame]) -> tuple[str, str]:
    """Return ``(new_stem, reason)`` for *rom*, or ``("", "")`` if unnamed.

    ``reason`` is ``"dat"`` or ``"custom"``.
    """
    if rom.dat_name:
        return sanitize_filename(rom.dat_name), "dat"

    entry = custom.get(rom.crc32) if rom.crc32 else None
    if entry is None or not entry.name:
        return "", ""
    if entry.base:
        tag_parts = [f"T-{entry.lang}" if entry.lang else "T"]
        if entry.group:
            tag_parts.append(entry.group)
        if entry.version:
            tag_parts.append(entry.version)
        stem = f"{entry.base} [{' '.join(tag_parts)}]"
    else:
        parts = [entry.name]
        for value in (entry.lang, entry.group, entry.version):
            if value:
                parts.append(f"({value})")
        stem = " ".join(parts)
    return sanitize_filename(stem), "custom"


@dataclass
class SaveRename:
    """One save file (or folder) that follows the ROM's new stem."""

    old_path: Path
    new_path: Path


@dataclass
class GameRename:
    """Planned rename of one ROM plus everything keyed to its stem."""

    rom: RomFile
    new_stem: str
    reason: str
    saves: list[GameSave] = field(default_factory=list)
    save_renames: list[SaveRename] = field(default_factory=list)
    backup_emulators: list[str] = field(default_factory=list)
    """Emulators whose backup chain will be migrated."""
    skip_reason: str = ""

    @property
    def old_stem(self) -> str:
        return self.rom.path.stem

    @property
    def new_name(self) -> str:
        return self.new_stem + self.rom.path.suffix

    @property
    def new_rom_path(self) -> Path:
        return self.rom.path.with_name(self.new_name)


@dataclass
class RenamePlan:
    items: list[GameRename] = field(default_factory=list)
    """Renames that will run."""
    skipped: list[GameRename] = field(default_factory=list)
    """Excluded items, each with a ``skip_reason``."""


@dataclass
class RenameResult:
    renamed_roms: int = 0
    renamed_saves: int = 0
    migrated_backups: int = 0
    errors: list[str] = field(default_factory=list)


class RenameEngine:
    """Plans and executes save-aware ROM normalization."""

    def __init__(self, config: Config, backup_mgr: BackupManager) -> None:
        self._cfg = config
        self._bm = backup_mgr

    # ------------------------------------------------------------------
    # Planning (pure — touches nothing on disk)
    # ------------------------------------------------------------------

    def plan_renames(
        self,
        roms: list[RomFile],
        saves: list[GameSave],
    ) -> RenamePlan:
        """Build the dry-run plan for every identifiable, mis-named ROM."""
        custom = load_custom_db(self._cfg.data_dir / "games_custom.json")

        saves_by_stem: dict[str, list[GameSave]] = {}
        for save in saves:
            if save.emulator in FILENAME_KEYED_EMULATORS:
                saves_by_stem.setdefault(save.game_id.lower(), []).append(save)

        plan = RenamePlan()
        claimed_targets: set[str] = set()
        claimed_stems: set[str] = set()

        for rom in roms:
            new_stem, reason = target_stem(rom, custom)
            if not new_stem or new_stem == rom.path.stem:
                continue

            item = GameRename(rom=rom, new_stem=new_stem, reason=reason)
            old_stem_key = rom.path.stem.lower()
            target_key = os.path.normcase(str(item.new_rom_path))

            # The stem's saves follow the first copy only — a duplicate
            # dump renaming the same saves twice would fail midway.
            if old_stem_key not in claimed_stems:
                item.saves = saves_by_stem.get(old_stem_key, [])

            if target_key in claimed_targets:
                item.skip_reason = "duplicate target in plan"
            elif (
                item.new_rom_path.exists()
                and os.path.normcase(str(item.new_rom_path))
                != os.path.normcase(str(rom.path))
            ):
                item.skip_reason = "target file already exists"
            else:
                item.skip_reason = self._plan_save_renames(item)

            if item.skip_reason:
                plan.skipped.append(item)
                continue

            item.backup_emulators = self._chains_to_migrate(item)
            claimed_targets.add(target_key)
            claimed_stems.add(old_stem_key)
            plan.items.append(item)

        return plan

    def _plan_save_renames(self, item: GameRename) -> str:
        """Fill ``item.save_renames``; return a skip reason on conflict."""
        old_stem = item.old_stem
        for save in item.saves:
            for sf in save.save_files:
                name = sf.path.name
                if not name.lower().startswith(old_stem.lower()):
                    logger.warning(
                        "Save file {} does not carry stem {}, leaving as is",
                        sf.path, old_stem)
                    continue
                new_name = item.new_stem + name[len(old_stem):]
                new_path = sf.path.with_name(new_name)
                if new_path.exists() and os.path.normcase(
                        str(new_path)) != os.path.normcase(str(sf.path)):
                    return f"save target already exists: {new_path.name}"
                item.save_renames.append(SaveRename(sf.path, new_path))
        return ""

    def _chains_to_migrate(self, item: GameRename) -> list[str]:
        """Emulators with a backup chain keyed to the old stem."""
        emulators = {s.emulator for s in item.saves}
        # Chains can outlive their saves — check the store directly too.
        for emu in FILENAME_KEYED_EMULATORS:
            if (self._bm.backup_root / emu / item.old_stem).is_dir():
                emulators.add(emu)
        return sorted(emulators)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute_plan(self, plan: RenamePlan) -> RenameResult:
        """Apply every planned item; failures are isolated per game."""
        result = RenameResult()
        for item in plan.items:
            self._execute_item(item, result)
        logger.info(
            "Rename run: {} ROMs, {} save files, {} backup chains, {} errors",
            result.renamed_roms, result.renamed_saves,
            result.migrated_backups, len(result.errors),
        )
        return result

    def _execute_item(self, item: GameRename, result: RenameResult) -> None:
        label = f"{item.rom.path.name} → {item.new_name}"

        # 1. Safety backup of the live saves (per emulator group).
        by_emulator: dict[str, list[GameSave]] = {}
        for save in item.saves:
            by_emulator.setdefault(save.emulator, []).append(save)
        for emu, group in by_emulator.items():
            try:
                self._bm.create_backup(group)
            except Exception as e:  # noqa: BLE001
                result.errors.append(
                    f"{label}: safety backup failed ({emu}): {e}")
                return

        # 2. Rename save files — roll back the ones done on failure.
        done: list[SaveRename] = []
        for sr in item.save_renames:
            try:
                self._safe_rename(sr.old_path, sr.new_path)
                done.append(sr)
            except OSError as e:
                result.errors.append(
                    f"{label}: save rename failed for {sr.old_path.name}: {e}")
                self._rollback(done, result)
                return

        # 3. Rename the ROM itself.
        try:
            self._safe_rename(item.rom.path, item.new_rom_path)
        except OSError as e:
            result.errors.append(f"{label}: ROM rename failed: {e}")
            self._rollback(done, result)
            return

        # 4. Migrate backup chains + record the game-id alias.
        for emu in item.backup_emulators:
            try:
                self._bm.rename_game(emu, item.old_stem, item.new_stem)
                result.migrated_backups += 1
            except Exception as e:  # noqa: BLE001
                # ROM + saves are already consistent; the alias keeps old
                # references resolving, so this is a warning not a rollback.
                result.errors.append(
                    f"{label}: backup migration failed ({emu}): {e}")

        result.renamed_roms += 1
        result.renamed_saves += len(done)
        logger.info("Renamed {} (+{} save files)", label, len(done))

    @staticmethod
    def _safe_rename(old: Path, new: Path) -> None:
        """Rename refusing to clobber an existing target.

        POSIX ``rename`` silently replaces the destination; Windows
        raises.  Make both behave like Windows — a rename must never
        destroy a file the plan didn't know about (case-only renames of
        the same file are still allowed).
        """
        if new.exists() and os.path.normcase(str(new)) != os.path.normcase(str(old)):
            raise FileExistsError(f"target exists: {new}")
        old.rename(new)

    @staticmethod
    def _rollback(done: list[SaveRename], result: RenameResult) -> None:
        for sr in reversed(done):
            try:
                sr.new_path.rename(sr.old_path)
            except OSError as e:
                result.errors.append(
                    f"rollback failed for {sr.new_path}: {e}")
