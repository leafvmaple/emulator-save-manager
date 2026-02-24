"""Citra emulator plugin — detects installations and scans 3DS game saves."""

from __future__ import annotations

import configparser
import platform
import re
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.config import _default_data_dir
from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave, SaveFile, SaveType
from app.plugins.base import EmulatorPlugin
from app.plugins.citra.game_db import GameDB

# Citra title ID layout:
#   sdmc/Nintendo 3DS/<id0>/<id1>/title/<high>/<low>/data/00000001/
# Save states:
#   states/<title_id>.cst  (slot 0) or <title_id>.<slot>.cst
_TITLE_ID_RE = re.compile(r"^[0-9a-fA-F]{16}$")
_SAVESTATE_RE = re.compile(
    r"^([0-9a-fA-F]{16})(?:\.(\d+))?\.cst$", re.IGNORECASE
)

# Known 3DS title-ID high values (category)
_TITLE_HIGH_APP = "00040000"       # Regular application
_TITLE_HIGH_DLC = "0004008c"       # DLC
_TITLE_HIGH_UPDATE = "0004000e"    # Update
_TITLE_HIGH_SYSTEM = "00040010"    # System title


def _format_title_id(high: str, low: str) -> str:
    """Build a 16-char title ID from high/low components."""
    return f"{high}{low}".upper()


def _is_game_title(high: str) -> bool:
    """Return True if the title-ID high half represents a user game."""
    return high.lower() in (_TITLE_HIGH_APP,)


