# Save-info plugins

Save-info plugins identify a game's save file and extract key facts — hero,
progress, money, location — so a save can be recognized from the scan list
without launching the game. The scan page shows an ℹ button on every save
file a plugin claims; clicking it opens a dialog with the extracted info.

Plugins are **pure Python and run in-process**. The manager is a
self-contained distributed app: a plugin must not shell out to external
tools or depend on other repositories at runtime. Port the format
knowledge (offsets, checksums, charmaps) into the plugin instead, and cite
the reference source in comments.

## Contract (version 1)

A plugin subclasses `app.saveinfo.base.SaveInfoPlugin`:

```python
from pathlib import Path
from app.saveinfo.base import (
    SaveInfoGroup, SaveInfoItem, SaveInfoPlugin, SaveInfoResult,
)

class MyGamePlugin(SaveInfoPlugin):
    @property
    def plugin_id(self) -> str:        # stable unique id, kebab-case
        return "my-game"

    @property
    def display_name(self) -> str:
        return "My Game (SNES)"

    def matches(self, path: Path, size: int, platform: str = "") -> bool:
        """Cheap pre-filter — extension / size / platform only, no file reads.
        Runs for every scanned save file; decides whether the UI offers
        the info button at all."""
        return path.suffix.lower() == ".srm" and size == 2048

    def extract(self, path: Path, lang: str = "en_US") -> SaveInfoResult:
        """The authority. Validate the file's own structure (magic bytes,
        checksums) and return matched=False + a localized reason for
        foreign files — never guess."""
        data = path.read_bytes()
        if not self._looks_like_my_game(data):
            return SaveInfoResult(False, reason="not a My Game save")
        return SaveInfoResult(
            True,
            title="My Game",
            summary="HERO Lv12 · 3,400 G",          # one line, for tooltips/rows
            groups=[SaveInfoGroup("Slot 1", [
                SaveInfoItem("Hero", "HERO Lv12"),
                SaveInfoItem("Gold", "3,400 G"),
            ])],
            warnings=[],                             # e.g. "slot 2 checksum bad"
        )
```

### Input

| Argument | Meaning |
|----------|---------|
| `path` | Absolute path of the save file |
| `size` | File size in bytes (pre-stat'ed; `matches` must not read the file) |
| `platform` | Hint from the owning emulator scan (`"NES"`, `"SNES"`, a RetroArch core name, or `""`). Treat it as a *negative* filter only — naming conventions vary per emulator plugin |
| `lang` | Manager UI language: `zh_CN` / `en_US` / `ja_JP` |

### Output — `SaveInfoResult`

| Field | Rule |
|-------|------|
| `matched` | `True` only after the file's own structure was positively verified |
| `reason` | When `matched=False`: localized, human-readable "why not" |
| `title` | Localized game title |
| `summary` | One-line digest of the most relevant slot |
| `groups` | Sections of `key`/`value` pairs — one group per in-game save slot works well |
| `warnings` | Localized anomalies (bad checksum, corrupted slot, …) |

All strings are **display-ready and already localized** by the plugin for
the requested `lang` (fall back to English for unknown languages). The
manager renders them verbatim; it has no knowledge of any game.

`extract()` must not raise on unrecognized or corrupt content — that is a
normal outcome (`matched=False`). The manager converts unexpected
exceptions into an `error` result and logs them.

### Matching flow

1. `SaveInfoManager.candidates_for()` runs every plugin's `matches()`
   pre-filter (cheap, no I/O beyond one `stat`).
2. `SaveInfoManager.extract()` calls candidates in registration order and
   returns the **first `matched` result**; if every candidate declines it
   returns the first decline so the UI can show its reason.
3. Results are cached per `(path, mtime, size, lang)` for the session.

## Where plugins live

- **Built-in**: a sub-package of `app/saveinfo/` with a `plugin.py`
  (e.g. `app/saveinfo/metal_max/plugin.py`). Discovered automatically;
  data files (name tables, charmaps) sit next to the module and are
  bundled by the PyInstaller spec via `collect_data_files('app.saveinfo')`.
- **User plugins**: `*.py` files dropped into
  `<data_dir>/plugins/save_info/` (e.g.
  `Documents\EmulatorSaveManager\plugins\save_info\` on Windows). Each
  file is imported and scanned for `SaveInfoPlugin` subclasses at startup.

## Built-in plugin: Metal Max (Famicom)

`metal-max-fc` reads the 8 KiB battery SRAM (Mesen `.sav`, RetroArch
`.srm`) and reports, per in-game save file: hero (level/HP/death), party,
gold, bank deposit, location (map name + tile), owned tanks, opened
chests, event-flag count and bounty progress — localized in zh/en/ja.

Identity is verified structurally, so it works regardless of the save's
filename: the save-system header markers (`0x7F0/0x7F1` must be
`0x25`/`0x00`) plus the ROM's own record checksum
(`~sum(record) & 0xFFFF`) — a foreign 8 KiB save passing both is
practically impossible. A used file that fails its checksum is still
shown, with a warning that the game will erase it at boot.

The format knowledge is ported from the retro-save-editor project's
ROM-disassembly-verified layout (reference only — see
`docs/metal-max-save-format.md` there); the map-name table is generated
from its `metal_max.game.json` export.
