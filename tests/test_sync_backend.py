"""Sync backends — local folder + WebDAV (via an in-memory fake client)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.backup import BackupManager
from app.core.sync import SyncManager
from app.core.sync_backend import LocalFolderBackend, WebDavBackend, make_backend


# ----------------------------------------------------------------------
# Fake WebDAV client (in-memory)
# ----------------------------------------------------------------------

class FakeWebDavClient:
    """Minimal stand-in for webdav4.client.Client backed by a dict."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    @staticmethod
    def _n(p: str) -> str:
        return p.strip("/")

    def exists(self, p: str) -> bool:
        p = self._n(p)
        return p in self.store or any(k.startswith(p + "/") for k in self.store)

    def ls(self, p: str, detail: bool = False):
        p = self._n(p)
        children = set()
        for k in self.store:
            if k.startswith(p + "/"):
                children.add(p + "/" + k[len(p) + 1:].split("/", 1)[0])
        out = sorted(children)
        out.append(p)  # WebDAV servers include the directory itself
        return out

    def upload_fileobj(self, fileobj, p: str, overwrite: bool = False) -> None:
        self.store[self._n(p)] = fileobj.read()

    def download_fileobj(self, p: str, fileobj) -> None:
        fileobj.write(self.store[self._n(p)])

    def mkdir(self, p: str) -> None:
        pass

    def remove(self, p: str) -> None:
        p = self._n(p)
        self.store.pop(p, None)
        for k in [k for k in self.store if k.startswith(p + "/")]:
            del self.store[k]

    def modified(self, p: str):
        from datetime import datetime
        return datetime.now()


class NutstoreLikeClient(FakeWebDavClient):
    """Like FakeWebDavClient, but PROPFIND on a missing path raises 409 (as
    Nutstore/坚果云 does) instead of behaving like a 404."""

    def exists(self, p: str) -> bool:
        p = self._n(p)
        if p == "" or p in self.store or any(k.startswith(p + "/") for k in self.store):
            return True
        raise RuntimeError("409 Conflict")

    def ls(self, p: str, detail: bool = False):
        n = self._n(p)
        if n and n not in self.store and not any(k.startswith(n + "/") for k in self.store):
            raise RuntimeError("404 Not Found")
        return super().ls(p, detail)


@dataclass
class _Cfg:
    backup_path: Path
    sync_folder: Path | None = None
    machine_id: str = "m"
    max_backups: int = 5


# ----------------------------------------------------------------------
# LocalFolderBackend
# ----------------------------------------------------------------------

def test_local_backend_roundtrip(tmp_path):
    b = LocalFolderBackend(tmp_path)
    assert b.is_configured
    assert not b.exists("a/b.zip")
    b.write_bytes("a/b.zip", b"DATA")
    assert b.exists("a/b.zip")
    assert b.read_bytes("a/b.zip") == b"DATA"
    assert b.list_dir("a") == ["b.zip"]
    assert b.mtime("a/b.zip") is not None
    b.delete("a/b.zip")
    assert not b.exists("a/b.zip")


def test_local_backend_unconfigured(tmp_path):
    b = LocalFolderBackend(tmp_path / "missing")
    assert not b.is_configured
    assert b.read_bytes("x") is None
    assert b.list_dir("x") == []
    ok, _ = b.test_connection()
    assert ok is False


# ----------------------------------------------------------------------
# WebDavBackend (fake client)
# ----------------------------------------------------------------------

def test_webdav_backend_roundtrip_and_paths():
    client = FakeWebDavClient()
    b = WebDavBackend("http://dav.example/", "user", "pw", base_path="games", client=client)
    assert b.is_configured

    b.write_bytes("PCSX2/SLUS-1/2020.zip", b"ZIPDATA")
    # base_path + root prefix is applied transparently.
    assert "games/emulator-save-manager/PCSX2/SLUS-1/2020.zip" in client.store
    assert b.exists("PCSX2/SLUS-1/2020.zip")
    assert b.read_bytes("PCSX2/SLUS-1/2020.zip") == b"ZIPDATA"
    assert b.list_dir("PCSX2/SLUS-1") == ["2020.zip"]
    assert b.read_bytes("nope.zip") is None
    b.delete("PCSX2/SLUS-1/2020.zip")
    assert not b.exists("PCSX2/SLUS-1/2020.zip")


