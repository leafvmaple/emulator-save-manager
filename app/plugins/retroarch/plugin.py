"""RetroArch emulator plugin — detects installations and scans saves across cores.

RetroArch is a multi-system frontend: one install runs dozens of libretro
cores.  Saves live under two configurable directories:

    saves/   — battery saves (.srm, .sav, .mcr, .eep, …) named after the ROM
    states/  — save states (.state, .state1…, .state.auto)

Both directories (and an optional per-core sub-folder layout when
``sort_savefiles_enable`` / ``sort_savestates_enable`` is on) are honored.
Overrides in ``retroarch.cfg`` (``savefile_directory`` / ``savestate_directory``)
take precedence; the special ``:`` prefix means "relative to the RetroArch
base directory" and ``"default"`` means the built-in default.
"""

from __future__ import annotations

import platform
import re
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.core.path_resolver import platform_data_dir_candidates
from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave, SaveFile, SaveType
from app.plugins.base import EmulatorPlugin

# Battery / SRAM save extensions produced by common cores.
_BATTERY_EXTS = {
    ".srm", ".sav", ".rtc", ".rt", ".mcr", ".eep", ".fla", ".sra",
    ".mpk", ".dsv", ".bsv", ".ram",
}

# Save-state filenames: <rom>.state, <rom>.state1 … <rom>.state9, <rom>.state.auto
_STATE_RE = re.compile(r"^(.+?)\.state(?:\d+|\.auto)?$", re.IGNORECASE)

# Files in saves/ that are never battery saves.
_NON_SAVE_EXTS = {".cht", ".opt", ".cfg", ".png", ".bsv"}

_CFG_NAME = "retroarch.cfg"


def _read_retroarch_cfg(path: Path) -> dict[str, str]:
    """Parse the subset of ``retroarch.cfg`` we care about (``key = "value"``)."""
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    keys = {"savefile_directory", "savestate_directory", "system_directory"}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key in keys:
                result[key] = val.strip().strip('"')
    except Exception as e:
        logger.debug("Failed to read retroarch.cfg {}: {}", path, e)
    return result


def _resolve_cfg_dir(value: str, base: Path) -> Path | None:
    """Resolve a retroarch.cfg directory value.

    Returns ``None`` for ``"default"`` / empty (caller uses the built-in
    default).  ``":"`` is RetroArch's alias for the base directory.
    """
    if not value or value.lower() == "default":
        return None
    if value.startswith(":"):
        rest = value[1:].lstrip("/\\")
        return base / rest if rest else base
    return Path(value)


