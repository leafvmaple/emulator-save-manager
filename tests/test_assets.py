"""Asset resolution — the app icon must ship and be locatable."""

from __future__ import annotations

from app.assets import app_icon_path, asset_path


def test_app_icon_exists():
    p = app_icon_path()
    assert p.name == "icon.png"
    assert p.is_file()
    assert p.stat().st_size > 0


def test_app_icon_ico_ships():
    ico = asset_path("app", "resources", "icon.ico")
    assert ico.is_file() and ico.stat().st_size > 0


def test_asset_path_fallback_for_missing():
    # A non-existent asset still returns a path (the source-tree fallback).
    p = asset_path("app", "resources", "does-not-exist.bin")
    assert p.name == "does-not-exist.bin"