def test_webdav_backend_not_configured_without_url():
    b = WebDavBackend("", "", "")
    assert not b.is_configured
    ok, msg = b.test_connection()
    assert ok is False and msg


# --- Nutstore-like server: PROPFIND-on-missing returns 409 (the reported bug) ---

def test_webdav_exists_swallows_propfind_409():
    b = WebDavBackend("http://x", "u", "p", client=NutstoreLikeClient())
    assert b.exists("not/there.zip") is False   # 409 must not propagate


def test_webdav_write_creates_dirs_despite_409():
    b = WebDavBackend("http://x", "u", "p", client=NutstoreLikeClient())
    # Must not gate dir creation on exists() (which 409s on a missing path).
    b.write_bytes("PCSX2/SLUS-1/2020.zip", b"DATA")
    assert b.read_bytes("PCSX2/SLUS-1/2020.zip") == b"DATA"


def test_webdav_test_connection_probes_root_not_missing_path():
    # The root exists, so the test must pass even though deep paths 409.
    b = WebDavBackend("http://x", "u", "p", client=NutstoreLikeClient())
    ok, msg = b.test_connection()
    assert ok is True, msg


def test_full_sync_over_nutstore_like_backend(tmp_path, make_game_save):
    client = NutstoreLikeClient()

    def machine(name):
        c = _Cfg(backup_path=tmp_path / f"{name}_b", machine_id=name)
        bm = BackupManager(c)
        sm = SyncManager(c, bm, backend=WebDavBackend("http://x", "u", "p", client=client))
        return bm, sm

    bmA, smA = machine("A")
    bmB, smB = machine("B")
    bmA.create_backup([make_game_save(tmp_path / "sa", files={"x.bin": b"HELLO"})])

    assert smA.push_all().pushed == 1               # dirs created despite 409s
    assert smB.pull_all().pulled == 1
    assert "PCSX2:SLUS-12345" in bmB.list_all_backups()


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------

def test_make_backend_selects_implementation(cfg):
    assert isinstance(make_backend(cfg), LocalFolderBackend)
    cfg.set("sync_backend", "webdav")
    cfg.set("webdav_url", "http://dav.example")
    cfg.set("webdav_username", "u")
    assert isinstance(make_backend(cfg), WebDavBackend)


# ----------------------------------------------------------------------
# End-to-end sync over a WebDAV-style backend
# ----------------------------------------------------------------------

def test_sync_over_webdav_backend(tmp_path, make_game_save):
    client = FakeWebDavClient()  # one shared remote

    def machine(name):
        c = _Cfg(backup_path=tmp_path / f"{name}_b", machine_id=name)
        bm = BackupManager(c)
        sm = SyncManager(c, bm, backend=WebDavBackend("http://x", "u", "p", client=client))
        return bm, sm

    bmA, smA = machine("A")
    bmB, smB = machine("B")

    bmA.create_backup([make_game_save(tmp_path / "sa", files={"x.bin": b"HELLO"})])
    assert smA.push_all().pushed == 1

    assert bmB.list_all_backups() == {}
    assert smB.pull_all().pulled == 1
    assert "PCSX2:SLUS-12345" in bmB.list_all_backups()

    # Re-pull is a no-op (identical content), not a conflict.
    res = smB.pull_all()
    assert res.pulled == 0 and not res.conflicts


def test_webdav_sync_conflict(tmp_path, make_game_save):
    client = FakeWebDavClient()

    def machine(name):
        c = _Cfg(backup_path=tmp_path / f"{name}_b", machine_id=name)
        bm = BackupManager(c)
        sm = SyncManager(c, bm, backend=WebDavBackend("http://x", "u", "p", client=client))
        return bm, sm

    bmA, smA = machine("A")
    bmB, smB = machine("B")

    bmA.create_backup([make_game_save(tmp_path / "sa", files={"x.bin": b"AAA"})])
    smA.push_all()
    bmB.create_backup([make_game_save(tmp_path / "sb", files={"x.bin": b"BBB"})])

    res = smB.pull_all()
    assert res.conflicts and res.conflicts[0].game_id == "SLUS-12345"
    assert res.conflicts[0].remote_rel.endswith(".zip")
