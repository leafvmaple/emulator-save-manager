"""Dolphin emulator plugin — detects installations and scans GameCube / Wii game saves."""

from __future__ import annotations

import configparser
import platform
import re
import struct
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave, SaveFile, SaveType
from app.plugins.base import EmulatorPlugin

# ---------------------------------------------------------------------------
# GameCube memory card constants (big-endian)
# ---------------------------------------------------------------------------

# Standard memory card sizes (in bytes)
GC_MEMCARD_SIZES = {
    0x0080000,   # 512 KB  — 59 blocks (standard)
    0x0100000,   # 1 MB    — 123 blocks
    0x0200000,   # 2 MB    — 251 blocks
    0x0400000,   # 4 MB    — 507 blocks (Datel)
    0x0800000,   # 8 MB
    0x1000000,   # 16 MB
}

GC_BLOCK_SIZE = 0x2000          # 8 KB
GC_DIR_OFFSET = GC_BLOCK_SIZE  # Block 1
GC_DIRENTRY_SIZE = 0x40         # 64 bytes per directory entry
GC_DIR_HEADER_SIZE = 0x3A       # 58-byte header before entries
GC_MAX_DIR_ENTRIES = 127

# Save-state filename pattern: <game_id>.s<slot>  (e.g. GALE01.s01)
_SAVESTATE_RE = re.compile(
    r"^(.{6})\.(s\d{2}|sav)$", re.IGNORECASE
)

# Wii title-ID high values
_WII_TITLE_HIGH_DISC = "00010000"    # Disc game
_WII_TITLE_HIGH_CHANNEL = "00010001"  # Channel / WiiWare
_WII_TITLE_HIGH_SYSTEM = "00010002"  # System channel

# GCI filename pattern: <gamecode><makercode>-<filename>.gci
_GCI_RE = re.compile(r"^([A-Z0-9]{4})([A-Z0-9]{2})-(.+)\.gci$", re.IGNORECASE)

# Region folder names used by Dolphin for GC memory cards
_GC_REGIONS = ("USA", "EUR", "JAP")

# GameTDB cover-art URL templates
_GAMETDB_WII_COVER = "https://art.gametdb.com/wii/cover/{region}/{game_id}.png"
_GAMETDB_GC_COVER = "https://art.gametdb.com/wii/cover/{region}/{game_id}.png"

# Region variants for cover art lookup
_COVER_REGIONS = ["US", "EN", "JA", "KO"]


# ---------------------------------------------------------------------------
# GC memory card parsing helpers
# ---------------------------------------------------------------------------

def _parse_gc_memcard_directory(data: bytes) -> list[dict[str, str | int]]:
    """Parse directory entries from a GameCube memory card image.

    Returns a list of dicts with 'game_code', 'maker_code', 'filename',
    'block_count', 'first_block'.
    """
    entries: list[dict[str, str | int]] = []

    if len(data) < GC_DIR_OFFSET + GC_BLOCK_SIZE:
        return entries

    dir_block = data[GC_DIR_OFFSET : GC_DIR_OFFSET + GC_BLOCK_SIZE]

    for i in range(GC_MAX_DIR_ENTRIES):
        offset = GC_DIR_HEADER_SIZE + i * GC_DIRENTRY_SIZE
        if offset + GC_DIRENTRY_SIZE > len(dir_block):
            break

        entry = dir_block[offset : offset + GC_DIRENTRY_SIZE]

        # Game code (4 bytes) — 0xFF means unused
        game_code_raw = entry[0:4]
        if game_code_raw == b"\xff\xff\xff\xff":
            continue

        try:
            game_code = game_code_raw.decode("ascii", errors="ignore").strip("\x00")
        except Exception:
            continue

        if not game_code or len(game_code) < 3:
            continue

        try:
            maker_code = entry[4:6].decode("ascii", errors="ignore").strip("\x00")
        except Exception:
            maker_code = ""

        # Filename (32 bytes at offset 0x08)
        try:
            filename = entry[0x08:0x28].split(b"\x00", 1)[0].decode(
                "ascii", errors="ignore"
            ).strip()
        except Exception:
            filename = ""

        # Block count (u16 big-endian at offset 0x38)
        block_count = struct.unpack_from(">H", entry, 0x38)[0]
        first_block = struct.unpack_from(">H", entry, 0x36)[0]

        entries.append({
            "game_code": game_code,
            "maker_code": maker_code,
            "filename": filename,
            "block_count": block_count,
            "first_block": first_block,
        })

    return entries


