"""Metal Max (Famicom) save-info plugin.

Reads the 8 KiB battery SRAM (Mesen ``.sav`` / RetroArch ``.srm`` — the
verbatim CPU $6000-$7FFF image) and summarises both in-game save files.

The format knowledge is ported from retro-save-editor (reference only —
``src/games/metal_max/layout.ts`` and ``docs/metal-max-save-format.md``,
everything ROM-disassembly-proven and Mesen-verified there):

- ``0x7F0/0x7F1`` file used-markers (0x25 = used, 0x00 = empty),
  ``0x7F2`` active file, ``0x7F3..0x7F6`` per-file checksum lo/lo/hi/hi.
- Save records: file 1 at ``0x800``, file 2 at ``0xC00``, 0x400 bytes each.
- Checksum over a whole record: ``~sum(bytes) & 0xFFFF``.
- The header markers + checksum double as the identity gate: a foreign
  8 KiB save passing both is practically impossible.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.saveinfo.base import (
    SaveInfoGroup,
    SaveInfoItem,
    SaveInfoPlugin,
    SaveInfoResult,
)
from app.saveinfo.metal_max.charmap import decode_name

SRAM_SIZE = 8192
RECORD_SIZE = 0x400
FILE_BASE = {1: 0x800, 2: 0xC00}

# Save-system header (absolute offsets)
USED_MARKER = {1: 0x7F0, 2: 0x7F1}
MARKER_USED = 0x25
ACTIVE_FILE = 0x7F2
CHECKSUM_LO = {1: 0x7F3, 2: 0x7F4}
CHECKSUM_HI = {1: 0x7F5, 2: 0x7F6}

# Record-relative offsets
NAMES = 0x00          # 3 chars x 5 bytes (4 glyphs + 0x9F terminator)
NAME_SLOT = 5
TANK_NAMES = 0x0F     # 11 tanks x 7 bytes; first 8 = NO_1..NO_8
TANK_NAME_SLOT = 7
PLAYER_TANKS = 8      # NO_1..NO_8 (9..11 are rental slots, pre-named)
GOLD = 0x5D           # le24, cap 9,999,999
MAX_HP = 0x60         # le16 x 3
CUR_HP = 0x66         # le16 x 3
TEAM_STATUS = 0x78    # u8 x 3: low nibble = party position, bit 7 = in tank
AILMENT = 0x7B        # u8 x 3; 0xFF = dead
LEVEL = 0x7E          # u8 x 3
BANK_GOLD = 0x290     # le24
EVENT_FLAGS = 0x36E   # 32 bytes, 256 flags, lsb-first
TREASURE = 0x38E      # 12 bytes, 91 chest flags, lsb-first
TREASURE_COUNT = 91
PLAYER_MAP = 0x3DA    # game.json map id; 0x00 = world map
PLAYER_SCROLL_X = 0x3DB  # signed screen-scroll origin, tile = scroll + anchor
PLAYER_SCROLL_Y = 0x3DC
PLAYER_TRAIN = 0x3DD  # sprites in the walking chain; anchor (7,8) solo, (8,7) >=2
BOUNTY_DEFEATED_FLAG = 0x50  # event flag base, one per wanted slot
BOUNTY_REWARDED_FLAG = 0x60
BOUNTY_SLOTS = 16
AILMENT_DEAD = 0xFF

# Platforms that positively rule this game out (the pre-filter stays
# permissive otherwise: RetroArch reports core names, not console names).
_OTHER_CONSOLES = (
    "snes", "sfc", "super", "gba", "gameboy", "gb", "nds", "3ds", "psx",
    "ps1", "ps2", "n64", "gamecube", "wii", "genesis", "megadrive",
    "saturn", "dreamcast",
)

_L = {
    "zh": {
        "title": "重装机兵",
        "file": "存档 {n}",
        "active": "当前",
        "hero": "主角",
        "party": "队伍",
        "roles": ("猎人", "机械师", "士兵"),
        "gold": "金钱",
        "bank": "银行存款",
        "location": "位置",
        "tanks": "战车",
        "tanks_value": "{n} 辆：{names}",
        "no_tanks": "无",
        "chests": "已开宝箱",
        "flags": "事件标志",
        "bounty": "赏金首",
        "bounty_value": "{d} 已击破（{r} 已领赏）",
        "world_map": "世界地图",
        "map_fallback": "地图 0x{id:02X}",
        "dead": "死亡",
        "reason_size": "文件大小 {size} 字节，不是 8KB 电池存档",
        "reason_header": "存档头标记不符——不是重装机兵的存档",
        "reason_empty": "没有已使用的存档位（尚未在游戏内存档）",
        "reason_no_valid": "所有存档位校验失败——可能不是重装机兵的存档",
        "warn_bad": "存档 {n} 校验和错误，游戏启动自检时会清除它",
        "paren": "（{s}）",
        "sep": "、",
    },
    "en": {
        "title": "Metal Max",
        "file": "File {n}",
        "active": "active",
        "hero": "Hero",
        "party": "Party",
        "roles": ("Hunter", "Mechanic", "Soldier"),
        "gold": "Gold",
        "bank": "Bank deposit",
        "location": "Location",
        "tanks": "Tanks",
        "tanks_value": "{n}: {names}",
        "no_tanks": "none",
        "chests": "Chests opened",
        "flags": "Event flags",
        "bounty": "Bounties",
        "bounty_value": "{d} defeated ({r} rewarded)",
        "world_map": "World Map",
        "map_fallback": "map 0x{id:02X}",
        "dead": "DEAD",
        "reason_size": "file is {size} bytes, not an 8 KiB battery save",
        "reason_header": "save-system header markers mismatch — not a Metal Max save",
        "reason_empty": "no used save file (nothing saved in-game yet)",
        "reason_no_valid": "every save file fails the checksum — likely not a Metal Max save",
        "warn_bad": "file {n} has a bad checksum — the game erases it during boot validation",
        "paren": " ({s})",
        "sep": ", ",
    },
    "ja": {
        "title": "メタルマックス",
        "file": "ファイル {n}",
        "active": "現在",
        "hero": "主人公",
        "party": "パーティ",
        "roles": ("ハンター", "メカニック", "ソルジャー"),
        "gold": "所持金",
        "bank": "預金",
        "location": "現在地",
        "tanks": "戦車",
        "tanks_value": "{n} 台：{names}",
        "no_tanks": "なし",
        "chests": "開けた宝箱",
        "flags": "イベントフラグ",
        "bounty": "賞金首",
        "bounty_value": "{d} 撃破（{r} 受取済）",
        "world_map": "ワールドマップ",
        "map_fallback": "マップ 0x{id:02X}",
        "dead": "死亡",
        "reason_size": "ファイルサイズ {size} バイト — 8KB のバッテリーセーブではありません",
        "reason_header": "セーブヘッダのマーカー不一致 — メタルマックスのセーブではありません",
        "reason_empty": "使用中のセーブファイルがありません",
        "reason_no_valid": "全ファイルのチェックサムが不正 — メタルマックスのセーブではない可能性",
        "warn_bad": "ファイル {n} のチェックサムが不正 — 起動時の検証で消去されます",
        "paren": "（{s}）",
        "sep": "、",
    },
}


def _short_lang(lang: str) -> str:
    lang = (lang or "").lower()
    if lang.startswith("zh"):
        return "zh"
    if lang.startswith("ja"):
        return "ja"
    return "en"


def checksum(record: bytes) -> int:
    """ROM routine $BE47: one's complement of the 16-bit byte sum."""
    return ~sum(record) & 0xFFFF


