"""Portable path placeholder round-trips."""

from __future__ import annotations

import pytest

from app.core import path_resolver as pr


@pytest.fixture
def fake_roots(tmp_path, monkeypatch):
    """Pin the well-known directory lookups to a temp layout."""
    home = tmp_path / "home"
    docs = home / "Documents"
    docs.mkdir(parents=True)
    monkeypatch.setattr(pr, "_cache", {})
    monkeypatch.setattr(pr, "get_documents_dir", lambda: docs)
    monkeypatch.setattr(pr, "get_home_dir", lambda: home)
    monkeypatch.setattr(pr, "get_appdata_dir", lambda: home / "AppData" / "Roaming")
    monkeypatch.setattr(pr, "get_localappdata_dir", lambda: home / "AppData" / "Local")
    return home, docs


def test_documents_roundtrip(fake_roots):
    _, docs = fake_roots
    abs_path = docs / "PCSX2" / "memcards" / "Mcd001.ps2"
    portable = pr.to_portable_path(abs_path)
    assert portable.startswith("${DOCUMENTS}/")
    assert pr.resolve_path(portable) == abs_path


def test_emu_data_placeholder_roundtrip(tmp_path):
    emu = tmp_path / "emu"
    save = emu / "sstates" / "game.p2s"
    portable = pr.to_portable_path(save, emu)
    assert portable == "${EMU_DATA}/sstates/game.p2s"
    assert pr.resolve_path(portable, emu) == save


def test_emu_data_takes_priority_over_documents(fake_roots):
    """A save under the emulator dir uses ${EMU_DATA}, not ${DOCUMENTS}."""
    _, docs = fake_roots
    emu = docs / "PCSX2"
    save = emu / "memcards" / "Mcd001.ps2"
    portable = pr.to_portable_path(save, emu)
    assert portable.startswith("${EMU_DATA}/")
    assert pr.resolve_path(portable, emu) == save


def test_portable_path_is_reversible(tmp_path):
    """Whatever placeholder (if any) is applied, the round-trip is lossless."""
    weird = tmp_path / "somewhere" / "else.bin"
    portable = pr.to_portable_path(weird)
    assert pr.resolve_path(portable) == weird