def _parse_gci_header(gci_path: Path) -> dict[str, str | int] | None:
    """Parse the 64-byte header of a GCI (individual GameCube save) file."""
    try:
        with open(gci_path, "rb") as f:
            header = f.read(GC_DIRENTRY_SIZE)
        if len(header) < GC_DIRENTRY_SIZE:
            return None

        game_code = header[0:4].decode("ascii", errors="ignore").strip("\x00")
        maker_code = header[4:6].decode("ascii", errors="ignore").strip("\x00")
        filename = header[0x08:0x28].split(b"\x00", 1)[0].decode(
            "ascii", errors="ignore"
        ).strip()
        block_count = struct.unpack_from(">H", header, 0x38)[0]

        if not game_code or len(game_code) < 3:
            return None

        return {
            "game_code": game_code,
            "maker_code": maker_code,
            "filename": filename,
            "block_count": block_count,
        }
    except Exception as e:
        logger.debug("Failed to parse GCI header {}: {}", gci_path, e)
        return None


def _ascii_to_hex(text: str) -> str:  # noqa: F841
    """Convert ASCII text to hex string (e.g. 'RMGE' → '524d4745')."""
    return text.encode("ascii").hex()


def _hex_to_ascii(hex_str: str) -> str:
    """Convert hex string to ASCII (e.g. '524d4745' → 'RMGE'), ignoring errors."""
    try:
        return bytes.fromhex(hex_str).decode("ascii", errors="ignore").strip("\x00")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Dolphin config parsing
# ---------------------------------------------------------------------------