def _le(data: bytes, offset: int, width: int) -> int:
    return int.from_bytes(data[offset:offset + width], "little")


def _signed8(v: int) -> int:
    return v - 256 if v > 127 else v


def _bit(data: bytes, base: int, index: int) -> bool:
    """lsb-first bitfield test (chest n -> byte n>>3, mask 1<<(n&7))."""
    return bool(data[base + (index >> 3)] & (1 << (index & 7)))


def _fmt_gold(n: int) -> str:
    return f"{n:,} G"


class MetalMaxSaveInfoPlugin(SaveInfoPlugin):
    """Save-info extractor for the Famicom Metal Max battery save."""

    _map_names: dict[str, dict[str, str]] | None = None

    @property
    def plugin_id(self) -> str:
        return "metal-max-fc"

    @property
    def display_name(self) -> str:
        return "Metal Max (Famicom)"

    # ------------------------------------------------------------------
    # Pre-filter
    # ------------------------------------------------------------------

    def matches(self, path: Path, size: int, platform: str = "") -> bool:
        if path.suffix.lower() not in (".sav", ".srm"):
            return False
        if size != SRAM_SIZE:
            return False
        p = platform.lower()
        return not any(tok in p for tok in _OTHER_CONSOLES)

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract(self, path: Path, lang: str = "en_US") -> SaveInfoResult:
        t = _L[_short_lang(lang)]
        data = path.read_bytes()

        if len(data) != SRAM_SIZE:
            return SaveInfoResult(False, reason=t["reason_size"].format(size=len(data)))

        # Identity gate 1: used-markers can only be 0x25 (used) or 0x00 (empty).
        markers = {f: data[USED_MARKER[f]] for f in (1, 2)}
        if any(m not in (MARKER_USED, 0x00) for m in markers.values()):
            return SaveInfoResult(False, reason=t["reason_header"])

        used = [f for f in (1, 2) if markers[f] == MARKER_USED]
        if not used:
            return SaveInfoResult(False, reason=t["reason_empty"])

        # Identity gate 2: at least one used file must pass the ROM checksum.
        def checksum_ok(f: int) -> bool:
            stored = data[CHECKSUM_LO[f]] | (data[CHECKSUM_HI[f]] << 8)
            return stored == checksum(data[FILE_BASE[f]:FILE_BASE[f] + RECORD_SIZE])

        valid = {f: checksum_ok(f) for f in used}
        if not any(valid.values()):
            return SaveInfoResult(False, reason=t["reason_no_valid"])

        # $67F2 can be 0 in very old saves — fall back to the first used file
        # (same fallback the reference editor applies).
        active = data[ACTIVE_FILE]
        if active not in used:
            active = used[0]
        groups: list[SaveInfoGroup] = []
        warnings: list[str] = []
        summary = ""

        for f in used:
            record = data[FILE_BASE[f]:FILE_BASE[f] + RECORD_SIZE]
            if not valid[f]:
                warnings.append(t["warn_bad"].format(n=f))

            label = t["file"].format(n=f)
            if f == active:
                label += t["paren"].format(s=t["active"])
            group = SaveInfoGroup(label, self._record_items(record, t, lang))
            groups.append(group)

            if valid[f] and (f == active or not summary):
                summary = self._summary(record, t, lang)

        return SaveInfoResult(
            True,
            title=t["title"],
            summary=summary,
            groups=groups,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Record parsing
    # ------------------------------------------------------------------

    def _record_items(self, r: bytes, t: dict, lang: str) -> list[SaveInfoItem]:
        chars = self._characters(r, t)
        hero = chars[0]
        hero_value = f"{hero['name']} Lv{hero['level']}  HP {hero['cur_hp']}/{hero['max_hp']}"
        if hero["dead"]:
            hero_value += t["paren"].format(s=t["dead"])

        members = [c for c in chars if c["in_party"]]
        members.sort(key=lambda c: c["position"])
        party_value = t["sep"].join(
            f"{c['name']}{t['paren'].format(s=t['roles'][c['id']])} Lv{c['level']}"
            for c in members
        )

        tanks = self._tank_names(r)
        tanks_value = (
            t["tanks_value"].format(n=len(tanks), names=t["sep"].join(tanks))
            if tanks else t["no_tanks"]
        )

        chests = sum(_bit(r, TREASURE, i) for i in range(TREASURE_COUNT))
        flags = sum(_bit(r, EVENT_FLAGS, i) for i in range(256))
        defeated = sum(
            _bit(r, EVENT_FLAGS, BOUNTY_DEFEATED_FLAG + s) for s in range(BOUNTY_SLOTS)
        )
        rewarded = sum(
            _bit(r, EVENT_FLAGS, BOUNTY_REWARDED_FLAG + s) for s in range(BOUNTY_SLOTS)
        )

        return [
            SaveInfoItem(t["hero"], hero_value),
            SaveInfoItem(t["party"], party_value),
            SaveInfoItem(t["gold"], _fmt_gold(_le(r, GOLD, 3))),
            SaveInfoItem(t["bank"], _fmt_gold(_le(r, BANK_GOLD, 3))),
            SaveInfoItem(t["location"], self._location(r, t, lang)),
            SaveInfoItem(t["tanks"], tanks_value),
            SaveInfoItem(t["chests"], f"{chests} / {TREASURE_COUNT}"),
            SaveInfoItem(t["flags"], f"{flags} / 256"),
            SaveInfoItem(
                t["bounty"], t["bounty_value"].format(d=defeated, r=rewarded)
            ),
        ]

    def _summary(self, r: bytes, t: dict, lang: str) -> str:
        hero = self._characters(r, t)[0]
        map_name = self._map_name(r[PLAYER_MAP], t, lang)
        return (
            f"{hero['name']} Lv{hero['level']} · "
            f"{_fmt_gold(_le(r, GOLD, 3))} · {map_name}"
        )

    @staticmethod
    def _characters(r: bytes, t: dict) -> list[dict]:
        out = []
        for i in range(3):
            team = r[TEAM_STATUS + i]
            out.append({
                "id": i,
                "name": decode_name(r[NAMES + i * NAME_SLOT:NAMES + (i + 1) * NAME_SLOT]),
                "level": r[LEVEL + i],
                "cur_hp": _le(r, CUR_HP + i * 2, 2),
                "max_hp": _le(r, MAX_HP + i * 2, 2),
                "in_party": (team & 0x0F) > 0,
                "position": team & 0x0F,
                "dead": r[AILMENT + i] == AILMENT_DEAD,
            })
        return out

    @staticmethod
    def _tank_names(r: bytes) -> list[str]:
        """Owned player tanks NO_1..NO_8 — the game forces a name on
        acquisition, so a non-blank name slot == an owned tank."""
        out = []
        for i in range(PLAYER_TANKS):
            slot = r[TANK_NAMES + i * TANK_NAME_SLOT:TANK_NAMES + (i + 1) * TANK_NAME_SLOT]
            if slot[0] not in (0x9F, 0x00):
                out.append(decode_name(slot))
        return out

    def _location(self, r: bytes, t: dict, lang: str) -> str:
        map_id = r[PLAYER_MAP]
        # The save stores the screen's scroll origin; the player is drawn at
        # a fixed screen tile — (7,8) solo, (8,7) with a party train >= 2.
        anchor = (7, 8) if r[PLAYER_TRAIN] <= 1 else (8, 7)
        tile_x = _signed8(r[PLAYER_SCROLL_X]) + anchor[0]
        tile_y = _signed8(r[PLAYER_SCROLL_Y]) + anchor[1]
        name = self._map_name(map_id, t, lang)
        return f"{name}{t['paren'].format(s=f'{tile_x}, {tile_y}')}"

    def _map_name(self, map_id: int, t: dict, lang: str) -> str:
        table = self._load_map_names()
        entry = table.get(f"{map_id:02X}")
        if entry:
            short = _short_lang(lang)
            name = entry.get(short) or entry.get("en") or entry.get("zh")
            if name:
                return name
        if map_id == 0:
            return t["world_map"]
        return t["map_fallback"].format(id=map_id)

    @classmethod
    def _load_map_names(cls) -> dict[str, dict[str, str]]:
        if cls._map_names is None:
            path = Path(__file__).parent / "map_names.json"
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    cls._map_names = json.load(fh).get("maps", {})
            except Exception:
                cls._map_names = {}
        return cls._map_names