class RetroArchPlugin(EmulatorPlugin):
    """Plugin for RetroArch — multi-system libretro frontend."""

    @property
    def name(self) -> str:
        return "RetroArch"

    @property
    def display_name(self) -> str:
        return "RetroArch (Multi-System)"

    @property
    def supported_platforms(self) -> list[str]:
        return ["Multi-System"]

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_installation(
        self,
        extra_paths: list[Path] | None = None,
    ) -> list[EmulatorInfo]:
        """Detect RetroArch installations on this machine."""
        installations: list[EmulatorInfo] = []
        candidates: list[Path] = []
        managed_roots: list[Path] = []  # standard config dirs → non-portable

        # 0. User-configured paths (all platforms)
        if extra_paths:
            for p in extra_paths:
                if p.exists() and p not in candidates:
                    candidates.append(p)

        system = platform.system()
        if system == "Windows":
            import os
            appdata = os.environ.get("APPDATA")
            if appdata:
                d = Path(appdata) / "RetroArch"
                if d.exists():
                    candidates.append(d)
                    managed_roots.append(d)
            for prog_path in [
                Path("C:/RetroArch-Win64"),
                Path("C:/RetroArch"),
                Path("C:/Program Files/RetroArch"),
                Path("C:/Program Files (x86)/RetroArch"),
                Path("C:/Program Files (x86)/Steam/steamapps/common/RetroArch"),
                Path.home() / "scoop" / "apps" / "retroarch" / "current",
            ]:
                if prog_path.exists() and prog_path not in candidates:
                    candidates.append(prog_path)
        else:
            # macOS: ~/Library/Application Support/RetroArch
            # Linux: ~/.config/retroarch, ~/.local/share/retroarch, Flatpak
            probes = platform_data_dir_candidates(
                macos_names=["RetroArch"], linux_names=["retroarch"],
            )
            probes.append(Path.home() / ".var" / "app"
                          / "org.libretro.RetroArch" / "config" / "retroarch")
            for p in probes:
                if p.exists() and p not in candidates:
                    candidates.append(p)
                    managed_roots.append(p)

        for candidate in candidates:
            # A valid RetroArch data dir has saves/ or states/ or retroarch.cfg.
            looks_valid = (
                (candidate / "saves").exists()
                or (candidate / "states").exists()
                or (candidate / _CFG_NAME).exists()
            )
            if not looks_valid:
                continue
            is_portable = candidate not in managed_roots
            installations.append(EmulatorInfo(
                name="RetroArch",
                install_path=candidate,
                data_path=candidate,
                supported_platforms=self.supported_platforms,
                is_portable=is_portable,
            ))

        # De-duplicate by data_path
        seen: set[str] = set()
        unique: list[EmulatorInfo] = []
        for info in installations:
            key = str(info.data_path)
            if key not in seen:
                seen.add(key)
                unique.append(info)
                logger.info("Detected RetroArch: data_path={}", info.data_path)
        return unique

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def get_save_directories(self, emulator_info: EmulatorInfo) -> dict[str, Path]:
        dp = emulator_info.data_path
        saves_dir = dp / "saves"
        states_dir = dp / "states"

        cfg = _read_retroarch_cfg(dp / _CFG_NAME)
        sf = _resolve_cfg_dir(cfg.get("savefile_directory", ""), dp)
        if sf is not None:
            saves_dir = sf
        ss = _resolve_cfg_dir(cfg.get("savestate_directory", ""), dp)
        if ss is not None:
            states_dir = ss

        return {"saves": saves_dir, "savestates": states_dir}

    def get_state_thumbnail(self, save_path: Path) -> bytes | None:
        """Return a RetroArch save-state thumbnail (sibling ``<state>.png``).

        With "Save State Thumbnail Enable" on, RetroArch writes a PNG next to
        the state file at ``<state_path>.png``.
        """
        sibling = save_path.with_name(save_path.name + ".png")
        if sibling.is_file():
            try:
                return sibling.read_bytes()
            except OSError as e:
                logger.debug("Cannot read thumbnail {}: {}", sibling, e)
        return None

    def scan_saves(
        self,
        emulator_info: EmulatorInfo,
        custom_paths: list[Path] | None = None,
    ) -> list[GameSave]:
        """Scan RetroArch battery saves and save states across cores."""
        seen: dict[str, GameSave] = {}
        dirs = self.get_save_directories(emulator_info)

        saves_dir = dirs.get("saves")
        if saves_dir and saves_dir.exists():
            self._scan_battery(saves_dir, seen)

        states_dir = dirs.get("savestates")
        if states_dir and states_dir.exists():
            self._scan_states(states_dir, seen)

        if custom_paths:
            for cp in custom_paths:
                if cp.is_dir():
                    self._scan_battery(cp, seen)
                    self._scan_states(cp, seen)

        all_saves = list(seen.values())
        logger.info("RetroArch: found {} game saves", len(all_saves))
        return all_saves

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _scan_battery(self, root: Path, seen: dict[str, GameSave]) -> None:
        for f, core in self._iter_files(root):
            ext = f.suffix.lower()
            if ext in _NON_SAVE_EXTS or ext not in _BATTERY_EXTS:
                continue
            self._add(seen, base=f.stem, core=core, path=f, save_type=SaveType.BATTERY)

    def _scan_states(self, root: Path, seen: dict[str, GameSave]) -> None:
        for f, core in self._iter_files(root):
            m = _STATE_RE.match(f.name)
            if not m:
                continue
            self._add(seen, base=m.group(1), core=core, path=f,
                      save_type=SaveType.SAVESTATE)

    @staticmethod
    def _iter_files(root: Path):
        """Yield (file, core_name) for files in *root* and its immediate subdirs.

        When RetroArch sorts saves by core/content, each save lands in a
        sub-folder named after the core; that name becomes a platform hint.
        """
        try:
            entries = list(root.iterdir())
        except (PermissionError, OSError):
            return
        for entry in entries:
            if entry.is_file():
                yield entry, ""
            elif entry.is_dir():
                try:
                    for child in entry.iterdir():
                        if child.is_file():
                            yield child, entry.name
                except (PermissionError, OSError):
                    continue

    @staticmethod
    def _add(
        seen: dict[str, GameSave],
        base: str,
        core: str,
        path: Path,
        save_type: SaveType,
    ) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        sf = SaveFile(
            path=path,
            save_type=save_type,
            size=stat.st_size,
            modified=datetime.fromtimestamp(stat.st_mtime),
        )
        if base in seen:
            seen[base].save_files.append(sf)
            if core and not seen[base].platform:
                seen[base].platform = core
        else:
            seen[base] = GameSave(
                emulator="RetroArch",
                game_name=base,
                game_id=base,
                platform=core,
                save_files=[sf],
            )