def _read_dolphin_ini(ini_path: Path) -> dict[str, str]:
    """Read Dolphin.ini and return relevant path overrides."""
    result: dict[str, str] = {}
    if not ini_path.is_file():
        return result
    try:
        parser = configparser.ConfigParser(strict=False)
        parser.read(str(ini_path), encoding="utf-8")

        # [General] section may have paths
        if parser.has_section("General"):
            for key in ("GCIFolderAPathOverride", "GCIFolderBPathOverride",
                        "MemcardAPath", "MemcardBPath",
                        "WiiSDCardPath", "NANDRootPath"):
                if parser.has_option("General", key):
                    val = parser.get("General", key).strip()
                    if val:
                        result[key] = val

        # [GBA] section may have GBA saves path (Dolphin supports GBA link)
        # Skip for now
    except Exception as e:
        logger.debug("Failed to parse Dolphin.ini {}: {}", ini_path, e)
    return result


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class DolphinPlugin(EmulatorPlugin):
    """Plugin for Dolphin — GameCube / Wii emulator."""

    @property
    def name(self) -> str:
        return "Dolphin"

    @property
    def display_name(self) -> str:
        return "Dolphin (GameCube / Wii)"

    @property
    def supported_platforms(self) -> list[str]:
        return ["GameCube", "Wii"]

    def get_cover_urls(self, game_id: str) -> list[str]:
        """Return candidate GameTDB cover image URLs for a game ID."""
        if not game_id or len(game_id) < 4:
            return []
        # Use first 6 chars if available, otherwise 4
        gid = game_id[:6] if len(game_id) >= 6 else game_id[:4]
        urls: list[str] = []
        for region in _COVER_REGIONS:
            urls.append(_GAMETDB_WII_COVER.format(region=region, game_id=gid))
        return urls

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_installation(
        self,
        extra_paths: list[Path] | None = None,
    ) -> list[EmulatorInfo]:
        """Detect Dolphin installations on this machine."""
        installations: list[EmulatorInfo] = []
        if platform.system() != "Windows":
            return installations

        candidates: list[Path] = []

        # 0. User-configured paths
        if extra_paths:
            for p in extra_paths:
                if not p.exists():
                    continue
                # If the user pointed at the install root, use User/ as data dir
                user_sub = p / "User"
                if user_sub.exists():
                    if user_sub not in candidates:
                        candidates.append(user_sub)
                elif p not in candidates:
                    candidates.append(p)

        appdata = Path.home() / "AppData" / "Roaming"
        localappdata = Path.home() / "AppData" / "Local"

        # 1. Default: %APPDATA%/Dolphin Emulator  (most common)
        dolphin_appdata = appdata / "Dolphin Emulator"
        if dolphin_appdata.exists():
            candidates.append(dolphin_appdata)

        # 2. %LOCALAPPDATA%/Dolphin Emulator  (some builds)
        dolphin_local = localappdata / "Dolphin Emulator"
        if dolphin_local.exists() and dolphin_local not in candidates:
            candidates.append(dolphin_local)

        # 3. Documents/Dolphin Emulator  (older versions)
        try:
            from app.core.path_resolver import get_documents_dir
            docs_dolphin = get_documents_dir() / "Dolphin Emulator"
            if docs_dolphin.exists() and docs_dolphin not in candidates:
                candidates.append(docs_dolphin)
        except ImportError:
            pass

        # 4. Portable mode — portable.txt next to executable
        for prog_path in [
            Path("C:/Dolphin"),
            Path("C:/Program Files/Dolphin"),
            Path("C:/Program Files (x86)/Dolphin"),
            Path("C:/Dolphin-x64"),
            Path.home() / "scoop" / "apps" / "dolphin",
            Path.home() / "scoop" / "apps" / "dolphin" / "current",
        ]:
            if prog_path.exists():
                # Portable mode: User/ directory next to exe
                user_dir = prog_path / "User"
                portable_txt = prog_path / "portable.txt"
                if user_dir.exists() or portable_txt.exists():
                    candidates.append(user_dir if user_dir.exists() else prog_path)
                elif (prog_path / "Sys").exists():
                    # Installation dir without portable — data is in AppData
                    pass

        for candidate in candidates:
            is_portable = (
                (candidate / "portable.txt").exists()
                or (candidate.parent / "portable.txt").exists()
            )

            # Check for signs of a valid Dolphin data directory
            has_gc = (candidate / "GC").exists()
            has_wii = (candidate / "Wii").exists()
            has_config = (candidate / "Config").exists()
            has_states = (candidate / "StateSaves").exists()
            is_user_path = extra_paths and candidate in extra_paths

            if has_gc or has_wii or has_config or has_states or is_user_path:
                installations.append(EmulatorInfo(
                    name="Dolphin",
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
                logger.info("Detected Dolphin: data_path={}", info.data_path)

        return unique

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan_saves(
        self,
        emulator_info: EmulatorInfo,
        custom_paths: list[Path] | None = None,
    ) -> list[GameSave]:
        """Scan Dolphin GameCube memory cards, Wii NAND saves, and save states."""
        all_saves: list[GameSave] = []
        dirs = self.get_save_directories(emulator_info)

        # -- GameCube memory cards (raw files) --
        gc_dir = dirs.get("gc")
        if gc_dir and gc_dir.exists():
            logger.info("Scanning Dolphin GC saves in {}", gc_dir)
            all_saves.extend(self._scan_gc_saves(gc_dir))

        # -- Wii NAND saves --
        wii_dir = dirs.get("wii")
        if wii_dir and wii_dir.exists():
            logger.info("Scanning Dolphin Wii saves in {}", wii_dir)
            all_saves.extend(self._scan_wii_saves(wii_dir))

        # -- Save states --
        states_dir = dirs.get("savestates")
        if states_dir and states_dir.exists():
            logger.info("Scanning Dolphin save states in {}", states_dir)
            all_saves.extend(self._scan_savestates(states_dir))

        # Custom paths
        if custom_paths:
            for cp in custom_paths:
                if cp.is_dir():
                    all_saves.extend(self._scan_gc_saves(cp))
                    all_saves.extend(self._scan_wii_saves(cp))

        # Merge saves with the same game ID
        all_saves = self._merge_saves(all_saves)

        # Resolve display names from game_names.json
        self.resolve_display_names(all_saves)

        logger.info("Dolphin: found {} game saves", len(all_saves))
        return all_saves

    def get_save_directories(self, emulator_info: EmulatorInfo) -> dict[str, Path]:
        """Return save directory paths, respecting config overrides."""
        dp = emulator_info.data_path

        gc_dir = dp / "GC"
        wii_dir = dp / "Wii"
        states_dir = dp / "StateSaves"

        # Try to read override paths from Dolphin.ini
        config_path = dp / "Config" / "Dolphin.ini"
        overrides = _read_dolphin_ini(config_path)

        if "NANDRootPath" in overrides:
            override = Path(overrides["NANDRootPath"])
            if override.exists():
                wii_dir = override

        return {
            "gc": gc_dir,
            "wii": wii_dir,
            "savestates": states_dir,
        }

    # ------------------------------------------------------------------
    # GameCube saves
    # ------------------------------------------------------------------

    def _scan_gc_saves(self, gc_dir: Path) -> list[GameSave]:
        """Scan GameCube save data — both raw memory cards and GCI folder mode."""
        saves: list[GameSave] = []

        # Iterate region folders (USA, EUR, JAP)
        for region in _GC_REGIONS:
            region_dir = gc_dir / region
            if not region_dir.exists():
                continue

            for item in self._safe_iterdir(region_dir):
                if item.is_file() and item.suffix.lower() in (".raw", ".gcp", ".mci"):
                    # Raw memory card image
                    saves.extend(self._scan_gc_memcard_file(item))
                elif item.is_file() and item.suffix.lower() == ".gci":
                    # Standalone .gci in region folder
                    saves.extend(self._scan_single_gci(item))
                elif item.is_dir():
                    # GCI folder mode (e.g. "Card A/", "Card B/")
                    saves.extend(self._scan_gci_folder(item))

        # Also check for .gci files directly in gc_dir
        for item in self._safe_iterdir(gc_dir):
            if item.is_file() and item.suffix.lower() == ".gci":
                saves.extend(self._scan_single_gci(item))

        return saves

    def _scan_gc_memcard_file(self, memcard_path: Path) -> list[GameSave]:
        """Parse a GameCube memory card image and extract game saves."""
        saves: list[GameSave] = []
        try:
            raw = memcard_path.read_bytes()
        except Exception as e:
            logger.warning("Cannot read GC memory card {}: {}", memcard_path, e)
            return saves

        if len(raw) not in GC_MEMCARD_SIZES and len(raw) < GC_BLOCK_SIZE * 3:
            logger.debug("Unexpected GC memory card size: {} bytes", len(raw))
            return saves

        entries = _parse_gc_memcard_directory(raw)
        if not entries:
            # Treat the whole card as one save
            stat = memcard_path.stat()
            saves.append(GameSave(
                emulator="Dolphin",
                game_name=memcard_path.stem,
                game_id=memcard_path.stem,
                platform="GameCube",
                save_files=[SaveFile(
                    path=memcard_path,
                    save_type=SaveType.MEMCARD,
                    size=stat.st_size,
                    modified=datetime.fromtimestamp(stat.st_mtime),
                )],
            ))
            return saves

        stat = memcard_path.stat()
        # Group entries by game_code to avoid duplicates per card
        seen_games: dict[str, GameSave] = {}
        for entry in entries:
            game_code = str(entry["game_code"])
            maker_code = str(entry["maker_code"])
            game_id = f"{game_code}{maker_code}" if maker_code else game_code

            if game_id in seen_games:
                # Same game — already counted
                continue

            seen_games[game_id] = GameSave(
                emulator="Dolphin",
                game_name=game_id,
                game_id=game_id,
                platform="GameCube",
                save_files=[SaveFile(
                    path=memcard_path,
                    save_type=SaveType.MEMCARD,
                    size=stat.st_size,
                    modified=datetime.fromtimestamp(stat.st_mtime),
                )],
            )

        saves.extend(seen_games.values())
        return saves

    def _scan_gci_folder(self, folder_path: Path) -> list[GameSave]:
        """Scan a Dolphin GCI folder-mode directory for .gci files."""
        saves: list[GameSave] = []
        seen_games: dict[str, GameSave] = {}

        for f in self._safe_iterdir(folder_path):
            if not f.is_file() or f.suffix.lower() != ".gci":
                continue

            header = _parse_gci_header(f)
            if header is None:
                continue

            game_code = str(header["game_code"])
            maker_code = str(header["maker_code"])
            game_id = f"{game_code}{maker_code}" if maker_code else game_code

            stat = f.stat()
            sf = SaveFile(
                path=f,
                save_type=SaveType.BATTERY,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )

            if game_id in seen_games:
                seen_games[game_id].save_files.append(sf)
            else:
                seen_games[game_id] = GameSave(
                    emulator="Dolphin",
                    game_name=game_id,
                    game_id=game_id,
                    platform="GameCube",
                    save_files=[sf],
                )

        saves.extend(seen_games.values())
        return saves

    def _scan_single_gci(self, gci_path: Path) -> list[GameSave]:
        """Scan a single standalone .gci file."""
        header = _parse_gci_header(gci_path)
        if header is None:
            return []

        game_code = str(header["game_code"])
        maker_code = str(header["maker_code"])
        game_id = f"{game_code}{maker_code}" if maker_code else game_code

        stat = gci_path.stat()
        return [GameSave(
            emulator="Dolphin",
            game_name=game_id,
            game_id=game_id,
            platform="GameCube",
            save_files=[SaveFile(
                path=gci_path,
                save_type=SaveType.BATTERY,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )],
        )]

    # ------------------------------------------------------------------
    # Wii NAND saves
    # ------------------------------------------------------------------

    def _scan_wii_saves(self, wii_dir: Path) -> list[GameSave]:
        """Scan Wii NAND title directories for save data.

        Path layout::

            Wii/title/<high>/<low>/data/
        """
        saves: list[GameSave] = []
        title_dir = wii_dir / "title"
        if not title_dir.exists():
            return saves

        for high_dir in self._safe_iterdir(title_dir):
            if not high_dir.is_dir():
                continue
            high = high_dir.name.lower()

            # Only scan disc games and channels/WiiWare
            if high not in (_WII_TITLE_HIGH_DISC, _WII_TITLE_HIGH_CHANNEL):
                continue

            for low_dir in self._safe_iterdir(high_dir):
                if not low_dir.is_dir():
                    continue
                low = low_dir.name.lower()

                data_dir = low_dir / "data"
                if not data_dir.exists():
                    continue

                # Convert hex title ID low to ASCII game code
                game_code = _hex_to_ascii(low)
                if not game_code:
                    game_code = low.upper()

                title_id = f"{high}{low}".upper()
                platform = "Wii"

                save_files = self._collect_files(data_dir, SaveType.FOLDER)
                if save_files:
                    saves.append(GameSave(
                        emulator="Dolphin",
                        game_name=game_code if game_code.isalnum() else title_id,
                        game_id=game_code if game_code.isalnum() else title_id,
                        platform=platform,
                        save_files=save_files,
                    ))

        return saves

    # ------------------------------------------------------------------
    # Save states
    # ------------------------------------------------------------------

    def _scan_savestates(self, states_dir: Path) -> list[GameSave]:
        """Scan Dolphin save state files (.s01–.s08, .sav)."""
        seen_games: dict[str, GameSave] = {}

        for f in self._safe_iterdir(states_dir):
            if not f.is_file():
                continue

            # Match pattern: GALE01.s01, RMGE01.sav, etc.
            m = _SAVESTATE_RE.match(f.name)
            if not m:
                # Also try Dolphin's .dtm (TAS movie) and .gci state filenames
                if f.suffix.lower() in (".dtm",):
                    # TAS recordings — skip, not save data
                    continue
                # Try generic: <game_id>.<anything>
                if len(f.stem) == 6 and f.stem.isalnum():
                    game_id = f.stem.upper()
                else:
                    continue
            else:
                game_id = m.group(1).upper()

            stat = f.stat()
            sf = SaveFile(
                path=f,
                save_type=SaveType.SAVESTATE,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )

            if game_id in seen_games:
                seen_games[game_id].save_files.append(sf)
            else:
                # Guess platform from game ID region code (5th char)
                # GameCube IDs typically start with 'G', Wii with 'R'/'S'
                platform = "GameCube"
                if len(game_id) >= 1 and game_id[0] in ("R", "S", "W", "H"):
                    platform = "Wii"

                seen_games[game_id] = GameSave(
                    emulator="Dolphin",
                    game_name=game_id,
                    game_id=game_id,
                    platform=platform,
                    save_files=[sf],
                )

        return list(seen_games.values())

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
