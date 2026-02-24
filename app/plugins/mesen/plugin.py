"""Mesen2 emulator plugin — detects installations and scans multi-system game saves."""

from __future__ import annotations

import json
import platform
import struct
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave, SaveFile, SaveType
from app.plugins.base import EmulatorPlugin

# Mesen2 save state header
MSS_MAGIC = b"MSS"
MSS_MIN_VERSION = 3

# Console type mapping from the save state header
CONSOLE_TYPE_MAP = {
    0: "NES",
    1: "SNES",
    2: "Game Boy",
    3: "PC Engine",
    4: "SMS",      # Master System / Game Gear
    5: "GBA",
    6: "WonderSwan",
}

# Battery save extension → platform mapping
BATTERY_EXT_PLATFORM: dict[str, str] = {
    ".sav": "",       # NES, GBA, SMS/GG, PCE — ambiguous
    ".srm": "",       # SNES, Game Boy — ambiguous
    ".rtc": "",       # RTC data
    ".eeprom": "WonderSwan",
    ".ieeprom": "WonderSwan",
    ".chr.sav": "NES",
    ".eeprom256": "NES",
    ".bs": "SNES",
}

# ROM extension → platform
ROM_EXT_PLATFORM: dict[str, str] = {
    ".nes": "NES",
    ".fds": "NES",
    ".unf": "NES",
    ".sfc": "SNES",
    ".smc": "SNES",
    ".gb": "Game Boy",
    ".gbc": "Game Boy",
    ".gba": "GBA",
    ".pce": "PC Engine",
    ".sms": "SMS",
    ".gg": "SMS",
    ".sg": "SMS",
    ".ws": "WonderSwan",
    ".wsc": "WonderSwan",
}


def _parse_mss_header(file_path: Path) -> dict | None:
    """Parse a Mesen2 save state (.mss) file header.

    Header format:
      - 3 bytes: "MSS" magic
      - 4 bytes: emu version (u32 little-endian)
      - 4 bytes: format version (u32 little-endian)
      - 4 bytes: console type (u32 little-endian)
      - variable: video data (skipped)
      - length-prefixed string: ROM name
    """
    try:
        with open(file_path, "rb") as f:
            magic = f.read(3)
            if magic != MSS_MAGIC:
                return None
            emu_version = struct.unpack("<I", f.read(4))[0]
            fmt_version = struct.unpack("<I", f.read(4))[0]
            if fmt_version < MSS_MIN_VERSION:
                return None
            console_type = struct.unpack("<I", f.read(4))[0]
            console_name = CONSOLE_TYPE_MAP.get(console_type, f"Unknown({console_type})")
            # We don't fully parse the video data and rom name here since
            # the ROM name is also in the filename
            return {
                "emu_version": emu_version,
                "format_version": fmt_version,
                "console_type": console_type,
                "console_name": console_name,
            }
    except Exception as e:
        logger.debug("Failed to parse MSS header {}: {}", file_path, e)
        return None


def _guess_platform_from_saves_dir(saves_dir: Path, rom_name: str) -> str:
    """Try to guess a game's platform from sibling ROM files or save extensions."""
    # Check if there's a ROM file with a known extension
    for ext, plat in ROM_EXT_PLATFORM.items():
        if (saves_dir.parent / "Roms" / f"{rom_name}{ext}").exists():
            return plat
    return ""


