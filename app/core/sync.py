"""Sync engine — bidirectional sync of backups through a pluggable backend.

Local backups (zip + sidecar json pairs) are pushed to / pulled from a remote
:class:`~app.core.sync_backend.SyncBackend` (a shared local folder or a WebDAV
server).  The engine itself is storage-agnostic: all remote access goes through
the backend's relative-path operations.
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from loguru import logger

from app.config import Config
from app.core.backup import BackupManager
from app.core.conflict import (
    ConflictInfo, ConflictResolution,
    zip_content_hash, zip_content_hash_bytes,
)
from app.core.sync_backend import SyncBackend, make_backend

SYNC_MANIFEST = "sync_manifest.json"
TRANSFER_CHUNK_SIZE = 1024 * 1024


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


@dataclass(frozen=True)
class SyncProgress:
    """Progress update for one sync transfer."""

    operation: str
    emulator: str
    game_id: str
    file_name: str
    current: int = 0
    total: int = 0


SyncProgressCallback = Callable[[SyncProgress], None]


class SyncManager:
    """Manages bidirectional sync of backups through a :class:`SyncBackend`."""

    def __init__(
        self,
        config: Config,
        backup_manager: BackupManager,
        backend: SyncBackend | None = None,
    ) -> None:
        self._cfg = config
        self._bm = backup_manager
        # When a backend is injected (tests) it's fixed; otherwise it's rebuilt
        # from config before each operation so changing the sync method / WebDAV
        # settings at runtime takes effect without an app restart.
        self._injected = backend
        self._backend = backend if backend is not None else make_backend(config)

    def _refresh_backend(self) -> None:
        if self._injected is None:
            self._backend = make_backend(self._cfg)

    @property
    def backend(self) -> SyncBackend:
        return self._backend

    @property
    def is_configured(self) -> bool:
        self._refresh_backend()
        return self._backend.is_configured

    # ------------------------------------------------------------------
    # Push — local backups → remote
    # ------------------------------------------------------------------

    def push(
        self,
        emulator: str,
        game_id: str,
        progress_callback: SyncProgressCallback | None = None,
    ) -> SyncResult:
        """Push the latest local backup for a game to the remote."""
        result = SyncResult()
        if not self._backend.is_configured:  # backend already resolved by caller
            result.errors.append("Sync not configured")
            return result

        backups = self._bm.list_backups(emulator, game_id)
        if not backups:
            logger.info("No backups to push for {}:{}", emulator, game_id)
            return result

        latest = backups[0]  # newest first
        local_zip = latest.backup_path
        local_meta = local_zip.with_suffix(".json")
        try:
            local_zip_size = local_zip.stat().st_size
        except OSError as e:
            result.errors.append(f"Cannot read backup {local_zip}: {e}")
            return result
        local_meta_data = local_meta.read_bytes() if local_meta.exists() else None

        rel_zip = f"{emulator}/{game_id}/{local_zip.name}"
        rel_meta = f"{emulator}/{game_id}/{local_meta.name}"

        # --- CRC32 version check ---
        local_crc = self._meta_field(local_meta_data, "crc32")
        if local_crc:
            is_mismatch, remote_crc = self.check_crc32_mismatch(emulator, game_id, local_crc)
            if is_mismatch:
                result.crc32_warnings.append(
                    f"{emulator}:{game_id} — local CRC {local_crc}, remote CRC {remote_crc}"
                )

        # Existing remote version → skip if identical, else a conflict.
        if self._backend.exists(rel_zip):
            remote_meta_data = self._backend.read_bytes(rel_meta)
            local_h = self._content_hash(local_zip)
            remote_h = self._meta_field(remote_meta_data, "content_hash")
            if not remote_h:
                remote_zip_data = self._backend.read_bytes(
                    rel_zip,
                    progress_callback=self._transfer_progress(
                        progress_callback, "check", emulator, game_id, local_zip.name,
                    ),
                )
                remote_h = self._content_hash_remote(remote_zip_data, remote_meta_data)
            if local_h and local_h == remote_h:
                logger.info("Push skipped — already in sync: {}:{}", emulator, game_id)
                return result
            result.conflicts.append(self._make_conflict(
                emulator, game_id, local_zip, local_h, rel_zip, remote_h, remote_meta_data,
            ))
            return result

        try:
            self._emit_progress(
                progress_callback, "push", emulator, game_id, local_zip.name, 0,
                local_zip_size,
            )
            self._backend.write_file(
                rel_zip,
                local_zip,
                progress_callback=self._transfer_progress(
                    progress_callback, "push", emulator, game_id, local_zip.name,
                ),
            )
            if local_meta_data is not None:
                self._backend.write_bytes(rel_meta, local_meta_data)
            result.pushed = 1
            logger.info("Pushed backup {}:{} → {}", emulator, game_id, rel_zip)
            self._update_manifest(emulator, game_id, local_zip, local_meta_data)
        except Exception as e:  # noqa: BLE001
            msg = f"Push failed for {emulator}:{game_id}: {e}"
            logger.error(msg)
            result.errors.append(msg)

        return result

    # ------------------------------------------------------------------
    # Pull — remote → local backups
    # ------------------------------------------------------------------

    def pull(
        self,
        emulator: str,
        game_id: str,
        progress_callback: SyncProgressCallback | None = None,
    ) -> SyncResult:
        """Pull the latest remote backup for a game to local storage."""
        result = SyncResult()
        if not self._backend.is_configured:  # backend already resolved by caller
            result.errors.append("Sync not configured")
            return result

        rel_dir = f"{emulator}/{game_id}"
        zips = sorted(
            (n for n in self._backend.list_dir(rel_dir) if n.endswith(".zip")),
            reverse=True,
        )
        if not zips:
            return result

        latest_name = zips[0]
        rel_zip = f"{rel_dir}/{latest_name}"
        rel_meta = f"{rel_dir}/{latest_name[:-4]}.json"

        remote_meta_data = self._backend.read_bytes(rel_meta)

        # --- CRC32 version check ---
        local_backups = self._bm.list_backups(emulator, game_id)
        remote_crc = self._meta_field(remote_meta_data, "crc32")
        if remote_crc and local_backups:
            local_meta = local_backups[0].backup_path.with_suffix(".json")
            local_crc = self._read_meta_field(local_meta, "crc32")
            if local_crc and local_crc.lower() != remote_crc.lower():
                result.crc32_warnings.append(
                    f"{emulator}:{game_id} — local CRC {local_crc}, remote CRC {remote_crc}"
                )

        # Compare with local
        if local_backups:
            latest_local = local_backups[0]
            local_h = self._content_hash(latest_local.backup_path)
            remote_h = self._meta_field(remote_meta_data, "content_hash")
            if not remote_h:
                remote_zip_data = self._backend.read_bytes(
                    rel_zip,
                    progress_callback=self._transfer_progress(
                        progress_callback, "pull", emulator, game_id, latest_name,
                    ),
                )
                if remote_zip_data is None:
                    return result
                remote_h = self._content_hash_remote(remote_zip_data, remote_meta_data)
            if local_h and local_h == remote_h:
                logger.info("Pull skipped — already in sync: {}:{}", emulator, game_id)
                return result
            result.conflicts.append(self._make_conflict(
                emulator, game_id, latest_local.backup_path, local_h,
                rel_zip, remote_h, remote_meta_data,
            ))
            return result

        # Write remote zip + meta to local backup root
        dest_dir = self._bm.backup_root / emulator / game_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            dest_zip = dest_dir / latest_name
            if not self._backend.read_file(
                rel_zip,
                dest_zip,
                progress_callback=self._transfer_progress(
                    progress_callback, "pull", emulator, game_id, latest_name,
                ),
            ):
                return result
            if remote_meta_data is not None:
                (dest_dir / f"{latest_name[:-4]}.json").write_bytes(remote_meta_data)
            result.pulled = 1
            logger.info("Pulled backup {}:{} ← {}", emulator, game_id, rel_zip)
        except OSError as e:
            msg = f"Pull failed for {emulator}:{game_id}: {e}"
            logger.error(msg)
            result.errors.append(msg)

        return result

    # ------------------------------------------------------------------
    # Full sync
    # ------------------------------------------------------------------

    def push_all(
        self,
        should_cancel: Callable[[], bool] | None = None,
        progress_callback: SyncProgressCallback | None = None,
    ) -> SyncResult:
        """Push every local backup to the remote."""
        result = SyncResult()
        if not self.is_configured:
            result.errors.append("Sync not configured")
            return result

        for key in self._bm.list_all_backups():
            if should_cancel and should_cancel():
                logger.info("Push cancelled")
                break
            emulator, game_id = key.split(":", 1)
            self._merge(
                result,
                self.push(emulator, game_id, progress_callback=progress_callback),
            )

        logger.info(
            "Push complete — pushed: {}, conflicts: {}, errors: {}",
            result.pushed, len(result.conflicts), len(result.errors),
        )
        return result

    def pull_all(
        self,
        should_cancel: Callable[[], bool] | None = None,
        progress_callback: SyncProgressCallback | None = None,
    ) -> SyncResult:
        """Pull every remote backup to local storage."""
        result = SyncResult()
        if not self.is_configured:
            result.errors.append("Sync not configured")
            return result

        for emulator in self._backend.list_dir(""):
            if emulator == SYNC_MANIFEST:
                continue
            for game_id in self._backend.list_dir(emulator):
                if should_cancel and should_cancel():
                    logger.info("Pull cancelled")
                    return result
                rel_dir = f"{emulator}/{game_id}"
                if not any(n.endswith(".zip") for n in self._backend.list_dir(rel_dir)):
                    continue
                self._merge(
                    result,
                    self.pull(emulator, game_id, progress_callback=progress_callback),
                )

        logger.info(
            "Pull complete — pulled: {}, conflicts: {}, errors: {}",
            result.pulled, len(result.conflicts), len(result.errors),
        )
        return result

    def sync_all(
        self,
        should_cancel: Callable[[], bool] | None = None,
        progress_callback: SyncProgressCallback | None = None,
    ) -> SyncResult:
        """Perform a full bidirectional sync for all known backups."""
        result = SyncResult()
        if not self.is_configured:
            result.errors.append("Sync not configured")
            return result

        push_result = self.push_all(should_cancel, progress_callback=progress_callback)
        pull_result = self.pull_all(should_cancel, progress_callback=progress_callback)
        result.pushed = push_result.pushed
        result.pulled = pull_result.pulled
        result.conflicts = push_result.conflicts + pull_result.conflicts
        result.errors = push_result.errors + pull_result.errors
        result.crc32_warnings = push_result.crc32_warnings + pull_result.crc32_warnings

        logger.info(
            "Sync complete — pushed: {}, pulled: {}, conflicts: {}, errors: {}",
            result.pushed, result.pulled, len(result.conflicts), len(result.errors),
        )
        return result

    @staticmethod
    def _merge(into: SyncResult, r: SyncResult) -> None:
        into.pushed += r.pushed
        into.pulled += r.pulled
        into.conflicts.extend(r.conflicts)
        into.errors.extend(r.errors)
        into.crc32_warnings.extend(r.crc32_warnings)

    # ------------------------------------------------------------------
    # Conflict resolution application
    # ------------------------------------------------------------------

    def apply_resolution(
        self, conflict: ConflictInfo, resolution: ConflictResolution
    ) -> list[str]:
        """Apply a conflict resolution decision through the backend."""
        errors: list[str] = []
        rel_zip = conflict.remote_rel
        rel_meta = (rel_zip[:-4] + ".json") if rel_zip.endswith(".zip") else rel_zip + ".json"
        local_zip = conflict.local_path
        local_meta = local_zip.with_suffix(".json")
        try:
            if resolution == ConflictResolution.USE_LOCAL:
                self._backend.write_file(rel_zip, local_zip)
                if local_meta.exists():
                    self._backend.write_bytes(rel_meta, local_meta.read_bytes())
                logger.info("Conflict resolved: USE_LOCAL for {}", conflict.game_id)

            elif resolution == ConflictResolution.USE_REMOTE:
                zd = self._backend.read_bytes(rel_zip)
                if zd is not None:
                    local_zip.parent.mkdir(parents=True, exist_ok=True)
                    local_zip.write_bytes(zd)
                md = self._backend.read_bytes(rel_meta)
                if md is not None:
                    local_meta.write_bytes(md)
                logger.info("Conflict resolved: USE_REMOTE for {}", conflict.game_id)

            elif resolution == ConflictResolution.KEEP_BOTH:
                # Keep the local copy and save the remote one alongside it.
                suffix = f"_conflict_{conflict.remote_machine or 'remote'}"
                zd = self._backend.read_bytes(rel_zip)
                if zd is not None:
                    alt = local_zip.parent / f"{local_zip.stem}{suffix}{local_zip.suffix}"
                    alt.parent.mkdir(parents=True, exist_ok=True)
                    alt.write_bytes(zd)
                    md = self._backend.read_bytes(rel_meta)
                    if md is not None:
                        alt.with_suffix(".json").write_bytes(md)
                logger.info("Conflict resolved: KEEP_BOTH for {}", conflict.game_id)

        except Exception as e:  # noqa: BLE001
            msg = f"Failed to apply resolution for {conflict.game_id}: {e}"
            logger.error(msg)
            errors.append(msg)

        return errors

    # ------------------------------------------------------------------
    # Manifest management
    # ------------------------------------------------------------------

    def get_manifest(self) -> dict:
        """Read the sync manifest from the remote."""
        data = self._backend.read_bytes(SYNC_MANIFEST)
        if data:
            try:
                return json.loads(data)
            except Exception:  # noqa: BLE001
                pass
        return {}

    def _update_manifest(
        self, emulator: str, game_id: str, local_zip: Path,
        meta_data: bytes | None,
    ) -> None:
        manifest = self.get_manifest()
        manifest[f"{emulator}:{game_id}"] = {
            "game_id": game_id,
            "emulator": emulator,
            "last_sync_time": datetime.now().isoformat(),
            "source_machine": self._cfg.machine_id,
            "file_hash": self._file_sha256(local_zip),
            "relative_path": f"{emulator}/{game_id}/{local_zip.name}",
            "crc32": self._meta_field(meta_data, "crc32"),
        }
        try:
            self._backend.write_bytes(
                SYNC_MANIFEST,
                json.dumps(manifest, indent=4, ensure_ascii=False).encode("utf-8"),
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to update sync manifest: {}", e)

    def check_crc32_mismatch(
        self, emulator: str, game_id: str, local_crc32: str
    ) -> tuple[bool, str]:
        """Return ``(is_mismatch, remote_crc32)`` from the manifest."""
        entry = self.get_manifest().get(f"{emulator}:{game_id}", {})
        remote_crc = entry.get("crc32", "")
        if not remote_crc or not local_crc32:
            return False, remote_crc
        mismatch = remote_crc.lower() != local_crc32.lower()
        if mismatch:
            logger.warning(
                "CRC32 mismatch for {}:{} — local={} remote={} (different game version?)",
                emulator, game_id, local_crc32, remote_crc,
            )
        return mismatch, remote_crc

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_conflict(
        self, emulator: str, game_id: str, local_zip: Path, local_hash: str,
        rel_zip: str, remote_hash: str, remote_meta_data: bytes | None,
    ) -> ConflictInfo:
        try:
            local_mtime = datetime.fromtimestamp(local_zip.stat().st_mtime)
        except OSError:
            local_mtime = datetime.now()
        remote_mtime = self._backend.mtime(rel_zip) or datetime.now()
        return ConflictInfo(
            game_id=game_id,
            emulator=emulator,
            relative_path=local_zip.name,
            local_path=local_zip,
            remote_path=Path(rel_zip),
            local_mtime=local_mtime,
            remote_mtime=remote_mtime,
            local_hash=local_hash,
            remote_hash=remote_hash,
            remote_machine=self._meta_field(remote_meta_data, "source_machine"),
            remote_rel=rel_zip,
        )

    def _content_hash(self, zip_path: Path) -> str:
        """Content hash of a local backup (cached sidecar value, else computed)."""
        cached = self._read_meta_field(zip_path.with_suffix(".json"), "content_hash")
        if cached:
            return cached
        return zip_content_hash(zip_path)

    def _content_hash_remote(self, zip_data: bytes | None, meta_data: bytes | None) -> str:
        cached = self._meta_field(meta_data, "content_hash")
        if cached:
            return cached
        return zip_content_hash_bytes(zip_data) if zip_data else ""

    @staticmethod
    def _emit_progress(
        callback: SyncProgressCallback | None,
        operation: str,
        emulator: str,
        game_id: str,
        file_name: str,
        current: int,
        total: int | None,
    ) -> None:
        if callback:
            callback(SyncProgress(
                operation=operation,
                emulator=emulator,
                game_id=game_id,
                file_name=file_name,
                current=current,
                total=total or 0,
            ))

    def _transfer_progress(
        self,
        callback: SyncProgressCallback | None,
        operation: str,
        emulator: str,
        game_id: str,
        file_name: str,
    ) -> Callable[[int, int | None], None] | None:
        if callback is None:
            return None

        def _callback(current: int, total: int | None) -> None:
            self._emit_progress(
                callback, operation, emulator, game_id, file_name, current, total,
            )

        return _callback

    @staticmethod
    def _file_sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(TRANSFER_CHUNK_SIZE):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _meta_field(data: bytes | None, field_name: str) -> str:
        if not data:
            return ""
        try:
            return json.loads(data).get(field_name, "")
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _read_meta_field(meta_path: Path, field_name: str) -> str:
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    return json.load(f).get(field_name, "")
            except Exception:  # noqa: BLE001
                pass
        return ""
