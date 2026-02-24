"""Snes9x emulator plugin — detects installations and scans SNES game saves."""

from __future__ import annotations

import configparser
import os
import platform
import re
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave, SaveFile, SaveType
from app.plugins.base import EmulatorPlugin

# Save-state filename patterns
# Snes9x uses: {romName}.00{slot}  (e.g. game.000, game.001, … game.009)
# Also supports .zst (zstandard compressed) and .frz (legacy freeze)
_SAVESTATE_RE = re.compile(
    r"^(.+)\.(0\d{2}|zst|frz)$", re.IGNORECASE
)

# Battery save extensions
_BATTERY_EXTENSIONS = {".srm"}

# Known Snes9x config filenames
_CONFIG_FILENAMES = ["snes9x.conf", "snes9x.cfg"]


def _read_snes9x_conf(conf_path: Path) -> dict[str, str]:
    """Read a Snes9x .conf/.cfg file and return key-value pairs.

    Snes9x config uses a simple ``Key = Value`` format (INI-like without
    section headers).  We prepend a dummy ``[snes9x]`` header so that
    Python's configparser can handle it.
    """
    result: dict[str, str] = {}
    if not conf_path.is_file():
        return result
    try:
        text = conf_path.read_text(encoding="utf-8", errors="replace")
        # configparser needs at least one section header
        cp = configparser.ConfigParser(strict=False)
        cp.read_string(f"[snes9x]\n{text}")
        for key, value in cp.items("snes9x"):
            result[key] = value
    except Exception as e:
        logger.debug("Failed to parse Snes9x config {}: {}", conf_path, e)
    return result


