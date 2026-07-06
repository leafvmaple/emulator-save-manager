"""Game icon provider — cover resolution, caching, thumbnails, robustness."""

from __future__ import annotations

import urllib.request
from pathlib import Path

from app.core.game_icon import GameIconProvider, get_plugin_icon


def _write_img(path: Path, n: int = 600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n" + b"x" * n)


def test_get_icon_path_cache_hit(tmp_path):
    prov = GameIconProvider(tmp_path / "icons")
    _write_img(prov._cache_dir / "SLPS-1.jpg")
    p = prov.get_icon_path("PCSX2", "SLPS-1")
    assert p is not None and p.name == "SLPS-1.jpg"


def test_get_icon_path_from_covers_dir_copies_to_cache(tmp_path):
    data = tmp_path / "pcsx2"
    _write_img(data / "covers" / "SLPS-2.png")
    prov = GameIconProvider(tmp_path / "icons")
    prov.register_emulator("PCSX2", data)
    assert prov.get_icon_path("PCSX2", "SLPS-2") is not None
    assert (prov._cache_dir / "SLPS-2.png").is_file()   # copied into the cache


def test_get_icon_path_miss(tmp_path):
    prov = GameIconProvider(tmp_path / "icons")
    assert prov.get_icon_path("PCSX2", "NOPE") is None


def test_download_cover_non_serial_no_network(tmp_path):
    # Not a PS2 serial and no resolver -> None, without touching the network.
    prov = GameIconProvider(tmp_path / "icons")
    assert prov.download_cover("CHRONO") is None


def test_download_cover_uses_resolver(tmp_path, monkeypatch):
    prov = GameIconProvider(tmp_path / "icons")
    prov.register_cover_resolver("Snes9x", lambda gid: f"http://x/{gid}.png")

    def fake_dl(url, dest, label=""):
        dest.write_bytes(b"img")
        return dest

    monkeypatch.setattr(prov, "_download_image", fake_dl)
    p = prov.download_cover("CT", emulator="Snes9x")
    assert p is not None and p.suffix == ".png"


def test_download_image_handles_network_error(tmp_path, monkeypatch):
    prov = GameIconProvider(tmp_path / "icons")

    def boom(*a, **k):
        raise OSError("offline / timeout / 401")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert prov._download_image("http://x/y.jpg", prov._cache_dir / "y.jpg") is None


def test_download_cover_negative_cache_skips_repeated_misses(tmp_path, monkeypatch):
    prov = GameIconProvider(tmp_path / "icons")
    prov.register_cover_resolver("Citra", lambda gid: [f"http://x/{gid}.png"])
    calls = 0

    def miss(url, dest, label=""):
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(prov, "_download_image", miss)

    assert prov.download_cover("0004000000170F00", emulator="Citra") is None
    assert prov.download_cover("0004000000170F00", emulator="Citra") is None
    assert calls == 1


def test_download_image_rejects_tiny_response(tmp_path, monkeypatch):
    prov = GameIconProvider(tmp_path / "icons")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"tiny"   # < 500 bytes -> treated as an error page, not an image

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert prov._download_image("http://x/y.jpg", prov._cache_dir / "y.jpg") is None


def test_extract_thumbnail_writes_cache(tmp_path):
    prov = GameIconProvider(tmp_path / "icons")
    prov.register_thumbnail_extractor("PCSX2", lambda p: b"PNGDATA")
    dest = prov.extract_thumbnail("PCSX2", "G", ["a.p2s"])
    assert dest is not None and dest.read_bytes() == b"PNGDATA"


def test_extract_thumbnail_no_extractor(tmp_path):
    prov = GameIconProvider(tmp_path / "icons")
    assert prov.extract_thumbnail("PCSX2", "G", ["a.p2s"]) is None


def test_extract_thumbnail_extractor_raising_is_safe(tmp_path):
    prov = GameIconProvider(tmp_path / "icons")

    def boom(p):
        raise RuntimeError("corrupt save-state")

    prov.register_thumbnail_extractor("PCSX2", boom)
    assert prov.extract_thumbnail("PCSX2", "G", ["a.p2s"]) is None   # no crash


def test_get_plugin_icon(qapp):
    pm = get_plugin_icon("PCSX2", 36)
    assert pm is not None and not pm.isNull()
    assert get_plugin_icon("NoSuchEmulator", 36) is None
    assert get_plugin_icon("PCSX2", 36) is get_plugin_icon("PCSX2", 36)   # cached
