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
from typing import BinaryIO, Callable

from loguru import logger

SYNC_ROOT_DIR = "emulator-save-manager"
TRANSFER_CHUNK_SIZE = 1024 * 1024
TransferProgress = Callable[[int, int | None], None]


class _ProgressReader:
    """File-like reader that reports cumulative bytes read."""

    def __init__(
        self,
        fileobj: BinaryIO,
        total: int | None,
        progress_callback: TransferProgress | None,
    ) -> None:
        self._fileobj = fileobj
        self._total = total
        self._progress_callback = progress_callback
        self._done = 0

    def read(self, size: int = -1) -> bytes:
        data = self._fileobj.read(size)
        if data and self._progress_callback:
            self._done += len(data)
            self._progress_callback(self._done, self._total)
        return data

    def tell(self) -> int:
        return self._fileobj.tell()

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        pos = self._fileobj.seek(offset, whence)
        self._done = self._fileobj.tell()
        return pos

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def __getattr__(self, name: str):  # noqa: ANN204
        return getattr(self._fileobj, name)


class _ProgressWriter:
    """File-like writer that reports cumulative bytes written."""

    def __init__(
        self,
        fileobj: BinaryIO,
        total: int | None,
        progress_callback: TransferProgress | None,
    ) -> None:
        self._fileobj = fileobj
        self._total = total
        self._progress_callback = progress_callback
        self._done = 0

    def write(self, data: bytes) -> int:
        written = self._fileobj.write(data)
        if self._progress_callback:
            self._done += written if written is not None else len(data)
            self._progress_callback(self._done, self._total)
        return written

    def flush(self) -> None:
        self._fileobj.flush()

    def writable(self) -> bool:
        return True

    def __getattr__(self, name: str):  # noqa: ANN204
        return getattr(self._fileobj, name)


def _copy_file(
    source: Path,
    dest: Path,
    progress_callback: TransferProgress | None = None,
) -> None:
    total = source.stat().st_size
    done = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, dest.open("wb") as out:
        while chunk := src.read(TRANSFER_CHUNK_SIZE):
            out.write(chunk)
            done += len(chunk)
            if progress_callback:
                progress_callback(done, total)
    if progress_callback and total == 0:
        progress_callback(0, 0)


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
    def read_bytes(
        self,
        rel: str,
        progress_callback: TransferProgress | None = None,
    ) -> bytes | None: ...

    @abstractmethod
    def write_bytes(
        self,
        rel: str,
        data: bytes,
        progress_callback: TransferProgress | None = None,
    ) -> None:
        """Write *data* to *rel*, creating parent directories as needed."""

    @abstractmethod
    def delete(self, rel: str) -> None: ...

    def size(self, rel: str) -> int | None:
        return None

    def read_file(
        self,
        rel: str,
        dest: Path,
        progress_callback: TransferProgress | None = None,
    ) -> bool:
        data = self.read_bytes(rel, progress_callback=progress_callback)
        if data is None:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True

    def write_file(
        self,
        rel: str,
        source: Path,
        progress_callback: TransferProgress | None = None,
    ) -> None:
        self.write_bytes(rel, source.read_bytes(), progress_callback=progress_callback)

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

    def read_bytes(
        self,
        rel: str,
        progress_callback: TransferProgress | None = None,
    ) -> bytes | None:
        p = self._p(rel)
        if not p or not p.is_file():
            return None
        try:
            data = p.read_bytes()
            if progress_callback:
                progress_callback(len(data), len(data))
            return data
        except OSError:
            return None

    def write_bytes(
        self,
        rel: str,
        data: bytes,
        progress_callback: TransferProgress | None = None,
    ) -> None:
        p = self._p(rel)
        if p is None:
            raise RuntimeError("Sync folder not configured")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        if progress_callback:
            progress_callback(len(data), len(data))

    def read_file(
        self,
        rel: str,
        dest: Path,
        progress_callback: TransferProgress | None = None,
    ) -> bool:
        p = self._p(rel)
        if not p or not p.is_file():
            return False
        try:
            _copy_file(p, dest, progress_callback)
            return True
        except OSError:
            return False

    def write_file(
        self,
        rel: str,
        source: Path,
        progress_callback: TransferProgress | None = None,
    ) -> None:
        p = self._p(rel)
        if p is None:
            raise RuntimeError("Sync folder not configured")
        _copy_file(source, p, progress_callback)

    def size(self, rel: str) -> int | None:
        p = self._p(rel)
        if p and p.is_file():
            try:
                return p.stat().st_size
            except OSError:
                return None
        return None

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
            # Explicit per-phase timeout: httpx defaults to 5s, too short for
            # multi-MB memcard uploads on a slow link, yet we still want a dead
            # server to fail rather than hang the sync thread forever.
            self._client_obj = Client(
                self._url, auth=(self._username, self._password),
                timeout=30.0,   # forwarded to httpx (default 5s is too short for uploads)
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

    def read_bytes(
        self,
        rel: str,
        progress_callback: TransferProgress | None = None,
    ) -> bytes | None:
        c = self._client
        if c is None:
            return None
        try:
            buf = io.BytesIO()
            total = self.size(rel) if progress_callback else None
            c.download_fileobj(
                self._remote(rel),
                _ProgressWriter(buf, total, progress_callback),
            )
            return buf.getvalue()
        except Exception as e:  # noqa: BLE001
            logger.debug("WebDAV read_bytes({}) failed: {}", rel, e)
            return None

    def write_bytes(
        self,
        rel: str,
        data: bytes,
        progress_callback: TransferProgress | None = None,
    ) -> None:
        c = self._client
        if c is None:
            raise RuntimeError("WebDAV not configured")
        path = self._remote(rel)
        self._ensure_parent(path)
        c.upload_fileobj(
            _ProgressReader(io.BytesIO(data), len(data), progress_callback),
            path,
            overwrite=True,
        )

    def read_file(
        self,
        rel: str,
        dest: Path,
        progress_callback: TransferProgress | None = None,
    ) -> bool:
        c = self._client
        if c is None:
            return False
        tmp = dest.with_name(dest.name + ".tmp")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            total = self.size(rel) if progress_callback else None
            with tmp.open("wb") as f:
                c.download_fileobj(
                    self._remote(rel),
                    _ProgressWriter(f, total, progress_callback),
                )
            tmp.replace(dest)
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("WebDAV read_file({}) failed: {}", rel, e)
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            return False

    def write_file(
        self,
        rel: str,
        source: Path,
        progress_callback: TransferProgress | None = None,
    ) -> None:
        c = self._client
        if c is None:
            raise RuntimeError("WebDAV not configured")
        path = self._remote(rel)
        self._ensure_parent(path)
        total = source.stat().st_size
        with source.open("rb") as f:
            c.upload_fileobj(
                _ProgressReader(f, total, progress_callback),
                path,
                overwrite=True,
            )

    def size(self, rel: str) -> int | None:
        c = self._client
        if c is None:
            return None
        info_fn = getattr(c, "info", None)
        if not callable(info_fn):
            return None
        try:
            info = info_fn(self._remote(rel))
        except Exception:  # noqa: BLE001
            return None
        if isinstance(info, dict):
            for key in (
                "content_length",
                "content-length",
                "getcontentlength",
                "{DAV:}getcontentlength",
                "size",
            ):
                value = info.get(key)
                if value is not None:
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        return None
        return None

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