class Snes9xPlugin(EmulatorPlugin):
    """Plugin for Snes9x — Super Nintendo / SNES emulator."""

    @property
    def name(self) -> str:
        return "Snes9x"

    @property
    def display_name(self) -> str:
        return "Snes9x (SNES)"

    @property
    def supported_platforms(self) -> list[str]:
        return ["SNES"]

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_installation(
        self,
        extra_paths: list[Path] | None = None,
    ) -> list[EmulatorInfo]:
        """Detect Snes9x installations on this machine."""
        installations: list[EmulatorInfo] = []
        if platform.system() != "Windows":
            return installations

        candidates: list[Path] = []

        # 0. User-configured paths
        if extra_paths:
            for p in extra_paths:
                if p.exists() and p not in candidates:
                    candidates.append(p)

        # 1. AppData\Roaming\Snes9x (non-portable)
        appdata = os.environ.get("APPDATA")
        if appdata:
            appdata_dir = Path(appdata) / "Snes9x"
            if appdata_dir.exists():
                candidates.append(appdata_dir)

        # 2. Common portable installation directories
        for prog_dir in [
            Path("C:/Snes9x"),
            Path("C:/Program Files/Snes9x"),
            Path("C:/Program Files (x86)/Snes9x"),
            Path.home() / "scoop" / "apps" / "snes9x",
        ]:
            if prog_dir.exists():
                candidates.append(prog_dir)

        # 3. Scoop — current version symlink
        scoop_current = Path.home() / "scoop" / "apps" / "snes9x" / "current"
        if scoop_current.exists() and scoop_current not in candidates:
            candidates.append(scoop_current)

        # Evaluate candidates
        for candidate in candidates:
            # Determine if this is a portable install (exe + config alongside)
            has_exe = any(
                (candidate / f).exists()
                for f in ("snes9x.exe", "snes9x-x64.exe", "snes9x-64.exe")
            )
            has_conf = any(
                (candidate / name).exists()
                for name in _CONFIG_FILENAMES
            )
            is_portable = has_exe and has_conf

            data_path = candidate

            installations.append(EmulatorInfo(
                name="Snes9x",
                install_path=candidate,
                data_path=data_path,
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
                logger.info("Detected Snes9x: data_path={}", info.data_path)

        return unique

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan_saves(
        self,
        emulator_info: EmulatorInfo,
        custom_paths: list[Path] | None = None,
    ) -> list[GameSave]:
        """Scan Snes9x battery saves and save states."""
        all_saves: list[GameSave] = []
        dirs = self.get_save_directories(emulator_info)

        # Battery saves (.srm)
        saves_dir = dirs.get("saves")
        if saves_dir and saves_dir.exists():
            all_saves.extend(self._scan_battery_saves(saves_dir))

        # Save states (.000–.009, .zst, .frz)
        states_dir = dirs.get("savestates")
        if states_dir and states_dir.exists():
            all_saves.extend(self._scan_savestates(states_dir))

        # Custom paths
        if custom_paths:
            for cp in custom_paths:
                if cp.is_dir():
                    all_saves.extend(self._scan_battery_saves(cp))
                    all_saves.extend(self._scan_savestates(cp))

        # Resolve display names from game_names.json (if present)
        self.resolve_display_names(all_saves)

        logger.info("Snes9x: found {} game saves", len(all_saves))
        return all_saves

    def get_save_directories(self, emulator_info: EmulatorInfo) -> dict[str, Path]:
        """Return save and save-state directory paths.

        Snes9x defaults:
          - Battery saves  → ``<data_path>/Saves``
          - Save states    → ``<data_path>/States`` (or ``<data_path>/SaveStates``)

        These can be overridden in the config file via
        ``SRAMFileDir`` and ``FreezeFileDir``.
        """
        dp = emulator_info.data_path

        # Defaults
        saves_dir = dp / "Saves"
        states_dir = dp / "States"

        # Fall back to alternate naming conventions
        if not saves_dir.exists():
            alt = dp / "SRam"
            if alt.exists():
                saves_dir = alt

        if not states_dir.exists():
            alt = dp / "SaveStates"
            if alt.exists():
                states_dir = alt

        # Override from config
        conf = self._find_and_read_config(dp)
        sram_dir = conf.get("sramfiledir") or conf.get("sramdir")
        if sram_dir:
            override = Path(sram_dir)
            if override.exists():
                saves_dir = override
                logger.info("Snes9x SRAM dir override: {}", saves_dir)

        freeze_dir = conf.get("freezefiledir") or conf.get("freezedir")
        if freeze_dir:
            override = Path(freeze_dir)
            if override.exists():
                states_dir = override
                logger.info("Snes9x save-state dir override: {}", states_dir)

        return {
            "saves": saves_dir,
            "savestates": states_dir,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_and_read_config(base_dir: Path) -> dict[str, str]:
        """Locate and parse the Snes9x config from *base_dir*."""
        for name in _CONFIG_FILENAMES:
            path = base_dir / name
            if path.is_file():
                return _read_snes9x_conf(path)
        return {}

    def _scan_battery_saves(self, saves_dir: Path) -> list[GameSave]:
        """Scan battery save files (.srm)."""
        saves: list[GameSave] = []
        seen_roms: dict[str, GameSave] = {}

        for f in saves_dir.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() not in _BATTERY_EXTENSIONS:
                continue

            rom_name = f.stem
            stat = f.stat()
            sf = SaveFile(
                path=f,
                save_type=SaveType.BATTERY,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )

            if rom_name in seen_roms:
                seen_roms[rom_name].save_files.append(sf)
            else:
                gs = GameSave(
                    emulator="Snes9x",
                    game_name=rom_name,
                    game_id=rom_name,
                    platform="SNES",
                    save_files=[sf],
                )
                seen_roms[rom_name] = gs

        saves.extend(seen_roms.values())
        return saves

    def _scan_savestates(self, states_dir: Path) -> list[GameSave]:
        """Scan Snes9x save-state files (.000–.009, .zst, .frz)."""
        saves: list[GameSave] = []
        seen_roms: dict[str, GameSave] = {}

        for f in states_dir.iterdir():
            if not f.is_file():
                continue

            m = _SAVESTATE_RE.match(f.name)
            if not m:
                continue

            rom_name = m.group(1)
            stat = f.stat()
            sf = SaveFile(
                path=f,
                save_type=SaveType.SAVESTATE,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )

            if rom_name in seen_roms:
                seen_roms[rom_name].save_files.append(sf)
            else:
                gs = GameSave(
                    emulator="Snes9x",
                    game_name=rom_name,
                    game_id=rom_name,
                    platform="SNES",
                    save_files=[sf],
                )
                seen_roms[rom_name] = gs

        saves.extend(seen_roms.values())
        return saves
