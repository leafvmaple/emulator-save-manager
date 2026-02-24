"""Sync engine — bidirectional sync through a shared local folder (e.g. OneDrive, 坚果云)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.config import Config
from app.core.backup import BackupManager
from app.core.conflict import ConflictDetector, ConflictInfo, ConflictResolution, file_sha256, dir_sha256


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

        # --- CRC32 version check ---
        local_crc = self._read_backup_crc32(latest.backup_path)
        if local_crc:
            is_mismatch, remote_crc = self.check_crc32_mismatch(
                emulator, game_id, local_crc,
            )
            if is_mismatch:
                result.crc32_warnings.append(
                    f"{emulator}:{game_id} — local CRC {local_crc}, remote CRC {remote_crc}"
                )

        dest_dir = self.sync_root / emulator / game_id / latest.folder_name
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Check for conflict with existing remote version
        if dest_dir.exists() and any(dest_dir.iterdir()):
            local_hash = dir_sha256(latest.backup_path)
            remote_hash = dir_sha256(dest_dir)
            if local_hash == remote_hash:
                logger.info("Push skipped — already in sync: {}:{}", emulator, game_id)
                return result
            conflict = self._detector.detect(
                latest.backup_path, dest_dir,
                game_id=game_id, emulator=emulator,
            )
            if conflict and conflict.is_real_conflict:
                result.conflicts.append(conflict)
                return result

        # Copy backup to sync folder
        try:
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            shutil.copytree(latest.backup_path, dest_dir)
            result.pushed = 1
            logger.info("Pushed backup {}:{} → {}", emulator, game_id, dest_dir)
            self._update_manifest(emulator, game_id, latest.folder_name, dest_dir)
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

        # Find latest backup in remote
        remote_backups = sorted(
            [d for d in remote_game_dir.iterdir() if d.is_dir()],
            key=lambda d: d.name,
            reverse=True,
        )
        if not remote_backups:
            return result

        latest_remote = remote_backups[0]

        # --- CRC32 version check ---
        remote_crc = self._read_backup_crc32(latest_remote)
        if remote_crc:
            local_backups_tmp = self._bm.list_backups(emulator, game_id)
            if local_backups_tmp:
                local_crc = self._read_backup_crc32(local_backups_tmp[0].backup_path)
                if local_crc and local_crc.lower() != remote_crc.lower():
                    result.crc32_warnings.append(
                        f"{emulator}:{game_id} — local CRC {local_crc}, remote CRC {remote_crc}"
                    )

        # Compare with local
        local_backups = self._bm.list_backups(emulator, game_id)
        if local_backups:
            latest_local = local_backups[0]
            local_hash = dir_sha256(latest_local.backup_path)
            remote_hash = dir_sha256(latest_remote)
            if local_hash == remote_hash:
                logger.info("Pull skipped — already in sync: {}:{}", emulator, game_id)
                return result

            # Check for conflict
            conflict = self._detector.detect(
                latest_local.backup_path, latest_remote,
                game_id=game_id, emulator=emulator,
                remote_machine=self._read_remote_machine(latest_remote),
            )
            if conflict and conflict.is_real_conflict:
                result.conflicts.append(conflict)
                return result

        # Copy remote backup to local
        dest_dir = self._bm.backup_root / emulator / game_id / latest_remote.name
        try:
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            shutil.copytree(latest_remote, dest_dir)
            result.pulled = 1
            logger.info("Pulled backup {}:{} ← {}", emulator, game_id, latest_remote)
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
                # Overwrite remote with local
                if conflict.remote_path.exists():
                    if conflict.remote_path.is_dir():
                        shutil.rmtree(conflict.remote_path)
                    else:
                        conflict.remote_path.unlink()
                if conflict.local_path.is_dir():
                    shutil.copytree(conflict.local_path, conflict.remote_path)
                else:
                    shutil.copy2(conflict.local_path, conflict.remote_path)
                logger.info("Conflict resolved: USE_LOCAL for {}", conflict.game_id)

            elif resolution == ConflictResolution.USE_REMOTE:
                # Overwrite local with remote
                if conflict.local_path.exists():
                    if conflict.local_path.is_dir():
                        shutil.rmtree(conflict.local_path)
                    else:
                        conflict.local_path.unlink()
                if conflict.remote_path.is_dir():
                    shutil.copytree(conflict.remote_path, conflict.local_path)
                else:
                    shutil.copy2(conflict.remote_path, conflict.local_path)
                logger.info("Conflict resolved: USE_REMOTE for {}", conflict.game_id)

            elif resolution == ConflictResolution.KEEP_BOTH:
                # Rename the remote with a suffix and copy it beside local
                suffix = f"_conflict_{conflict.remote_machine or 'remote'}"
                if conflict.local_path.is_dir():
                    alt = conflict.local_path.parent / (conflict.local_path.name + suffix)
                    shutil.copytree(conflict.remote_path, alt, dirs_exist_ok=True)
                else:
                    stem = conflict.local_path.stem + suffix
                    alt = conflict.local_path.parent / (stem + conflict.local_path.suffix)
                    shutil.copy2(conflict.remote_path, alt)
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
        self, emulator: str, game_id: str, backup_name: str, backup_dir: Path
    ) -> None:
        manifest_path = self.sync_root / SYNC_MANIFEST
        manifest: dict = {}
        if manifest_path.exists():
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception:
                manifest = {}

        key = f"{emulator}:{game_id}"
        manifest[key] = {
            "game_id": game_id,
            "emulator": emulator,
            "last_sync_time": datetime.now().isoformat(),
            "source_machine": self._cfg.machine_id,
            "file_hash": dir_sha256(backup_dir),
            "relative_path": f"{emulator}/{game_id}/{backup_name}",
            "crc32": self._read_backup_crc32(backup_dir),
        }

        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to update sync manifest: {}", e)

    def _read_remote_machine(self, backup_dir: Path) -> str:
        info_path = backup_dir / "backup_info.json"
        if info_path.exists():
            try:
                with open(info_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("source_machine", "")
            except Exception:
                pass
        return ""

    def _read_backup_crc32(self, backup_dir: Path) -> str:
        """Read CRC32 from a backup's backup_info.json."""
        info_path = backup_dir / "backup_info.json"
        if info_path.exists():
            try:
                with open(info_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("crc32", "")
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
