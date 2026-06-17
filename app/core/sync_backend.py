"""Pluggable remote-storage backends for sync.

The sync engine talks to the remote through a :class:`SyncBackend` whose
operations are all relative to a single sync root.  Two implementations ship:

- :class:`LocalFolderBackend` — a local/shared folder (OneDrive, 坚果云 client,
  Google Drive, …) kept in sync by an external client.  This is the original
  behaviour.
- :class:`WebDavBackend` — a WebDAV server (Nextcloud, 坚果云 WebDAV, Synology,
  …) the app talks to directly over HTTP.
"""

from __future__ import annotations

import io
import shutil
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from loguru import logger

SYNC_ROOT_DIR = "emulator-save-manager"


class SyncBackend(ABC):
    """Remote storage abstraction. All ``rel`` paths are relative to the sync
    root and use forward slashes (e.g. ``"PCSX2/SLUS-123/2020.zip"``)."""

    @property
    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def exists(self, rel: str) -> bool: ...

    @abstractmethod
    def list_dir(self, rel: str) -> list[str]:
        """Return entry names directly under *rel* (files and sub-dirs)."""

    @abstractmethod
    def read_bytes(self, rel: str) -> bytes | None: ...

    @abstractmethod
    def write_bytes(self, rel: str, data: bytes) -> None:
        """Write *data* to *rel*, creating parent directories as needed."""

    @abstractmethod
    def delete(self, rel: str) -> None: ...

    def mtime(self, rel: str) -> datetime | None:
        return None

    def test_connection(self) -> tuple[bool, str]:
        return (self.is_configured, "OK" if self.is_configured else "Not configured")


# ----------------------------------------------------------------------
# Local / shared folder
# ----------------------------------------------------------------------

class LocalFolderBackend(SyncBackend):
    def __init__(self, sync_folder: Path | str | None) -> None:
        self._folder = Path(sync_folder) if sync_folder else None

    @property
    def _root(self) -> Path | None:
        if not self._folder or not self._folder.exists():
            return None
        return self._folder / SYNC_ROOT_DIR

    def _p(self, rel: str) -> Path | None:
        root = self._root
        return (root / rel) if root is not None else None

    @property
    def is_configured(self) -> bool:
        return bool(self._folder) and self._folder.exists()

    def exists(self, rel: str) -> bool:
        p = self._p(rel)
        return bool(p and p.exists())

    def list_dir(self, rel: str) -> list[str]:
        p = self._p(rel)
        if not p or not p.is_dir():
            return []
        try:
            return [c.name for c in p.iterdir()]
        except OSError:
            return []

    def read_bytes(self, rel: str) -> bytes | None:
        p = self._p(rel)
        if not p or not p.is_file():
            return None
        try:
            return p.read_bytes()
        except OSError:
            return None

    def write_bytes(self, rel: str, data: bytes) -> None:
        p = self._p(rel)
        if p is None:
            raise RuntimeError("Sync folder not configured")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def delete(self, rel: str) -> None:
        p = self._p(rel)
        if not p or not p.exists():
            return
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                p.unlink()
            except OSError:
                pass

    def mtime(self, rel: str) -> datetime | None:
        p = self._p(rel)
        if p and p.exists():
            try:
                return datetime.fromtimestamp(p.stat().st_mtime)
            except OSError:
                return None
        return None

    def test_connection(self) -> tuple[bool, str]:
        if not self._folder:
            return (False, "No sync folder set")
        if not self._folder.exists():
            return (False, f"Folder not found: {self._folder}")
        return (True, "OK")


# ----------------------------------------------------------------------
# WebDAV
# ----------------------------------------------------------------------

