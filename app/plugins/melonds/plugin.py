"""melonDS emulator plugin — detects installations and scans Nintendo DS game saves."""

from __future__ import annotations

import configparser
import platform
import re
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave, SaveFile, SaveType
from app.plugins.base import EmulatorPlugin

# Save-state filename pattern: {romName}.ds{slot}  (e.g. game.ds0, game.ds1 …)
_SAVESTATE_RE = re.compile(
    r"^(.+)\.(ds\d+)$", re.IGNORECASE
)

# Battery save extensions
_BATTERY_EXTENSIONS = {".dsv"}

# melonDS config filenames (v1.0+ uses TOML, older uses INI)
_CONFIG_FILENAMES = ["melonDS.toml", "melonDS.ini"]


def _read_melonds_toml(toml_path: Path) -> dict[str, str]:
    """Read melonDS TOML config and return relevant path settings.

    melonDS 1.0+ uses a TOML config file. We look for keys like:
        SaveFilePath, SavestatePath
    within the top-level or [Paths] / [Instance0] sections.
    """
    result: dict[str, str] = {}
    if not toml_path.is_file():
        return result
    try:
        text = toml_path.read_text(encoding="utf-8", errors="replace")
        # Try stdlib tomllib (Python 3.11+) first, fall back to manual parsing
        parsed: dict[str, object]
        try:
            import tomllib
            parsed = tomllib.loads(text)
        except ImportError:
            try:
                import tomli  # type: ignore[import-untyped]
                parsed = dict(tomli.loads(text))  # type: ignore[reportUnknownMemberType]
            except ImportError:
                parsed = dict(_simple_toml_parse(text))

        # melonDS may store paths at top level or under various sections
        # Flatten one level for lookup
        flat: dict[str, str] = {}
        for k in parsed:
            v = parsed[k]
            if isinstance(v, str):
                flat[k] = v
            elif isinstance(v, dict):
                sub = dict(v)  # type: ignore[var-annotated]
                for k2, v2 in sub.items():  # type: ignore[reportUnknownVariableType]
                    if isinstance(v2, str) and isinstance(k2, str):
                        flat[k2] = v2

        for key in ("SaveFilePath", "SavestatePath", "SavesPath", "StatesPath"):
            if key in flat and flat[key]:
                result[key] = flat[key]

    except Exception as e:
        logger.debug("Failed to parse melonDS TOML config {}: {}", toml_path, e)
    return result


def _read_melonds_ini(ini_path: Path) -> dict[str, str]:
    """Read melonDS INI config (older versions) and return path settings."""
    result: dict[str, str] = {}
    if not ini_path.is_file():
        return result
    try:
        text = ini_path.read_text(encoding="utf-8", errors="replace")
        # melonDS INI has no section headers, prepend a dummy one
        cp = configparser.ConfigParser(strict=False)
        cp.read_string(f"[melonDS]\n{text}")
        for key in ("SaveFilePath", "SavestatePath", "SavesPath", "StatesPath"):
            if cp.has_option("melonDS", key):
                val = cp.get("melonDS", key).strip()
                if val:
                    result[key] = val
    except Exception as e:
        logger.debug("Failed to parse melonDS INI config {}: {}", ini_path, e)
    return result


def _simple_toml_parse(text: str) -> dict[str, str]:
    """Very simple TOML key=value parser (no nested tables, arrays, etc.).

    Only handles bare ``key = "value"`` and ``key = value`` lines.
    Enough for extracting melonDS path settings.
    """
    data: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and val:
            data[key] = val
    return data


