"""WebDAV backend against a REAL local WebDAV server (wsgidav over HTTP).

Catches webdav4 ↔ server behaviour the in-memory fake can't (PROPFIND on the
root, MKCOL over HTTP, the Nutstore-style 409-on-missing path the test-connection
fix addresses). Skipped if wsgidav/cheroot aren't installed.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("wsgidav")
pytest.importorskip("cheroot")

from app.core.backup import BackupManager
from app.core.sync import SyncManager
from app.core.sync_backend import WebDavBackend
from app.models.game_save import GameSave, SaveFile, SaveType

_USER, _PASS = "alice", "secret"


@pytest.fixture
def webdav_server(tmp_path):
    from wsgidav.wsgidav_app import WsgiDAVApp
    from cheroot import wsgi

    serve_root = tmp_path / "dav_root"
    serve_root.mkdir()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = {
        "host": "127.0.0.1",
        "port": port,
        "provider_mapping": {"/dav": str(serve_root)},
        "simple_dc": {"user_mapping": {"*": {_USER: {"password": _PASS}}}},
        "verbose": 0,
        "logging": {"enable": False},
    }
    server = wsgi.Server(("127.0.0.1", port), WsgiDAVApp(config))
    threading.Thread(target=server.start, daemon=True).start()
    time.sleep(0.8)
    try:
        yield f"http://127.0.0.1:{port}/dav"
    finally:
        try:
            server.stop()
        except Exception:  # noqa: BLE001
            pass


def test_webdav_real_roundtrip(webdav_server):
    b = WebDavBackend(webdav_server, _USER, _PASS)

    ok, msg = b.test_connection()
    assert ok, msg

    assert b.exists("a/b/c.zip") is False
    b.write_bytes("a/b/c.zip", b"REALDATA")    # MKCOL the chain + PUT, over HTTP
    assert b.exists("a/b/c.zip")
    assert b.read_bytes("a/b/c.zip") == b"REALDATA"
    assert b.list_dir("a/b") == ["c.zip"]
    assert "a" in b.list_dir("")               # root listing
    b.delete("a/b/c.zip")
    assert b.exists("a/b/c.zip") is False


def test_webdav_real_bad_password(webdav_server):
    ok, msg = WebDavBackend(webdav_server, _USER, "wrong").test_connection()
    assert ok is False and msg


def test_webdav_real_full_sync(webdav_server, tmp_path):
    @dataclass
    class _Cfg:
        backup_path: Path
        machine_id: str = "m"
        sync_folder: Path | None = None
        max_backups: int = 5

    def _save(d: Path) -> GameSave:
        d.mkdir(parents=True, exist_ok=True)
        (d / "x.bin").write_bytes(b"HELLO")
        return GameSave(
            emulator="PCSX2", game_name="G", game_id="SLUS-9", platform="PS2",
            save_files=[SaveFile(path=d / "x.bin", save_type=SaveType.SAVESTATE,
                                 size=5, modified=datetime.now())],
        )

    bm_a = BackupManager(_Cfg(tmp_path / "A"))
    sm_a = SyncManager(_Cfg(tmp_path / "A"), bm_a,
                       backend=WebDavBackend(webdav_server, _USER, _PASS))
    bm_b = BackupManager(_Cfg(tmp_path / "B"))
    sm_b = SyncManager(_Cfg(tmp_path / "B"), bm_b,
                       backend=WebDavBackend(webdav_server, _USER, _PASS))

    bm_a.create_backup([_save(tmp_path / "sa")])
    assert sm_a.push_all().pushed == 1
    assert sm_b.pull_all().pulled == 1
    assert "PCSX2:SLUS-9" in bm_b.list_all_backups()