class MesenPlugin(EmulatorPlugin):
    """Plugin for Mesen2 — multi-system emulator (NES/SNES/GB/GBA/PCE/SMS/WS)."""

    @property
    def name(self) -> str:
        return "Mesen"

    @property
    def display_name(self) -> str:
        return "Mesen2 (Multi-System)"

    @property
    def supported_platforms(self) -> list[str]:
        return ["NES", "SNES", "Game Boy", "GBA", "PC Engine", "SMS", "WonderSwan"]

    def detect_installation(self) -> list[EmulatorInfo]:
        """Detect Mesen2 installations."""
        installations: list[EmulatorInfo] = []
        if platform.system() != "Windows":
            return installations

        candidates: list[Path] = []

        # 1. Default: Documents/Mesen2
        docs_path = Path.home() / "Documents" / "Mesen2"
        if docs_path.exists():
            candidates.append(docs_path)

        # 2. Portable mode: check common installation dirs
        for prog_dir in [
            Path("C:/Mesen2"),
            Path("C:/Mesen"),
            Path("C:/Program Files/Mesen2"),
            Path("C:/Program Files/Mesen"),
            Path.home() / "scoop" / "apps" / "mesen",
        ]:
            if prog_dir.exists():
                # Portable if settings.json exists next to exe
                if (prog_dir / "settings.json").exists():
                    candidates.append(prog_dir)
                elif docs_path.exists():
                    pass  # already have the docs path
                else:
                    candidates.append(prog_dir)

        # Evaluate
        for candidate in candidates:
            saves_dir = candidate / "Saves"
            states_dir = candidate / "SaveStates"
            is_portable = (candidate / "settings.json").exists()

            if saves_dir.exists() or states_dir.exists() or is_portable:
                data_path = candidate
            else:
                data_path = docs_path if docs_path.exists() else candidate

            # Try to read override paths from settings.json
            settings_file = data_path / "settings.json"
            if settings_file.exists():
                try:
                    with open(settings_file, "r", encoding="utf-8") as f:
                        settings = json.load(f)
                    prefs = settings.get("Preferences", {})
                    if prefs.get("OverrideSaveDataFolder") and prefs.get("SaveDataFolder"):
                        saves_override = Path(prefs["SaveDataFolder"])
                        if saves_override.exists():
                            logger.info("Mesen save folder override: {}", saves_override)
                    if prefs.get("OverrideSaveStateFolder") and prefs.get("SaveStateFolder"):
                        states_override = Path(prefs["SaveStateFolder"])
                        if states_override.exists():
                            logger.info("Mesen save state folder override: {}", states_override)
                except Exception as e:
                    logger.debug("Failed to read Mesen settings.json: {}", e)

            installations.append(EmulatorInfo(
                name="Mesen",
                install_path=candidate,
                data_path=data_path,
                supported_platforms=self.supported_platforms,
                is_portable=is_portable,
            ))

        # De-duplicate
        seen: set[str] = set()
        unique: list[EmulatorInfo] = []
        for info in installations:
            key = str(info.data_path)
            if key not in seen:
                seen.add(key)
                unique.append(info)
                logger.info("Detected Mesen2: data_path={}", info.data_path)

        return unique

    def scan_saves(
        self,
        emulator_info: EmulatorInfo,
        custom_paths: list[Path] | None = None,
    ) -> list[GameSave]:
        """Scan Mesen2 battery saves and save states."""
        all_saves: list[GameSave] = []
        dirs = self.get_save_directories(emulator_info)

        # -- Battery saves --
        saves_dir = dirs.get("saves")
        if saves_dir and saves_dir.exists():
            all_saves.extend(self._scan_battery_saves(saves_dir))

        # -- Save states --
        states_dir = dirs.get("savestates")
        if states_dir and states_dir.exists():
            all_saves.extend(self._scan_savestates(states_dir))

        # Custom paths
        if custom_paths:
            for cp in custom_paths:
                if cp.is_dir():
                    all_saves.extend(self._scan_battery_saves(cp))
                    all_saves.extend(self._scan_savestates(cp))

        logger.info("Mesen: found {} game saves", len(all_saves))
        return all_saves

    def get_save_directories(self, emulator_info: EmulatorInfo) -> dict[str, Path]:
        dp = emulator_info.data_path

        # Check for override paths from settings.json
        saves_dir = dp / "Saves"
        states_dir = dp / "SaveStates"

        settings_file = dp / "settings.json"
        if settings_file.exists():
            try:
                with open(settings_file, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                prefs = settings.get("Preferences", {})
                if prefs.get("OverrideSaveDataFolder") and prefs.get("SaveDataFolder"):
                    override = Path(prefs["SaveDataFolder"])
                    if override.exists():
                        saves_dir = override
                if prefs.get("OverrideSaveStateFolder") and prefs.get("SaveStateFolder"):
                    override = Path(prefs["SaveStateFolder"])
                    if override.exists():
                        states_dir = override
            except Exception:
                pass

        return {
            "saves": saves_dir,
            "savestates": states_dir,
        }

    def _scan_battery_saves(self, saves_dir: Path) -> list[GameSave]:
        """Scan battery save files (.sav, .srm, etc.)."""
        saves: list[GameSave] = []
        seen_roms: dict[str, GameSave] = {}
        battery_extensions = {".sav", ".srm", ".rtc", ".eeprom", ".ieeprom", ".bs"}

        for f in saves_dir.iterdir():
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext not in battery_extensions:
                continue

            rom_name = f.stem
            # Handle compound extensions like .chr.sav
            if rom_name.endswith(".chr"):
                rom_name = rom_name[:-4]

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
                plat = BATTERY_EXT_PLATFORM.get(ext, "")
                if not plat:
                    plat = _guess_platform_from_saves_dir(saves_dir, rom_name)
                gs = GameSave(
                    emulator="Mesen",
                    game_name=rom_name,
                    game_id=rom_name,
                    platform=plat,
                    save_files=[sf],
                )
                seen_roms[rom_name] = gs

        saves.extend(seen_roms.values())
        return saves

    def _scan_savestates(self, states_dir: Path) -> list[GameSave]:
        """Scan Mesen2 save state files (.mss)."""
        saves: list[GameSave] = []
        seen_roms: dict[str, GameSave] = {}

        for f in states_dir.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() != ".mss":
                continue

            # Filename format: {romName}_{slotIndex}.mss
            stem = f.stem
            parts = stem.rsplit("_", 1)
            rom_name = parts[0] if len(parts) == 2 else stem

            stat = f.stat()
            header = _parse_mss_header(f)
            console_name = header["console_name"] if header else ""

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
                    emulator="Mesen",
                    game_name=rom_name,
                    game_id=rom_name,
                    platform=console_name,
                    save_files=[sf],
                )
                seen_roms[rom_name] = gs

        saves.extend(seen_roms.values())
        return saves