class WebDavBackend(SyncBackend):
    def __init__(
        self,
        url: str,
        username: str = "",
        password: str = "",
        base_path: str = "",
        client=None,  # injectable for tests  # noqa: ANN001
    ) -> None:
        self._url = (url or "").rstrip("/")
        self._username = username or ""
        self._password = password or ""
        self._base = (base_path or "").strip("/")
        self._client_obj = client
        self._client_err = ""

    @property
    def _client(self):  # noqa: ANN201
        if self._client_obj is not None:
            return self._client_obj
        if not self._url:
            return None
        try:
            from webdav4.client import Client
            self._client_obj = Client(
                self._url, auth=(self._username, self._password),
            )
        except Exception as e:  # noqa: BLE001
            self._client_err = str(e)
            logger.error("WebDAV client init failed: {}", e)
            return None
        return self._client_obj

    def _remote(self, rel: str) -> str:
        parts = [self._base, SYNC_ROOT_DIR, (rel or "").strip("/")]
        return "/".join(p for p in parts if p)

    @property
    def is_configured(self) -> bool:
        return bool(self._url and self._username)

    def exists(self, rel: str) -> bool:
        c = self._client
        if c is None:
            return False
        try:
            return bool(c.exists(self._remote(rel)))
        except Exception as e:  # noqa: BLE001
            logger.debug("WebDAV exists({}) failed: {}", rel, e)
            return False

    def list_dir(self, rel: str) -> list[str]:
        c = self._client
        if c is None:
            return []
        path = self._remote(rel)
        try:
            if not c.exists(path):
                return []
            names: list[str] = []
            self_name = path.rstrip("/").rsplit("/", 1)[-1]
            for entry in c.ls(path, detail=False):
                name = str(entry).rstrip("/").rsplit("/", 1)[-1]
                if name and name != self_name:
                    names.append(name)
            return names
        except Exception as e:  # noqa: BLE001
            logger.debug("WebDAV list_dir({}) failed: {}", rel, e)
            return []

    def read_bytes(self, rel: str) -> bytes | None:
        c = self._client
        if c is None:
            return None
        try:
            buf = io.BytesIO()
            c.download_fileobj(self._remote(rel), buf)
            return buf.getvalue()
        except Exception as e:  # noqa: BLE001
            logger.debug("WebDAV read_bytes({}) failed: {}", rel, e)
            return None

    def write_bytes(self, rel: str, data: bytes) -> None:
        c = self._client
        if c is None:
            raise RuntimeError("WebDAV not configured")
        path = self._remote(rel)
        self._ensure_parent(path)
        c.upload_fileobj(io.BytesIO(data), path, overwrite=True)

    def _ensure_parent(self, remote_path: str) -> None:
        # Create each ancestor collection top-down via MKCOL, ignoring errors.
        # We must NOT gate on exists(): some servers (e.g. Nutstore/坚果云)
        # answer PROPFIND on a missing path with 409, which webdav4 re-raises —
        # so an exists()-first check would skip mkdir and the dir is never made.
        if "/" not in remote_path:
            return
        parent = remote_path.rsplit("/", 1)[0]
        c = self._client
        cur = ""
        for seg in parent.split("/"):
            cur = f"{cur}/{seg}" if cur else seg
            try:
                c.mkdir(cur)
            except Exception:  # noqa: BLE001 - already exists (405) / created above
                pass

    def delete(self, rel: str) -> None:
        c = self._client
        if c is None:
            return
        try:
            path = self._remote(rel)
            if c.exists(path):
                c.remove(path)
        except Exception as e:  # noqa: BLE001
            logger.debug("WebDAV delete({}) failed: {}", rel, e)

    def mtime(self, rel: str) -> datetime | None:
        c = self._client
        if c is None:
            return None
        try:
            return c.modified(self._remote(rel))
        except Exception:  # noqa: BLE001
            return None

    def test_connection(self) -> tuple[bool, str]:
        if not self.is_configured:
            return (False, "URL and username are required")
        c = self._client
        if c is None:
            return (False, self._client_err or "Could not create WebDAV client")
        try:
            # PROPFIND the server root — it always exists, so this validates the
            # URL + credentials. Probing a not-yet-created deep path (the base
            # path is created on first push) would 409 on servers like Nutstore
            # (PROPFIND-on-missing), which is the bug this avoids.
            c.ls("", detail=False)
            return (True, "OK")
        except Exception as e:  # noqa: BLE001
            return (False, str(e))


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------

def make_backend(config) -> SyncBackend:  # noqa: ANN001
    """Build the configured sync backend (``folder`` by default)."""
    kind = config.get("sync_backend", "folder")
    if kind == "webdav":
        from app.core.credentials import get_webdav_password
        return WebDavBackend(
            url=config.get("webdav_url", ""),
            username=config.get("webdav_username", ""),
            password=get_webdav_password(),
            base_path=config.get("webdav_base_path", ""),
        )
    return LocalFolderBackend(config.sync_folder)