class CitraPlugin(EmulatorPlugin):
    """Plugin for Citra / Lime3DS — Nintendo 3DS emulator."""

    _game_db: GameDB | None = None

    @classmethod
    def _get_game_db(cls) -> GameDB:
        if cls._game_db is None:
            cache_dir = _default_data_dir() / "cache"
            cls._game_db = GameDB(cache_dir)
        return cls._game_db

    @property
    def name(self) -> str:
        return "Citra"

    @property
    def display_name(self) -> str:
        return "Citra (Nintendo 3DS)"

    @property
    def supported_platforms(self) -> list[str]:
        return ["3DS"]

    def get_cover_urls(self, game_id: str) -> list[str]:
        """Return candidate GameTDB cover image URLs for a 3DS title ID."""
        db = self._get_game_db()
        if not db.is_loaded:
            db.load()
        if not db.is_loaded:
            return []
        # Strip extdata suffix when looking up
        lookup_id = game_id.replace("_extdata", "")
        return db.get_cover_urls(lookup_id)

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_installation(
        self,
        extra_paths: list[Path] | None = None,
    ) -> list[EmulatorInfo]:
        """Detect Citra / Lime3DS installations on this machine."""
        installations: list[EmulatorInfo] = []
        if platform.system() != "Windows":
            return installations

        candidates: list[Path] = []

        # 0. User-configured paths
        if extra_paths:
            for p in extra_paths:
                if p.exists() and p not in candidates:
                    candidates.append(p)

        appdata = Path.home() / "AppData" / "Roaming"

        # 1. Official Citra
        citra_dir = appdata / "Citra"
        if citra_dir.exists():
            candidates.append(citra_dir)

        # 2. Lime3DS (Citra fork)
        lime_dir = appdata / "Lime3DS"
        if lime_dir.exists():
            candidates.append(lime_dir)

        # 3. Citra-enhanced / other forks
        for name in ("citra-emu", "Citra-Enhanced"):
            fork_dir = appdata / name
            if fork_dir.exists():
                candidates.append(fork_dir)

        # 4. Portable mode — look for user/ dir next to executable
        for prog_path in [
            Path("C:/Citra"),
            Path("C:/Program Files/Citra"),
            Path("C:/Lime3DS"),
            Path.home() / "scoop" / "apps" / "citra",
        ]:
            user_dir = prog_path / "user"
            if user_dir.exists():
                candidates.append(user_dir)

        for candidate in candidates:
            sdmc_dir = candidate / "sdmc"
            nand_dir = candidate / "nand"
            states_dir = candidate / "states"
            is_portable = not str(candidate).startswith(str(appdata))

            if sdmc_dir.exists() or nand_dir.exists() or states_dir.exists():
                installations.append(EmulatorInfo(
                    name="Citra",
                    install_path=candidate.parent if is_portable else candidate,
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
                logger.info("Detected Citra: data_path={}", info.data_path)
        return unique

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan_saves(
        self,
        emulator_info: EmulatorInfo,
        custom_paths: list[Path] | None = None,
    ) -> list[GameSave]:
        """Scan Citra game saves and save states."""
        all_saves: list[GameSave] = []
        dirs = self.get_save_directories(emulator_info)

        # -- SDMC saves (game saves inside virtual SD card) --
        sdmc_dir = dirs.get("sdmc")
        if sdmc_dir and sdmc_dir.exists():
            all_saves.extend(self._scan_sdmc_saves(sdmc_dir))

        # -- Extra data (SpotPass, StreetPass, etc.) --
        extdata_dir = dirs.get("extdata")
        if extdata_dir and extdata_dir.exists():
            all_saves.extend(self._scan_extdata(extdata_dir))

        # -- Save states --
        states_dir = dirs.get("states")
        if states_dir and states_dir.exists():
            all_saves.extend(self._scan_savestates(states_dir))

        # Custom paths
        if custom_paths:
            for cp in custom_paths:
                if cp.is_dir():
                    all_saves.extend(self._scan_sdmc_saves(cp))

        # Merge saves with the same game ID (e.g. SDMC save + save state)
        all_saves = self._merge_saves(all_saves)

        # --- Resolve display names from local game_names.json ---
        self.resolve_display_names(all_saves)

        logger.info("Citra: found {} game saves", len(all_saves))
        return all_saves

    def resolve_display_names(self, saves: list[GameSave]) -> None:
        """Override to handle Citra-specific extdata suffix."""
        from app.i18n import get_current_language
        lang = get_current_language()
        table = self._load_game_names()
        if not table:
            return
        for save in saves:
            lookup_id = save.game_id.replace("_extdata", "")
            display = self.get_display_name(lookup_id, lang)
            if display:
                if save.game_id.endswith("_extdata"):
                    save.game_name = f"{display} (extdata)"
                else:
                    save.game_name = display

    def get_save_directories(self, emulator_info: EmulatorInfo) -> dict[str, Path]:
        dp = emulator_info.data_path
        sdmc = dp / "sdmc"

        # Try to read custom directories from qt-config.ini
        config_paths = [
            dp / "config" / "qt-config.ini",
            dp / "config" / "sdl2-config.ini",
        ]
        for cfg_path in config_paths:
            if cfg_path.exists():
                try:
                    overrides = self._read_config_paths(cfg_path)
                    if "sdmc" in overrides:
                        sdmc = Path(overrides["sdmc"])
                except Exception as e:
                    logger.debug("Failed to read Citra config {}: {}", cfg_path, e)

        return {
            "sdmc": sdmc,
            "extdata": sdmc / "Nintendo 3DS" if sdmc.exists() else dp / "sdmc" / "Nintendo 3DS",
            "states": dp / "states",
        }

    # ------------------------------------------------------------------
    # Internal scanning helpers
    # ------------------------------------------------------------------

    def _scan_sdmc_saves(self, sdmc_dir: Path) -> list[GameSave]:
        """Scan game saves from the virtual SD card structure.

        Path layout:
          sdmc/Nintendo 3DS/<id0>/<id1>/title/<high>/<low>/data/00000001/
        """
        saves: list[GameSave] = []
        nintendo_dir = sdmc_dir / "Nintendo 3DS"
        if not nintendo_dir.exists():
            return saves

        for id0_dir in self._safe_iterdir(nintendo_dir):
            if not id0_dir.is_dir() or not _TITLE_ID_RE.match(id0_dir.name.lower().ljust(16, "0")[:16]):
                # id0 is a 32-char hex directory
                if not id0_dir.is_dir():
                    continue
            for id1_dir in self._safe_iterdir(id0_dir):
                if not id1_dir.is_dir():
                    continue
                title_base = id1_dir / "title"
                if not title_base.exists():
                    continue
                saves.extend(self._scan_title_dir(title_base))

        return saves

    def _scan_title_dir(self, title_base: Path) -> list[GameSave]:
        """Scan title/<high>/<low>/data/00000001/ for save files."""
        saves: list[GameSave] = []

        for high_dir in self._safe_iterdir(title_base):
            if not high_dir.is_dir():
                continue
            high = high_dir.name.lower()
            if not _is_game_title(high):
                continue

            for low_dir in self._safe_iterdir(high_dir):
                if not low_dir.is_dir():
                    continue
                low = low_dir.name.lower()
                title_id = _format_title_id(high, low)

                # Main save data
                save_data_dir = low_dir / "data" / "00000001"
                if save_data_dir.exists():
                    save_files = self._collect_files(save_data_dir, SaveType.FOLDER)
                    if save_files:
                        saves.append(GameSave(
                            emulator="Citra",
                            game_name=title_id,
                            game_id=title_id,
                            platform="3DS",
                            save_files=save_files,
                        ))

        return saves

    def _scan_extdata(self, nintendo_dir: Path) -> list[GameSave]:
        """Scan extdata from the virtual SD card.

        Path layout:
          Nintendo 3DS/<id0>/<id1>/extdata/<high>/<low>/
        """
        saves: list[GameSave] = []

        for id0_dir in self._safe_iterdir(nintendo_dir):
            if not id0_dir.is_dir():
                continue
            for id1_dir in self._safe_iterdir(id0_dir):
                if not id1_dir.is_dir():
                    continue
                extdata_base = id1_dir / "extdata" / "00000000"
                if not extdata_base.exists():
                    continue
                for low_dir in self._safe_iterdir(extdata_base):
                    if not low_dir.is_dir():
                        continue
                    low = low_dir.name.lower()
                    title_id = _format_title_id("00000000", low)
                    ext_files = self._collect_files(low_dir, SaveType.FOLDER)
                    if ext_files:
                        saves.append(GameSave(
                            emulator="Citra",
                            game_name=f"{title_id} (extdata)",
                            game_id=f"{title_id}_extdata",
                            platform="3DS",
                            save_files=ext_files,
                        ))

        return saves

    def _scan_savestates(self, states_dir: Path) -> list[GameSave]:
        """Scan Citra save state files (.cst)."""
        seen: dict[str, GameSave] = {}

        for f in self._safe_iterdir(states_dir):
            if not f.is_file():
                continue
            m = _SAVESTATE_RE.match(f.name)
            if not m:
                continue

            title_id = m.group(1).upper()
            stat = f.stat()
            sf = SaveFile(
                path=f,
                save_type=SaveType.SAVESTATE,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )

            if title_id in seen:
                seen[title_id].save_files.append(sf)
            else:
                seen[title_id] = GameSave(
                    emulator="Citra",
                    game_name=title_id,
                    game_id=title_id,
                    platform="3DS",
                    save_files=[sf],
                )

        return list(seen.values())

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_files(directory: Path, save_type: SaveType) -> list[SaveFile]:
        """Recursively collect all files under *directory* as SaveFile objects."""
        files: list[SaveFile] = []
        try:
            for f in directory.rglob("*"):
                if not f.is_file():
                    continue
                stat = f.stat()
                files.append(SaveFile(
                    path=f,
                    save_type=save_type,
                    size=stat.st_size,
                    modified=datetime.fromtimestamp(stat.st_mtime),
                ))
        except Exception as e:
            logger.debug("Error collecting files from {}: {}", directory, e)
        return files

    @staticmethod
    def _safe_iterdir(directory: Path):
        """Iterate directory entries, ignoring permission errors."""
        try:
            return list(directory.iterdir())
        except PermissionError:
            logger.debug("Permission denied: {}", directory)
            return []

    @staticmethod
    def _merge_saves(saves: list[GameSave]) -> list[GameSave]:
        """Merge GameSave entries that share the same game_id."""
        merged: dict[str, GameSave] = {}
        for gs in saves:
            if gs.game_id in merged:
                merged[gs.game_id].save_files.extend(gs.save_files)
            else:
                merged[gs.game_id] = gs
        return list(merged.values())

    @staticmethod
    def _read_config_paths(config_path: Path) -> dict[str, str]:
        """Read custom directory overrides from Citra's qt-config.ini."""
        overrides: dict[str, str] = {}
        try:
            parser = configparser.ConfigParser(strict=False)
            parser.read(str(config_path), encoding="utf-8")
            # Citra stores paths under [Data%20Storage]
            section = "Data%20Storage"
            if parser.has_section(section):
                if parser.has_option(section, "sdmc_directory"):
                    val = parser.get(section, "sdmc_directory").strip()
                    if val:
                        overrides["sdmc"] = val
                if parser.has_option(section, "nand_directory"):
                    val = parser.get(section, "nand_directory").strip()
                    if val:
                        overrides["nand"] = val
        except Exception as e:
            logger.debug("Error reading Citra config {}: {}", config_path, e)
        return overrides
