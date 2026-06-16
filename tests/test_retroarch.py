"""RetroArch plugin — multi-core save detection and scanning."""

from __future__ import annotations

import pytest

from app.models.emulator import EmulatorInfo
from app.models.game_save import SaveType
from app.plugins.retroarch.plugin import RetroArchPlugin, _STATE_RE


def _emu(dp):
    return EmulatorInfo(
        name="RetroArch", install_path=dp, data_path=dp,
        supported_platforms=["Multi-System"],
    )


def _data_dir(tmp_path, saves=None, states=None):
    dp = tmp_path / "RetroArch"
    (dp / "saves").mkdir(parents=True)
    (dp / "states").mkdir(parents=True)
    for name, content in (saves or {}).items():
        (dp / "saves" / name).write_bytes(content)
    for name, content in (states or {}).items():
        (dp / "states" / name).write_bytes(content)
    return dp


def test_retroarch_is_discovered():
    from app.plugins.plugin_manager import PluginManager
    pm = PluginManager()
    pm.discover()
    assert "RetroArch" in pm.get_plugin_names()


@pytest.mark.parametrize(
    "name, base",
    [
        ("Sonic.state", "Sonic"),
        ("Sonic.state1", "Sonic"),
        ("Sonic.state9", "Sonic"),
        ("Sonic.state.auto", "Sonic"),
        ("My.Game.state2", "My.Game"),
    ],
)
def test_state_filename_parsing(name, base):
    m = _STATE_RE.match(name)
    assert m and m.group(1) == base


def test_scan_groups_battery_and_states(tmp_path):
    dp = _data_dir(
        tmp_path,
        saves={"Sonic.srm": b"S", "Mario.srm": b"M"},
        states={"Sonic.state": b"1", "Sonic.state1": b"2"},
    )
    by_id = {s.game_id: s for s in RetroArchPlugin().scan_saves(_emu(dp))}
    assert set(by_id) == {"Sonic", "Mario"}
    assert len(by_id["Sonic"].save_files) == 3   # srm + 2 states
    assert len(by_id["Mario"].save_files) == 1
    types = {sf.save_type for sf in by_id["Sonic"].save_files}
    assert types == {SaveType.BATTERY, SaveType.SAVESTATE}


def test_non_save_files_are_ignored(tmp_path):
    dp = _data_dir(tmp_path, saves={"Sonic.srm": b"S", "Sonic.cht": b"x", "ui.cfg": b"y"})
    by_id = {s.game_id: s for s in RetroArchPlugin().scan_saves(_emu(dp))}
    assert set(by_id) == {"Sonic"}
    assert len(by_id["Sonic"].save_files) == 1


def test_per_core_subfolder_sets_platform(tmp_path):
    dp = tmp_path / "RA"
    (dp / "saves" / "Snes9x").mkdir(parents=True)
    (dp / "saves" / "Snes9x" / "Zelda.srm").write_bytes(b"Z")
    z = next(s for s in RetroArchPlugin().scan_saves(_emu(dp)) if s.game_id == "Zelda")
    assert z.platform == "Snes9x"


def test_cfg_savefile_override_colon_alias(tmp_path):
    dp = tmp_path / "RA"
    dp.mkdir()
    (dp / "customsaves").mkdir()
    (dp / "customsaves" / "Game.srm").write_bytes(b"G")
    (dp / "retroarch.cfg").write_text(
        'savefile_directory = ":\\customsaves"\n'
        'savestate_directory = "default"\n',
        encoding="utf-8",
    )
    plugin = RetroArchPlugin()
    dirs = plugin.get_save_directories(_emu(dp))
    assert dirs["saves"] == dp / "customsaves"
    assert dirs["savestates"] == dp / "states"  # "default" → built-in
    assert any(s.game_id == "Game" for s in plugin.scan_saves(_emu(dp)))


def test_cfg_savefile_override_absolute(tmp_path):
    dp = tmp_path / "RA"
    dp.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (dp / "retroarch.cfg").write_text(
        f'savefile_directory = "{elsewhere}"\n', encoding="utf-8",
    )
    dirs = RetroArchPlugin().get_save_directories(_emu(dp))
    assert dirs["saves"] == elsewhere


def test_detect_via_extra_paths(tmp_path):
    dp = tmp_path / "portable-ra"
    (dp / "saves").mkdir(parents=True)
    infos = RetroArchPlugin().detect_installation([dp])
    assert any(i.data_path == dp for i in infos)


def test_detect_linux(tmp_path, monkeypatch):
    from app.core import path_resolver as pr
    cfg = tmp_path / ".config"
    (cfg / "retroarch" / "saves").mkdir(parents=True)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr(pr, "_cache", {})
    monkeypatch.setattr(pr, "get_xdg_config_dir", lambda: cfg)
    monkeypatch.setattr(pr, "get_xdg_data_dir", lambda: tmp_path / ".local" / "share")

    infos = RetroArchPlugin().detect_installation(None)
    assert any(i.data_path.name == "retroarch" for i in infos)
