"""Versioned backup engine — creates ZIP-based backups of game saves.

Each backup produces a pair of files:

    {backup_root}/{emulator}/{game_id}/{YYYY-MM-DD_HH-mm}.zip   — save data
    {backup_root}/{emulator}/{game_id}/{YYYY-MM-DD_HH-mm}.json  — metadata

The ZIP groups **all** selected saves for one game (save states, memory
cards, folder memory cards, etc.) into a single archive so that sync only
needs to copy one file.
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.config import Config
from app.models.backup_record import BackupInfo, BackupRecord
from app.models.game_save import GameSave
from app.core.path_resolver import to_portable_path


class BackupManager:
    """Manages versioned backups of game saves as ZIP archives."""

    def __init__(self, config: Config) -> None:
        self._cfg = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def backup_root(self) -> Path:
        return self._cfg.backup_path

    def create_backup(self, saves: list[GameSave]) -> BackupRecord:
        """Create a ZIP backup for a group of *saves* (same game).

        All :class:`GameSave` objects should share the same
        ``emulator`` / ``game_id``.  The resulting archive contains
        every :class:`SaveFile` from every supplied ``GameSave``.

        Inside the ZIP files are stored under ``{save_type}/{filename}``
        (or ``{save_type}/{dir_name}/…`` for folder saves), with the
        original absolute path recorded in the sidecar JSON so that the
        restore engine knows where to put them back.
        """
        if not saves:
            raise ValueError("No saves to back up")

        ref = saves[0]
        emulator = ref.emulator
        game_id = ref.game_id
        game_name = ref.game_name
        platform = ref.platform
        crc32 = ref.crc32
        emu_data_path = ref.data_path

        # Prefer a human-readable display name / a non-empty CRC / data_path
        for s in saves:
            if s.game_name != s.game_id:
                game_name = s.game_name
                break
        for s in saves:
            if s.crc32:
                crc32 = s.crc32
                break
        for s in saves:
            if s.data_path is not None:
                emu_data_path = s.data_path
                break

        now = datetime.now()
        ts = now.strftime("%Y-%m-%d_%H-%M")
        game_dir = self.backup_root / emulator / game_id
        game_dir.mkdir(parents=True, exist_ok=True)

        zip_path = game_dir / f"{ts}.zip"
        meta_path = game_dir / f"{ts}.json"

        backup_paths: list[dict] = []

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for save in saves:
                for sf in save.save_files:
                    type_prefix = sf.save_type.value
                    try:
                        if sf.path.is_dir():
                            dir_name = sf.path.name
                            zip_prefix = f"{type_prefix}/{dir_name}"
                            for child in sf.path.rglob("*"):
                                if child.is_file():
                                    rel = child.relative_to(sf.path)
                                    arc_name = f"{zip_prefix}/{rel.as_posix()}"
                                    zf.write(child, arc_name)
                            backup_paths.append({
                                "source": to_portable_path(sf.path, emu_data_path),
                                "type": type_prefix,
                                "zip_path": f"{zip_prefix}/",
                                "is_dir": True,
                            })
                        elif sf.path.is_file():
                            arc_name = f"{type_prefix}/{sf.path.name}"
                            zf.write(sf.path, arc_name)
                            backup_paths.append({
                                "source": to_portable_path(sf.path, emu_data_path),
                                "type": type_prefix,
                                "zip_path": arc_name,
                                "is_dir": False,
                            })
                    except Exception as e:
                        logger.error("Failed to add {} to zip: {}", sf.path, e)
                        raise

        # Write sidecar metadata
        # Store the emulator data_path as a portable string so restore
        # on a different machine can map ${EMU_DATA} correctly.
        portable_data_path = (
            to_portable_path(emu_data_path) if emu_data_path else ""
        )
        info = BackupInfo(
            title=game_name,
            game_id=game_id,
            emulator=emulator,
            platform=platform,
            backup_paths=backup_paths,
            source_machine=self._cfg.machine_id,
            crc32=crc32,
            emulator_data_path=portable_data_path,
        )
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(info.to_dict(), f, indent=4, ensure_ascii=False)

        # Version number
        existing = self.list_backups(emulator, game_id)
        version = len(existing) + 1

        record = BackupRecord(
            game_save=GameSave(
                emulator=emulator,
                game_name=game_name,
                game_id=game_id,
                platform=platform,
                crc32=crc32,
            ),
            backup_time=now,
            backup_path=zip_path,
            version=version,
        )
        logger.info("Created backup v{} for {} at {}", version, game_name, zip_path)

        # Auto-rotate
        self.rotate_backups(emulator, game_id)
        return record

    def list_backups(self, emulator: str, game_id: str) -> list[BackupRecord]:
        """List all backup records for a given game, sorted newest-first."""
        game_dir = self.backup_root / emulator / game_id
        if not game_dir.exists():
            return []

        records: list[BackupRecord] = []
        for zp in sorted(game_dir.glob("*.zip"), reverse=True):
            meta_path = zp.with_suffix(".json")
            if not meta_path.exists():
                continue
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                info = BackupInfo.from_dict(data)
                try:
                    bt = datetime.strptime(zp.stem, "%Y-%m-%d_%H-%M")
                except ValueError:
                    bt = datetime.fromtimestamp(zp.stat().st_mtime)

                gs = GameSave(
                    emulator=info.emulator,
                    game_name=info.title,
                    game_id=info.game_id,
                    platform=info.platform,
                    crc32=info.crc32,
                )
                records.append(BackupRecord(
                    game_save=gs,
                    backup_time=bt,
                    backup_path=zp,
                    is_pinned=info.is_pinned,
                    label=info.label,
                ))
            except Exception as e:
                logger.warning("Failed to read backup meta {}: {}", meta_path, e)

        # Assign version numbers (oldest = 1)
        for i, r in enumerate(reversed(records), start=1):
            r.version = i
        return records

    def list_all_backups(self) -> dict[str, list[BackupRecord]]:
        """Return all backups grouped by ``emulator:game_id``."""
        result: dict[str, list[BackupRecord]] = {}
        if not self.backup_root.exists():
            return result
        for emu_dir in self.backup_root.iterdir():
            if not emu_dir.is_dir():
                continue
            for game_dir in emu_dir.iterdir():
                if not game_dir.is_dir():
                    continue
                key = f"{emu_dir.name}:{game_dir.name}"
                records = self.list_backups(emu_dir.name, game_dir.name)
                if records:
                    result[key] = records
        return result

    def rotate_backups(self, emulator: str, game_id: str) -> int:
        """Delete oldest non-pinned backups exceeding the max limit."""
        max_backups = self._cfg.max_backups
        records = self.list_backups(emulator, game_id)
        non_pinned = [r for r in records if not r.is_pinned]

        deleted = 0
        if len(non_pinned) > max_backups:
            to_delete = non_pinned[max_backups:]
            for record in to_delete:
                try:
                    self.delete_backup(record)
                    deleted += 1
                    logger.info("Rotated old backup: {}", record.backup_path)
                except Exception as e:
                    logger.error("Failed to delete backup {}: {}", record.backup_path, e)
        return deleted

    def pin_backup(self, record: BackupRecord, label: str = "") -> None:
        """Mark a backup as permanently pinned."""
        self._update_meta(record.backup_path, is_pinned=True, label=label)
        record.is_pinned = True
        record.label = label

    def unpin_backup(self, record: BackupRecord) -> None:
        """Remove the pin from a backup."""
        self._update_meta(record.backup_path, is_pinned=False)
        record.is_pinned = False

    def delete_backup(self, record: BackupRecord) -> None:
        """Delete a specific backup (zip + sidecar json)."""
        zp = record.backup_path
        meta = zp.with_suffix(".json")
        if zp.exists():
            zp.unlink()
        if meta.exists():
            meta.unlink()
        logger.info("Deleted backup: {}", zp)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_meta(self, zip_path: Path, **kwargs: object) -> None:
        meta_path = zip_path.with_suffix(".json")
        if not meta_path.exists():
            return
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.update(kwargs)
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to update backup meta {}: {}", meta_path, e)
