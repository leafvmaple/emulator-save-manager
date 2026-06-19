"""Plugin discovery and pure parsing helpers."""

from __future__ import annotations

import pytest

EXPECTED = {"PCSX2", "Mesen", "Snes9x", "Citra", "Dolphin", "melonDS"}


def test_all_plugins_discovered():
    """Every shipped plugin (incl. dynamically imported ones) is found.

    This guards the packaging path: plugins are loaded via importlib, which
    is exactly what the v0.3.0 build fix had to account for.
    """
    from app.plugins.plugin_manager import PluginManager

    pm = PluginManager()
    pm.discover()
    assert EXPECTED <= set(pm.get_plugin_names())


def test_each_plugin_exposes_required_api():
    from app.plugins.plugin_manager import PluginManager

    pm = PluginManager()
    pm.discover()
    for plugin in pm.get_all_plugins():
        assert isinstance(plugin.name, str) and plugin.name
        assert isinstance(plugin.display_name, str) and plugin.display_name
        assert isinstance(plugin.supported_platforms, list)


def test_game_plugin_registry_keeps_rom_plugins_separate():
    from app.plugins.base import GamePlugin
    from app.plugins.plugin_manager import PluginManager

    class FakeGamePlugin(GamePlugin):
        @property
        def name(self):
            return "fake"

        @property
        def display_name(self):
            return "Fake Platform"

        @property
        def platform(self):
            return "fake-platform"

        def get_rom_extensions(self):
            return [".fake"]

    pm = PluginManager()
    plugin = FakeGamePlugin()
    pm.register_game_plugin(plugin)

    assert pm.get_game_plugin("fake") is plugin
    assert pm.get_game_plugin("fake-platform") is plugin
    assert pm.get_plugin("fake") is None


@pytest.mark.parametrize(
    "filename, serial, crc",
    [
        ("SLPS-25733 (083F0E03)", "SLPS-25733", "083F0E03"),
        ("SLUS-21005 (DEADBEEF)", "SLUS-21005", "DEADBEEF"),
    ],
)
def test_pcsx2_savestate_filename_parse(filename, serial, crc):
    from app.plugins.pcsx2.plugin import _SAVESTATE_RE

    m = _SAVESTATE_RE.match(filename)
    assert m is not None
    assert m.group(1) == serial
    assert m.group(2).upper() == crc


@pytest.mark.parametrize(
    "dirname, expected",
    [
        ("BISLPS-25733OGS", "SLPS-25733"),
        ("BASLUS-21005INGS001", "SLUS-21005"),
        ("BESLES-12345SAVE00", "SLES-12345"),
    ],
)
def test_pcsx2_serial_extraction(dirname, expected):
    from app.plugins.pcsx2.plugin import _extract_game_id_from_dirname

    assert _extract_game_id_from_dirname(dirname) == expected
