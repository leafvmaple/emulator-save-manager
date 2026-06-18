"""Save-state thumbnail extraction (Phase 1: PCSX2 embedded + RetroArch sibling)."""

from __future__ import annotations

import io
import json
import os
import zipfile

from app.core.backup import BackupManager
from app.core.state_thumbnail import read_backup_thumbnail
from app.plugins.citra.plugin import CitraPlugin
from app.plugins.pcsx2.plugin import PCSX2Plugin
from app.plugins.retroarch.plugin import RetroArchPlugin
from app.plugins.snes9x.plugin import Snes9xPlugin

_PNG = b"\x89PNG\r\n\x1a\nFAKE-SCREENSHOT-BYTES"
_PNG_NEWER = b"\x89PNG\r\n\x1a\nNEWER-SCREENSHOT-BYTES"


def _p2s_bytes(png: bytes = _PNG) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Version.txt", "1")
        zf.writestr("Screenshot.png", png)
        zf.writestr("eeMemory.bin", b"\x00" * 32)
    return buf.getvalue()


# ---- PCSX2: screenshot embedded in the .p2s zip ----

def test_pcsx2_extracts_embedded_screenshot(tmp_path):
    p2s = tmp_path / "SLPS-25733 (ABC).00.p2s"
    p2s.write_bytes(_p2s_bytes())
    assert PCSX2Plugin().get_state_thumbnail(p2s) == _PNG


def test_pcsx2_no_png_returns_none(tmp_path):
    p2s = tmp_path / "x.p2s"
    with zipfile.ZipFile(p2s, "w") as zf:
        zf.writestr("Version.txt", "1")
    assert PCSX2Plugin().get_state_thumbnail(p2s) is None


def test_pcsx2_non_p2s_returns_none(tmp_path):
    f = tmp_path / "Mcd001.ps2"
    f.write_bytes(b"not a state")
    assert PCSX2Plugin().get_state_thumbnail(f) is None


def test_pcsx2_corrupt_zip_returns_none(tmp_path):
    f = tmp_path / "broken.p2s"
    f.write_bytes(b"this is not a zip file")
    assert PCSX2Plugin().get_state_thumbnail(f) is None


# ---- RetroArch: sibling <state>.png ----

def test_retroarch_reads_sibling_thumbnail(tmp_path):
    state = tmp_path / "Sonic.state1"
    state.write_bytes(b"STATE-DATA")
    (tmp_path / "Sonic.state1.png").write_bytes(_PNG)
    assert RetroArchPlugin().get_state_thumbnail(state) == _PNG


def test_retroarch_no_sibling_returns_none(tmp_path):
    state = tmp_path / "Sonic.state1"
    state.write_bytes(b"STATE-DATA")
    assert RetroArchPlugin().get_state_thumbnail(state) is None


# ---- Citra: .cst has no embedded screenshot; sibling image is supported ----

def test_citra_reads_sibling_thumbnail(tmp_path):
    state = tmp_path / "0004000000055E00.01.cst"
    state.write_bytes(b"CST\x1b" + b"\0" * 252 + b"ZSTD-DATA")
    (tmp_path / "0004000000055E00.01.cst.png").write_bytes(_PNG)

    assert CitraPlugin().get_state_thumbnail(state) == _PNG


def test_citra_cst_without_sibling_returns_none(tmp_path):
    state = tmp_path / "0004000000055E00.01.cst"
    state.write_bytes(b"CST\x1b" + b"\0" * 252 + b"ZSTD-DATA")

    assert CitraPlugin().get_state_thumbnail(state) is None


# ---- base default: plugins without support return None ----

def test_plugin_without_thumbnail_support_returns_none(tmp_path):
    f = tmp_path / "game.000"
    f.write_bytes(b"x")
    assert Snes9xPlugin().get_state_thumbnail(f) is None


# ---- provider: extraction caches the thumbnail as the game's icon ----

def test_provider_extracts_and_caches_thumbnail(tmp_path):
    from app.core.game_icon import GameIconProvider

    state = tmp_path / "Sonic.state1"
    state.write_bytes(b"STATE")
    (tmp_path / "Sonic.state1.png").write_bytes(_PNG)

    prov = GameIconProvider(tmp_path / "icons")
    prov.register_thumbnail_extractor("RetroArch", RetroArchPlugin().get_state_thumbnail)

    path = prov.extract_thumbnail("RetroArch", "Sonic", [state])
    assert path is not None and path.read_bytes() == _PNG
    # Cached → a subsequent icon look-up finds it without re-extracting.
    assert prov.get_icon_path("RetroArch", "Sonic") is not None


def test_provider_extract_without_extractor_returns_none(tmp_path):
    from app.core.game_icon import GameIconProvider

    prov = GameIconProvider(tmp_path / "icons")
    assert prov.extract_thumbnail("Nope", "G", [tmp_path / "x"]) is None


# ---- backups: screenshots travel with backup versions ----

def test_backup_embeds_savestate_thumbnail(cfg, make_game_save, tmp_path):
    gs = make_game_save(tmp_path / "states", files={"slot0.p2s": _p2s_bytes()})

    record = BackupManager(cfg).create_backup([gs])

    meta = json.loads(record.backup_path.with_suffix(".json").read_text(encoding="utf-8"))
    thumb_path = meta["backup_paths"][0]["thumbnail_zip_path"]
    assert thumb_path.startswith("thumbnails/")

    with zipfile.ZipFile(record.backup_path, "r") as zf:
        assert zf.read(thumb_path) == _PNG
    assert read_backup_thumbnail(record.backup_path) == _PNG


def test_backup_embeds_citra_sibling_thumbnail(cfg, make_game_save, tmp_path):
    gs = make_game_save(
        tmp_path / "states",
        emulator="Citra",
        game_id="0004000000055E00",
        files={"0004000000055E00.01.cst": b"CST\x1b" + b"\0" * 252 + b"ZSTD-DATA"},
    )
    (tmp_path / "states" / "0004000000055E00.01.cst.png").write_bytes(_PNG)

    record = BackupManager(cfg).create_backup([gs])

    assert read_backup_thumbnail(record.backup_path) == _PNG


def test_backup_preview_prefers_newest_savestate_thumbnail(
    cfg, make_game_save, tmp_path
):
    gs = make_game_save(
        tmp_path / "states",
        files={
            "slot0.p2s": _p2s_bytes(_PNG),
            "slot9.p2s": _p2s_bytes(_PNG_NEWER),
        },
    )
    old_ts = 1_700_000_000
    new_ts = 1_700_000_900
    for sf in gs.save_files:
        ts = new_ts if sf.path.name == "slot9.p2s" else old_ts
        os.utime(sf.path, (ts, ts))

    record = BackupManager(cfg).create_backup([gs])

    meta = json.loads(record.backup_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert all(bp.get("modified_time") for bp in meta["backup_paths"])
    assert read_backup_thumbnail(record.backup_path) == _PNG_NEWER


def test_read_backup_thumbnail_falls_back_to_archived_p2s(tmp_path):
    zip_path = tmp_path / "old.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("savestate/slot0.p2s", _p2s_bytes())

    zip_path.with_suffix(".json").write_text(json.dumps({
        "backup_paths": [{
            "type": "savestate",
            "zip_path": "savestate/slot0.p2s",
            "is_dir": False,
        }],
    }), encoding="utf-8")

    assert read_backup_thumbnail(zip_path) == _PNG
