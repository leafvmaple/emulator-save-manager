"""Tests for the save-info plugin system and the Metal Max plugin.

The Metal Max cases run against synthetic saves built from the documented
layout (record offsets, glyph table and the ROM checksum formula), so no
binary fixtures are needed; a real-save check runs only when the local
reference save exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.saveinfo.base import SaveInfoPlugin, SaveInfoResult
from app.saveinfo.manager import SaveInfoManager
from app.saveinfo.metal_max import plugin as mm
from app.saveinfo.metal_max.charmap import CHARMAP, NAME_TERMINATOR
from app.saveinfo.metal_max.plugin import MetalMaxSaveInfoPlugin

REVERSE = {glyph: code for code, glyph in CHARMAP.items()}


def encode_name(name: str, slot_size: int) -> bytes:
    data = bytearray([NAME_TERMINATOR] * slot_size)
    for i, ch in enumerate(name):
        data[i] = REVERSE[ch]
    return bytes(data)


def build_save(
    hero: str = "レバンナ",
    level: int = 12,
    cur_hp: int = 210,
    max_hp: int = 260,
    gold: int = 115_354,
    bank: int = 40_000,
    map_id: int = 0x0B,
    scroll: tuple[int, int] = (-3, 4),
    train: int = 1,
    tanks: tuple[str, ...] = ("M1A1",),
    chests: tuple[int, ...] = (0, 2, 90),
    flags: tuple[int, ...] = (0x11, 0x50, 0x51, 0x60),
    ailment: int = 0,
    files: tuple[int, ...] = (1,),
    active: int = 1,
) -> bytearray:
    """Assemble a checksum-valid Metal Max SRAM image from the layout."""
    data = bytearray(mm.SRAM_SIZE)
    for f in files:
        base = mm.FILE_BASE[f]
        data[base + mm.NAMES:base + mm.NAMES + mm.NAME_SLOT] = encode_name(
            hero, mm.NAME_SLOT
        )
        data[base + mm.LEVEL] = level
        data[base + mm.CUR_HP:base + mm.CUR_HP + 2] = cur_hp.to_bytes(2, "little")
        data[base + mm.MAX_HP:base + mm.MAX_HP + 2] = max_hp.to_bytes(2, "little")
        data[base + mm.TEAM_STATUS] = 0x01  # hero in party, position 1
        data[base + mm.AILMENT] = ailment
        data[base + mm.GOLD:base + mm.GOLD + 3] = gold.to_bytes(3, "little")
        data[base + mm.BANK_GOLD:base + mm.BANK_GOLD + 3] = bank.to_bytes(3, "little")
        data[base + mm.PLAYER_MAP] = map_id
        data[base + mm.PLAYER_SCROLL_X] = scroll[0] & 0xFF
        data[base + mm.PLAYER_SCROLL_Y] = scroll[1] & 0xFF
        data[base + mm.PLAYER_TRAIN] = train
        for i, name in enumerate(tanks):
            off = base + mm.TANK_NAMES + i * mm.TANK_NAME_SLOT
            data[off:off + mm.TANK_NAME_SLOT] = encode_name(name, mm.TANK_NAME_SLOT)
        for c in chests:
            data[base + mm.TREASURE + (c >> 3)] |= 1 << (c & 7)
        for fl in flags:
            data[base + mm.EVENT_FLAGS + (fl >> 3)] |= 1 << (fl & 7)
        data[mm.USED_MARKER[f]] = mm.MARKER_USED
        checksum = mm.checksum(bytes(data[base:base + mm.RECORD_SIZE]))
        data[mm.CHECKSUM_LO[f]] = checksum & 0xFF
        data[mm.CHECKSUM_HI[f]] = checksum >> 8
    data[mm.ACTIVE_FILE] = active
    return data


@pytest.fixture
def save_path(tmp_path) -> Path:
    p = tmp_path / "Metal Max (Japan).sav"
    p.write_bytes(bytes(build_save()))
    return p


def items_by_key(result: SaveInfoResult, group: int = 0) -> dict[str, str]:
    return {i.key: i.value for i in result.groups[group].items}


# ----------------------------------------------------------------------
# Metal Max plugin — pre-filter
# ----------------------------------------------------------------------

class TestMetalMaxMatches:
    def test_accepts_8k_sav_and_srm(self):
        p = MetalMaxSaveInfoPlugin()
        assert p.matches(Path("a.sav"), 8192)
        assert p.matches(Path("a.SRM"), 8192, "NES")
        assert p.matches(Path("a.srm"), 8192, "Mesen")

    def test_rejects_wrong_size_or_extension(self):
        p = MetalMaxSaveInfoPlugin()
        assert not p.matches(Path("a.sav"), 4096)
        assert not p.matches(Path("a.state"), 8192)

    def test_rejects_other_consoles_only(self):
        p = MetalMaxSaveInfoPlugin()
        assert not p.matches(Path("a.srm"), 8192, "SNES")
        assert not p.matches(Path("a.srm"), 8192, "snes9x")
        assert p.matches(Path("a.sav"), 8192, "")  # unknown stays permissive
        assert p.matches(Path("a.srm"), 8192, "fceumm")


# ----------------------------------------------------------------------
# Metal Max plugin — extraction
# ----------------------------------------------------------------------

class TestMetalMaxExtract:
    def test_extracts_localized_facts(self, save_path):
        r = MetalMaxSaveInfoPlugin().extract(save_path, "zh_CN")
        assert r.matched
        assert r.title == "重装机兵"
        assert len(r.groups) == 1
        assert r.groups[0].label == "存档 1（当前）"
        items = items_by_key(r)
        assert items["主角"] == "レバンナ Lv12  HP 210/260"
        assert items["金钱"] == "115,354 G"
        assert items["银行存款"] == "40,000 G"
        assert items["战车"] == "1 辆：M1A1"
        assert items["已开宝箱"] == "3 / 91"
        assert items["事件标志"] == "4 / 256"
        # flags 0x50/0x51 = bounty kills, 0x60 = one reward collected
        assert items["赏金首"] == "2 已击破（1 已领赏）"
        assert "レバンナ Lv12" in r.summary
        assert not r.warnings

    def test_location_uses_map_table_and_anchor(self, save_path):
        # scroll (-3, 4) + solo anchor (7, 8) = tile (4, 12); map 0x0B is
        # the Riorado hunter office in the bundled name table.
        items = items_by_key(MetalMaxSaveInfoPlugin().extract(save_path, "en_US"))
        assert items["Location"] == "Riorado·Hunter office (4, 12)"

    def test_party_anchor_with_train(self, tmp_path):
        p = tmp_path / "a.sav"
        p.write_bytes(bytes(build_save(scroll=(5, 3), train=3)))
        items = items_by_key(MetalMaxSaveInfoPlugin().extract(p, "en_US"))
        assert "(13, 10)" in items["Location"]  # (8, 7) anchor for trains >= 2

    def test_unknown_map_falls_back_to_hex_id(self, tmp_path):
        p = tmp_path / "a.sav"
        p.write_bytes(bytes(build_save(map_id=0xFD)))
        items = items_by_key(MetalMaxSaveInfoPlugin().extract(p, "en_US"))
        assert "map 0xFD" in items["Location"]

    def test_dead_hero_is_flagged(self, tmp_path):
        p = tmp_path / "a.sav"
        p.write_bytes(bytes(build_save(ailment=0xFF)))
        items = items_by_key(MetalMaxSaveInfoPlugin().extract(p, "zh_CN"))
        assert "（死亡）" in items["主角"]

    def test_both_files_and_bad_checksum_warning(self, tmp_path):
        data = build_save(files=(1, 2), active=2)
        data[mm.FILE_BASE[2] + 0x100] ^= 0xFF  # corrupt file 2 only
        p = tmp_path / "a.sav"
        p.write_bytes(bytes(data))
        r = MetalMaxSaveInfoPlugin().extract(p, "zh_CN")
        assert r.matched
        assert len(r.groups) == 2
        assert len(r.warnings) == 1
        assert "存档 2" in r.warnings[0]
        # summary must come from the valid file, not the corrupt active one
        assert "レバンナ" in r.summary

    def test_rejects_blank_foreign_and_truncated(self, tmp_path):
        p = MetalMaxSaveInfoPlugin()
        blank = tmp_path / "blank.sav"
        blank.write_bytes(bytes(mm.SRAM_SIZE))
        assert not p.extract(blank, "en_US").matched

        foreign = tmp_path / "foreign.sav"
        foreign.write_bytes(bytes([0xAB]) * mm.SRAM_SIZE)
        r = p.extract(foreign, "en_US")
        assert not r.matched and "not a Metal Max save" in r.reason

        small = tmp_path / "small.sav"
        small.write_bytes(bytes(512))
        assert "512" in p.extract(small, "en_US").reason

    def test_rejects_when_every_used_file_fails_checksum(self, tmp_path):
        data = build_save()
        data[mm.FILE_BASE[1] + 0x40] ^= 0xFF
        p = tmp_path / "a.sav"
        p.write_bytes(bytes(data))
        r = MetalMaxSaveInfoPlugin().extract(p, "en_US")
        assert not r.matched and "checksum" in r.reason


REAL_SAVE = Path("D:/Game/retro-save-editor/test/fixtures/metal_max_levanna.sav")


@pytest.mark.skipif(not REAL_SAVE.exists(), reason="local reference save only")
def test_real_save_smoke():
    r = MetalMaxSaveInfoPlugin().extract(REAL_SAVE, "zh_CN")
    assert r.matched
    assert "レバンナ Lv1" in r.summary
    assert items_by_key(r)["位置"] == "拉多镇·猎人事务所（4, 12）"


# ----------------------------------------------------------------------
# Manager
# ----------------------------------------------------------------------

class _StubPlugin(SaveInfoPlugin):
    def __init__(self, pid: str = "stub", match: bool = True, matched: bool = True):
        self._pid = pid
        self._match = match
        self._matched = matched
        self.extract_calls = 0

    @property
    def plugin_id(self) -> str:
        return self._pid

    @property
    def display_name(self) -> str:
        return f"Stub {self._pid}"

    def matches(self, path, size, platform=""):  # noqa: ANN001
        return self._match

    def extract(self, path, lang="en_US"):  # noqa: ANN001
        self.extract_calls += 1
        return SaveInfoResult(self._matched, reason="" if self._matched else "nope")


class TestSaveInfoManager:
    def test_discovers_builtin_metal_max(self):
        m = SaveInfoManager()
        m.discover()
        assert "metal-max-fc" in [p.plugin_id for p in m.get_all_plugins()]

    def test_candidates_prefilter(self, save_path):
        m = SaveInfoManager()
        m.discover()
        assert [p.plugin_id for p in m.candidates_for(save_path)] == ["metal-max-fc"]
        assert m.candidates_for(save_path, platform="SNES") == []
        assert m.candidates_for(save_path.with_name("missing.sav")) == []

    def test_extract_stamps_plugin_identity(self, save_path):
        m = SaveInfoManager()
        m.discover()
        r = m.extract(save_path, lang="zh_CN")
        assert r is not None and r.matched
        assert r.plugin_id == "metal-max-fc"
        assert r.plugin_name

    def test_extract_first_match_wins_and_caches(self, tmp_path):
        p = tmp_path / "x.sav"
        p.write_bytes(b"data")
        declines = _StubPlugin("declines", matched=False)
        wins = _StubPlugin("wins")
        never = _StubPlugin("never")
        m = SaveInfoManager()
        for s in (declines, wins, never):
            m.register(s)

        r = m.extract(p, lang="en_US")
        assert r is not None and r.matched and r.plugin_id == "wins"
        assert never.extract_calls == 0

        m.extract(p, lang="en_US")
        assert wins.extract_calls == 1  # cached

        p.write_bytes(b"changed!")  # mtime/size change invalidates the cache
        m.extract(p, lang="en_US")
        assert wins.extract_calls == 2

    def test_extract_returns_first_decline_when_none_match(self, tmp_path):
        p = tmp_path / "x.sav"
        p.write_bytes(b"data")
        m = SaveInfoManager()
        m.register(_StubPlugin("a", matched=False))
        m.register(_StubPlugin("b", matched=False))
        r = m.extract(p)
        assert r is not None and not r.matched and r.plugin_id == "a"

    def test_extract_none_without_candidates(self, tmp_path):
        p = tmp_path / "x.sav"
        p.write_bytes(b"data")
        m = SaveInfoManager()
        m.register(_StubPlugin("a", match=False))
        assert m.extract(p) is None

    def test_plugin_exception_becomes_error_result(self, tmp_path):
        class Boom(_StubPlugin):
            def extract(self, path, lang="en_US"):  # noqa: ANN001
                raise RuntimeError("kaputt")

        p = tmp_path / "x.sav"
        p.write_bytes(b"data")
        m = SaveInfoManager()
        m.register(Boom("boom"))
        r = m.extract(p)
        assert r is not None and not r.matched and "kaputt" in r.error

    def test_user_dir_plugins_are_loaded(self, tmp_path, save_path):
        user_dir = tmp_path / "user_plugins"
        user_dir.mkdir()
        (user_dir / "my_game.py").write_text(
            "from app.saveinfo.base import SaveInfoPlugin, SaveInfoResult\n"
            "class MyPlugin(SaveInfoPlugin):\n"
            "    plugin_id = property(lambda self: 'my-game')\n"
            "    display_name = property(lambda self: 'My Game')\n"
            "    def matches(self, path, size, platform=''):\n"
            "        return False\n"
            "    def extract(self, path, lang='en_US'):\n"
            "        return SaveInfoResult(False)\n",
            encoding="utf-8",
        )
        m = SaveInfoManager()
        m.discover([user_dir])
        ids = [p.plugin_id for p in m.get_all_plugins()]
        assert "my-game" in ids and "metal-max-fc" in ids
