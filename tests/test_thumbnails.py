"""Save-state thumbnail extraction (Phase 1: PCSX2 embedded + RetroArch sibling)."""

from __future__ import annotations

import zipfile

from app.plugins.pcsx2.plugin import PCSX2Plugin
from app.plugins.retroarch.plugin import RetroArchPlugin
from app.plugins.snes9x.plugin import Snes9xPlugin

_PNG = b"\x89PNG\r\n\x1a\nFAKE-SCREENSHOT-BYTES"


# ---- PCSX2: screenshot embedded in the .p2s zip ----

def test_pcsx2_extracts_embedded_screenshot(tmp_path):
    p2s = tmp_path / "SLPS-25733 (ABC).00.p2s"
    with zipfile.ZipFile(p2s, "w") as zf:
        zf.writestr("Version.txt", "1")
        zf.writestr("Screenshot.png", _PNG)
        zf.writestr("eeMemory.bin", b"\x00" * 32)
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
