"""Scanner — detection + scanning orchestration across plugins."""

from __future__ import annotations

from pathlib import Path

from app.core.scanner import Scanner
from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave


class _FakePlugin:
    def __init__(self, name, emus=None, saves=None, raise_detect=False, raise_scan=False):
        self.name = name
        self.display_name = name
        self._emus = emus or []
        self._saves = saves or []
        self._raise_detect = raise_detect
        self._raise_scan = raise_scan
        self.scanned_with = None

    def detect_installation(self, extra=None):
        if self._raise_detect:
            raise RuntimeError("detect boom")
        return list(self._emus)

    def scan_saves(self, emu_info, custom_paths=None):
        self.scanned_with = (emu_info, custom_paths)
        if self._raise_scan:
            raise RuntimeError("scan boom")
        return list(self._saves)


class _FakePM:
    def __init__(self, plugins):
        self._plugins = {p.name: p for p in plugins}

    def get_all_plugins(self):
        return list(self._plugins.values())

    def get_plugin(self, name):
        return self._plugins.get(name)


def _emu(name, data="C:/d"):
    return EmulatorInfo(name=name, install_path=Path("C:/i"),
                        data_path=Path(data), supported_platforms=["PS2"])


def _save(emu, gid):
    return GameSave(emulator=emu, game_name=gid, game_id=gid, platform="PS2")


def test_full_scan_detects_and_scans(cfg):
    p = _FakePlugin("PCSX2", emus=[_emu("PCSX2")], saves=[_save("PCSX2", "SLPS-1")])
    emus, saves = Scanner(_FakePM([p]), cfg).full_scan()
    assert [e.name for e in emus] == ["PCSX2"]
    assert [s.game_id for s in saves] == ["SLPS-1"]


def test_failing_plugin_does_not_abort_scan(cfg):
    good = _FakePlugin("Good", emus=[_emu("Good")], saves=[_save("Good", "G1")])
    bad = _FakePlugin("Bad", raise_detect=True)
    emus, saves = Scanner(_FakePM([bad, good]), cfg).full_scan()
    assert [e.name for e in emus] == ["Good"]   # bad plugin swallowed
    assert [s.game_id for s in saves] == ["G1"]


def test_scan_exception_is_swallowed(cfg):
    p = _FakePlugin("PCSX2", emus=[_emu("PCSX2")], raise_scan=True)
    emus, saves = Scanner(_FakePM([p]), cfg).full_scan()
    assert [e.name for e in emus] == ["PCSX2"]
    assert saves == []


def test_data_path_attached_to_saves(cfg):
    s = _save("PCSX2", "G")
    assert s.data_path is None
    p = _FakePlugin("PCSX2", emus=[_emu("PCSX2", data="C:/pcsx2data")], saves=[s])
    _, saves = Scanner(_FakePM([p]), cfg).full_scan()
    assert saves[0].data_path == Path("C:/pcsx2data")


def test_scan_resolves_display_name_from_rom_library(cfg, tmp_path):
    from app.data.rom_library import RomLibrary
    from app.models.rom_entry import RomEntry, RomInfo

    lib = RomLibrary(cfg.data_dir)
    lib.add(
        RomEntry(
            rom_path=str(tmp_path / "Long Rom Name.nds"),
            platform="nds",
            game_id="NTR-TEST",
            rom_info=RomInfo(title_name_zh="真实游戏名"),
        )
    )
    lib.save()

    save = GameSave(
        emulator="melonDS",
        game_name="Long Rom Name - Copy",
        game_id="Long Rom Name - Copy",
        platform="NDS",
    )
    p = _FakePlugin("melonDS", emus=[_emu("melonDS")], saves=[save])

    _, saves = Scanner(_FakePM([p]), cfg).full_scan()

    assert saves[0].game_id == "Long Rom Name - Copy"
    assert saves[0].game_name == "真实游戏名"


def test_cancel_before_detection(cfg):
    p = _FakePlugin("PCSX2", emus=[_emu("PCSX2")], saves=[_save("PCSX2", "G")])
    emus, saves = Scanner(_FakePM([p]), cfg).full_scan(should_cancel=lambda: True)
    assert emus == [] and saves == []


def test_cancel_between_phases(cfg):
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 1   # let detection run once, then cancel before scan

    p = _FakePlugin("PCSX2", emus=[_emu("PCSX2")], saves=[_save("PCSX2", "G")])
    emus, saves = Scanner(_FakePM([p]), cfg).full_scan(should_cancel=cancel)
    assert [e.name for e in emus] == ["PCSX2"]
    assert saves == []
