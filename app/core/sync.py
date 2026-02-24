"""Sync engine — bidirectional sync through a shared local folder (e.g. OneDrive, 坚果云).

Backup archives are stored as ``{timestamp}.zip`` + ``{timestamp}.json``
pairs.  The sync engine copies these pairs to/from the shared folder.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.config import Config
from app.core.backup import BackupManager
from app.core.conflict import ConflictDetector, ConflictInfo, ConflictResolution, file_sha256


SYNC_MANIFEST = "sync_manifest.json"
SYNC_ROOT_DIR = "emulator-save-manager"


@dataclass
class SyncManifestEntry:
    """One entry in the sync manifest."""

    game_id: str
    emulator: str
    last_sync_time: str  # ISO format
    source_machine: str
    file_hash: str
    relative_path: str  # relative to the sync root
    crc32: str = ""       # disc CRC32 for version verification


@dataclass
class SyncResult:
    """Result of a sync operation."""

    pushed: int = 0
    pulled: int = 0
    conflicts: list[ConflictInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    crc32_warnings: list[str] = field(default_factory=list)
    """Warnings about CRC32 mismatches (different game disc versions)."""


class SyncManager:
    """Manages bidirectional sync of backups through a shared folder."""

    def __init__(self, config: Config, backup_manager: BackupManager) -> None:
        self._cfg = config
        self._bm = backup_manager
        self._detector = ConflictDetector()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def sync_root(self) -> Path:
        """Root sync directory: {sync_folder}/emulator-save-manager/."""
        sf = self._cfg.sync_folder
        if not sf or not sf.exists():
            return Path()
        return sf / SYNC_ROOT_DIR

    @property
    def is_configured(self) -> bool:
        sf = self._cfg.sync_folder
        return bool(sf) and sf.exists()

    # ------------------------------------------------------------------
    # Push — local backups → sync folder
    # ------------------------------------------------------------------

    def push(self, emulator: str, game_id: str) -> SyncResult:
        """Push the latest local backup for a game to the sync folder."""
        result = SyncResult()
        if not self.is_configured:
            result.errors.append("Sync folder not configured")
            return result

        backups = self._bm.list_backups(emulator, game_id)
        if not backups:
            logger.info("No backups to push for {}:{}", emulator, game_id)
            return result

        latest = backups[0]  # newest first
        local_zip = latest.backup_path
        local_meta = local_zip.with_suffix(".json")

        # --- CRC32 version check ---
        local_crc = self._read_meta_field(local_meta, "crc32")
        if local_crc:
            is_mismatch, remote_crc = self.check_crc32_mismatch(
                emulator, game_id, local_crc,
            )
            if is_mismatch:
                result.crc32_warnings.append(
                    f"{emulator}:{game_id} — local CRC {local_crc}, remote CRC {remote_crc}"
                )

        dest_dir = self.sync_root / emulator / game_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_zip = dest_dir / local_zip.name
        dest_meta = dest_dir / local_meta.name

        # Check for conflict with existing remote version
        if dest_zip.exists():
            local_hash = file_sha256(local_zip)
            remote_hash = file_sha256(dest_zip)
            if local_hash == remote_hash:
                logger.info("Push skipped — already in sync: {}:{}", emulator, game_id)
                return result
            conflict = self._detector.detect(
                local_zip, dest_zip,
                game_id=game_id, emulator=emulator,
            )
            if conflict and conflict.is_real_conflict:
                result.conflicts.append(conflict)
                return result

        # Copy zip + meta to sync folder
        try:
            shutil.copy2(local_zip, dest_zip)
            if local_meta.exists():
                shutil.copy2(local_meta, dest_meta)
            result.pushed = 1
            logger.info("Pushed backup {}:{} → {}", emulator, game_id, dest_zip)
            self._update_manifest(emulator, game_id, local_zip.stem, dest_zip)
        except Exception as e:
            msg = f"Push failed for {emulator}:{game_id}: {e}"
            logger.error(msg)
            result.errors.append(msg)

        return result

    # ------------------------------------------------------------------
    # Pull — sync folder → local backups
    # ------------------------------------------------------------------

    def pull(self, emulator: str, game_id: str) -> SyncResult:
        """Pull the latest remote backup for a game from the sync folder."""
        result = SyncResult()
        if not self.is_configured:
            result.errors.append("Sync folder not configured")
            return result

        remote_game_dir = self.sync_root / emulator / game_id
        if not remote_game_dir.exists():
            return result

        # Find latest zip in remote
        remote_zips = sorted(
            remote_game_dir.glob("*.zip"),
            key=lambda p: p.name,
            reverse=True,
        )
        if not remote_zips:
            return result

        latest_remote_zip = remote_zips[0]
        latest_remote_meta = latest_remote_zip.with_suffix(".json")

        # --- CRC32 version check ---
        remote_crc = self._read_meta_field(latest_remote_meta, "crc32")
        if remote_crc:
            local_backups_tmp = self._bm.list_backups(emulator, game_id)
            if local_backups_tmp:
                local_meta = local_backups_tmp[0].backup_path.with_suffix(".json")
                local_crc = self._read_meta_field(local_meta, "crc32")
                if local_crc and local_crc.lower() != remote_crc.lower():
                    result.crc32_warnings.append(
                        f"{emulator}:{game_id} — local CRC {local_crc}, remote CRC {remote_crc}"
                    )

        # Compare with local
        local_backups = self._bm.list_backups(emulator, game_id)
        if local_backups:
            latest_local = local_backups[0]
            local_hash = file_sha256(latest_local.backup_path)
            remote_hash = file_sha256(latest_remote_zip)
            if local_hash == remote_hash:
                logger.info("Pull skipped — already in sync: {}:{}", emulator, game_id)
                return result

            # Check for conflict
            conflict = self._detector.detect(
                latest_local.backup_path, latest_remote_zip,
                game_id=game_id, emulator=emulator,
                remote_machine=self._read_meta_field(latest_remote_meta, "source_machine"),
            )
            if conflict and conflict.is_real_conflict:
                result.conflicts.append(conflict)
                return result

        # Copy remote zip + meta to local backup root
        dest_dir = self._bm.backup_root / emulator / game_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_zip = dest_dir / latest_remote_zip.name
        dest_meta = dest_dir / latest_remote_meta.name
        try:
            shutil.copy2(latest_remote_zip, dest_zip)
            if latest_remote_meta.exists():
                shutil.copy2(latest_remote_meta, dest_meta)
            result.pulled = 1
            logger.info("Pulled backup {}:{} ← {}", emulator, game_id, latest_remote_zip)
        except Exception as e:
            msg = f"Pull failed for {emulator}:{game_id}: {e}"
            logger.error(msg)
            result.errors.append(msg)

        return result

    # ------------------------------------------------------------------
    # Full sync
    # ------------------------------------------------------------------

    def sync_all(self) -> SyncResult:
        """Perform a full bidirectional sync for all known backups."""
        result = SyncResult()
        if not self.is_configured:
            result.errors.append("Sync folder not configured")
            return result

        # Push all local backups
        all_local = self._bm.list_all_backups()
        for key, records in all_local.items():
            emulator, game_id = key.split(":", 1)
            r = self.push(emulator, game_id)
            result.pushed += r.pushed
            result.conflicts.extend(r.conflicts)
            result.errors.extend(r.errors)

        # Pull remote backups not present locally
        if self.sync_root.exists():
            for emu_dir in self.sync_root.iterdir():
                if not emu_dir.is_dir():
                    continue
                for game_dir in emu_dir.iterdir():
                    if not game_dir.is_dir():
                        continue
                    # Only pull if the remote dir contains zip files
                    if not any(game_dir.glob("*.zip")):
                        continue
                    emulator = emu_dir.name
                    game_id = game_dir.name
                    r = self.pull(emulator, game_id)
                    result.pulled += r.pulled
                    result.conflicts.extend(r.conflicts)
                    result.errors.extend(r.errors)

        logger.info(
            "Sync complete — pushed: {}, pulled: {}, conflicts: {}, errors: {}",
            result.pushed, result.pulled, len(result.conflicts), len(result.errors),
        )
        return result

    # ------------------------------------------------------------------
    # Conflict resolution application
    # ------------------------------------------------------------------

    def apply_resolution(
        self, conflict: ConflictInfo, resolution: ConflictResolution
    ) -> list[str]:
        """Apply a conflict resolution decision."""
        errors: list[str] = []
        try:
            if resolution == ConflictResolution.USE_LOCAL:
                # Overwrite remote with local (zip + meta pair)
                for ext in (".zip", ".json"):
                    src = conflict.local_path.with_suffix(ext)
                    dst = conflict.remote_path.with_suffix(ext)
                    if src.exists():
                        shutil.copy2(src, dst)
                logger.info("Conflict resolved: USE_LOCAL for {}", conflict.game_id)

            elif resolution == ConflictResolution.USE_REMOTE:
                for ext in (".zip", ".json"):
                    src = conflict.remote_path.with_suffix(ext)
                    dst = conflict.local_path.with_suffix(ext)
                    if src.exists():
                        shutil.copy2(src, dst)
                logger.info("Conflict resolved: USE_REMOTE for {}", conflict.game_id)

            elif resolution == ConflictResolution.KEEP_BOTH:
                suffix = f"_conflict_{conflict.remote_machine or 'remote'}"
                for ext in (".zip", ".json"):
                    src = conflict.remote_path.with_suffix(ext)
                    if src.exists():
                        alt = src.parent / (conflict.local_path.stem + suffix + ext)
                        shutil.copy2(src, alt)
                logger.info("Conflict resolved: KEEP_BOTH for {}", conflict.game_id)

        except Exception as e:
            msg = f"Failed to apply resolution for {conflict.game_id}: {e}"
            logger.error(msg)
            errors.append(msg)

        return errors

    # ------------------------------------------------------------------
    # Manifest management
    # ------------------------------------------------------------------

    def _update_manifest(
        self, emulator: str, game_id: str, backup_name: str, backup_zip: Path
    ) -> None:
        manifest_path = self.sync_root / SYNC_MANIFEST
        manifest: dict = {}
        if manifest_path.exists():
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception:
                manifest = {}

        meta_path = backup_zip.with_suffix(".json")
        key = f"{emulator}:{game_id}"
        manifest[key] = {
            "game_id": game_id,
            "emulator": emulator,
            "last_sync_time": datetime.now().isoformat(),
            "source_machine": self._cfg.machine_id,
            "file_hash": file_sha256(backup_zip),
            "relative_path": f"{emulator}/{game_id}/{backup_zip.name}",
            "crc32": self._read_meta_field(meta_path, "crc32"),
        }

        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to update sync manifest: {}", e)

    @staticmethod
    def _read_meta_field(meta_path: Path, field: str) -> str:
        """Read a single field from a sidecar .json metadata file."""
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get(field, "")
            except Exception:
                pass
        return ""

    def check_crc32_mismatch(
        self, emulator: str, game_id: str, local_crc32: str
    ) -> tuple[bool, str]:
        """Check if remote CRC32 differs from local for a given game.

        Returns ``(is_mismatch, remote_crc32)``.  A mismatch indicates
        different game disc versions across devices.
        """
        manifest = self.get_manifest()
        key = f"{emulator}:{game_id}"
        entry = manifest.get(key, {})
        remote_crc = entry.get("crc32", "")
        if not remote_crc or not local_crc32:
            return False, remote_crc  # can't compare, assume OK
        mismatch = remote_crc.lower() != local_crc32.lower()
        if mismatch:
            logger.warning(
                "CRC32 mismatch for {}:{} — local={} remote={} (different game version?)",
                emulator, game_id, local_crc32, remote_crc,
            )
        return mismatch, remote_crc

    def get_manifest(self) -> dict:
        """Read the sync manifest."""
        manifest_path = self.sync_root / SYNC_MANIFEST
        if manifest_path.exists():
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