class MelonDSPlugin(EmulatorPlugin):
    """Plugin for melonDS — Nintendo DS emulator."""

    @property
    def name(self) -> str:
        return "melonDS"

    @property
    def display_name(self) -> str:
        return "melonDS (Nintendo DS)"

    @property
    def supported_platforms(self) -> list[str]:
        return ["NDS"]

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_installation(
        self,
        extra_paths: list[Path] | None = None,
    ) -> list[EmulatorInfo]:
        """Detect melonDS installations on this machine."""
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
        localappdata = Path.home() / "AppData" / "Local"

        # 1. Default: %APPDATA%/melonDS  (melonDS 1.0+)
        melonds_appdata = appdata / "melonDS"
        if melonds_appdata.exists():
            candidates.append(melonds_appdata)

        # 2. %LOCALAPPDATA%/melonDS  (some builds)
        melonds_local = localappdata / "melonDS"
        if melonds_local.exists() and melonds_local not in candidates:
            candidates.append(melonds_local)

        # 3. Portable mode — melonDS.ini / melonDS.toml next to executable
        for prog_path in [
            Path("C:/melonDS"),
            Path("C:/Program Files/melonDS"),
            Path("C:/Program Files (x86)/melonDS"),
            Path.home() / "scoop" / "apps" / "melonds",
            Path.home() / "scoop" / "apps" / "melonds" / "current",
        ]:
            if prog_path.exists():
                has_config = any(
                    (prog_path / cfg).exists() for cfg in _CONFIG_FILENAMES
                )
                if has_config:
                    candidates.append(prog_path)

        for candidate in candidates:
            is_portable = not str(candidate).startswith(str(appdata)) and not str(
                candidate
            ).startswith(str(localappdata))

            # Check for signs of a valid melonDS data directory
            has_config = any(
                (candidate / cfg).exists() for cfg in _CONFIG_FILENAMES
            )
            has_saves = any(
                (candidate / d).exists()
                for d in ("Battery", "Savestates", "StateSlots")
            )
            has_exe = (candidate / "melonDS.exe").exists()
            is_user_path = extra_paths and candidate in extra_paths

            if has_config or has_saves or has_exe or is_user_path:
                installations.append(
                    EmulatorInfo(
                        name="melonDS",
                        install_path=candidate.parent if is_portable else candidate,
                        data_path=candidate,
                        supported_platforms=self.supported_platforms,
                        is_portable=is_portable,
                    )
                )

        # De-duplicate by data_path
        seen: set[str] = set()
        unique: list[EmulatorInfo] = []
        for info in installations:
            key = str(info.data_path)
            if key not in seen:
                seen.add(key)
                unique.append(info)
                logger.info("Detected melonDS: data_path={}", info.data_path)

        return unique

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan_saves(
        self,
        emulator_info: EmulatorInfo,
        custom_paths: list[Path] | None = None,
    ) -> list[GameSave]:
        """Scan melonDS battery saves and save states."""
        all_saves: list[GameSave] = []
        dirs = self.get_save_directories(emulator_info)

        # -- Battery saves (.sav) --
        saves_dir = dirs.get("saves")
        if saves_dir and saves_dir.exists():
            all_saves.extend(self._scan_battery_saves(saves_dir))

        # -- Save states (.ds0, .ds1, …) --
        states_dir = dirs.get("savestates")
        if states_dir and states_dir.exists():
            all_saves.extend(self._scan_savestates(states_dir))

        # Custom paths
        if custom_paths:
            for cp in custom_paths:
                if cp.is_dir():
                    all_saves.extend(self._scan_battery_saves(cp))
                    all_saves.extend(self._scan_savestates(cp))

        # Merge saves with the same game ID
        all_saves = self._merge_saves(all_saves)

        # Resolve display names from game_names.json (if present)
        self.resolve_display_names(all_saves)

        logger.info("melonDS: found {} game saves", len(all_saves))
        return all_saves

    def get_save_directories(self, emulator_info: EmulatorInfo) -> dict[str, Path]:
        """Return save and save-state directories, respecting config overrides."""
        dp = emulator_info.data_path

        # Defaults — melonDS uses "Battery" for battery saves and
        # "Savestates" or "StateSlots" for save states.
        saves_dir = dp / "Battery"
        if not saves_dir.exists():
            saves_dir = dp  # fallback: scan data root

        states_dir = dp / "Savestates"
        if not states_dir.exists():
            states_dir = dp / "StateSlots"
        if not states_dir.exists():
            states_dir = dp  # fallback: scan data root

        # Try to read override paths from config
        config_overrides = self._read_config(dp)
        if "SaveFilePath" in config_overrides:
            override = Path(config_overrides["SaveFilePath"])
            if override.exists():
                saves_dir = override
        elif "SavesPath" in config_overrides:
            override = Path(config_overrides["SavesPath"])
            if override.exists():
                saves_dir = override

        if "SavestatePath" in config_overrides:
            override = Path(config_overrides["SavestatePath"])
            if override.exists():
                states_dir = override
        elif "StatesPath" in config_overrides:
            override = Path(config_overrides["StatesPath"])
            if override.exists():
                states_dir = override

        return {
            "saves": saves_dir,
            "savestates": states_dir,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_config(data_path: Path) -> dict[str, str]:
        """Read melonDS config file (TOML or INI)."""
        toml_path = data_path / "melonDS.toml"
        if toml_path.is_file():
            return _read_melonds_toml(toml_path)

        ini_path = data_path / "melonDS.ini"
        if ini_path.is_file():
            return _read_melonds_ini(ini_path)

        return {}

    def _scan_battery_saves(self, saves_dir: Path) -> list[GameSave]:
        """Scan battery save files (.sav)."""
        saves: list[GameSave] = []
        seen_roms: dict[str, GameSave] = {}

        for f in self._safe_iterdir(saves_dir):
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext not in _BATTERY_EXTENSIONS:
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
                    emulator="melonDS",
                    game_name=rom_name,
                    game_id=rom_name,
                    platform="NDS",
                    save_files=[sf],
                )
                seen_roms[rom_name] = gs

        saves.extend(seen_roms.values())
        return saves

    def _scan_savestates(self, states_dir: Path) -> list[GameSave]:
        """Scan melonDS save state files (.ds0, .ds1, …)."""
        saves: list[GameSave] = []
        seen_roms: dict[str, GameSave] = {}

        for f in self._safe_iterdir(states_dir):
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
                    emulator="melonDS",
                    game_name=rom_name,
                    game_id=rom_name,
                    platform="NDS",
                    save_files=[sf],
                )
                seen_roms[rom_name] = gs

        saves.extend(seen_roms.values())
        return saves

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_iterdir(directory: Path) -> list[Path]:
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
