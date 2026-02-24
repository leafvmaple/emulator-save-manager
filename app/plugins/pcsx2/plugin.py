"""PCSX2 emulator plugin — detects installations and scans PS2 game saves."""

from __future__ import annotations

import platform
import re
import struct
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from loguru import logger

from app.config import _default_data_dir
from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave, SaveFile, SaveType
from app.plugins.base import EmulatorPlugin
from app.plugins.pcsx2.game_db import GameDB

# PS2 memory card constants
PS2_MEMCARD_MAGIC = b"Sony PS2 Memory Card Format"
PS1_MEMCARD_MAGIC = b"MC"
PS2_PAGE_SIZE = 512
PS2_ECC_SIZE = 16
PS2_RAW_PAGE_SIZE = PS2_PAGE_SIZE + PS2_ECC_SIZE  # 528
PS2_CLUSTER_SIZE = PS2_PAGE_SIZE * 2  # 1024
PS2_MEMCARD_8MB = 8 * 1024 * 1024  # 8388608 raw
PS2_SUPERBLOCK_OFFSET = 0

# Directory entry structure (PS2 memory card filesystem)
DIRENTRY_SIZE = 512  # Each directory entry is 512 bytes

# Pattern for save state filenames: SERIAL (CRC32).slot.p2s
_SAVESTATE_RE = re.compile(
    r'^([A-Z]{4}-\d{5})\s*\(([0-9A-Fa-f]{8})\)'
)


def _read_ps2_superblock(data: bytes) -> dict | None:
    """Read the PS2 memory card superblock (first page)."""
    if len(data) < PS2_PAGE_SIZE:
        return None
    magic = data[0:28]
    if not magic.startswith(PS2_MEMCARD_MAGIC):
        return None
    # Superblock fields (partial parse)
    page_size = struct.unpack_from("<H", data, 0x28)[0] if len(data) > 0x2A else 512
    pages_per_cluster = struct.unpack_from("<H", data, 0x2A)[0] if len(data) > 0x2C else 2
    pages_per_block = struct.unpack_from("<H", data, 0x2C)[0] if len(data) > 0x2E else 16
    clusters_per_card = struct.unpack_from("<I", data, 0x30)[0] if len(data) > 0x34 else 8192
    alloc_offset = struct.unpack_from("<I", data, 0x34)[0] if len(data) > 0x38 else 0x34
    alloc_end = struct.unpack_from("<I", data, 0x38)[0] if len(data) > 0x3C else 0x1034
    rootdir_cluster = struct.unpack_from("<I", data, 0x3C)[0] if len(data) > 0x40 else 0
    return {
        "page_size": page_size,
        "pages_per_cluster": pages_per_cluster,
        "pages_per_block": pages_per_block,
        "clusters_per_card": clusters_per_card,
        "alloc_offset": alloc_offset,
        "alloc_end": alloc_end,
        "rootdir_cluster": rootdir_cluster,
    }


def _has_ecc(file_size: int) -> bool:
    """Heuristic: raw (.ps2) files with ECC have sizes that are multiples of 528-page blocks."""
    # A standard 8 MB card without ECC is 8388608 bytes
    # With ECC it's bigger — each 512-byte page has 16 bytes ECC appended
    no_ecc_size = 8 * 1024 * 1024
    with_ecc_sizes = [
        (no_ecc_size // PS2_PAGE_SIZE) * PS2_RAW_PAGE_SIZE,  # ~8.65 MB
    ]
    if file_size in with_ecc_sizes or file_size > no_ecc_size * 1.02:
        return True
    return False


def _strip_ecc(data: bytes) -> bytes:
    """Strip ECC bytes from raw memory card data (528 → 512 per page)."""
    pages = len(data) // PS2_RAW_PAGE_SIZE
    out = bytearray()
    for i in range(pages):
        offset = i * PS2_RAW_PAGE_SIZE
        out.extend(data[offset : offset + PS2_PAGE_SIZE])
    return bytes(out)


def _read_root_directory_entries(data: bytes, superblock: dict) -> list[dict]:
    """Read top-level directory entries from a PS2 memory card image.

    Each directory entry in the root dir represents one game save.
    Returns a list of dicts with 'name', 'length', 'cluster'.
    """
    entries: list[dict] = []
    page_size = superblock.get("page_size", PS2_PAGE_SIZE)
    pages_per_cluster = superblock.get("pages_per_cluster", 2)
    cluster_size = page_size * pages_per_cluster
    alloc_offset = superblock.get("alloc_offset", 0x34)
    rootdir_cluster = superblock.get("rootdir_cluster", 0)

    # Translate logical cluster to byte offset
    def cluster_to_offset(cluster: int) -> int:
        return (alloc_offset + cluster) * cluster_size

    root_offset = cluster_to_offset(rootdir_cluster)

    # Read the '.' entry first to get the number of entries
    if root_offset + DIRENTRY_SIZE > len(data):
        return entries

    dot_entry_data = data[root_offset : root_offset + DIRENTRY_SIZE]
    mode = struct.unpack_from("<I", dot_entry_data, 0)[0] if len(dot_entry_data) >= 4 else 0
    num_entries = struct.unpack_from("<I", dot_entry_data, 4)[0] if len(dot_entry_data) >= 8 else 0

    if num_entries == 0 or num_entries > 1000:
        # Fallback: scan a reasonable number
        num_entries = min(15, (len(data) - root_offset) // DIRENTRY_SIZE)

    for i in range(2, min(num_entries, 100)):  # skip '.' and '..'
        entry_offset = root_offset + i * DIRENTRY_SIZE
        if entry_offset + DIRENTRY_SIZE > len(data):
            break
        entry_data = data[entry_offset : entry_offset + DIRENTRY_SIZE]
        e_mode = struct.unpack_from("<I", entry_data, 0)[0]
        e_length = struct.unpack_from("<I", entry_data, 4)[0]

        # Check if entry is a used directory (game save folders are directories)
        # Mode bit 0x8427 = exists | directory | readable | writable | executable
        if not (e_mode & 0x8000):  # not in use
            continue

        # Entry name starts at offset 0x20 (32) and is up to 32 bytes
        raw_name = entry_data[0x20:0x40]
        try:
            name = raw_name.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()
        except Exception:
            name = ""

        if name and name not in (".", ".."):
            # Cluster of this entry's first data
            e_cluster = struct.unpack_from("<I", entry_data, 0x10)[0]
            entries.append({
                "name": name,
                "length": e_length,
                "cluster": e_cluster,
            })
    return entries


def _extract_game_id_from_dirname(dirname: str) -> str:
    """Extract a game serial from a PS2 save directory name.

    PS2 save directories are typically named like:
      BADATA-SYSTEM         (system data)
      BASLUS-21005INGS001   (game serial + save suffix)
      BESLES-12345SAVE00

    The serial is the first 11 characters following the initial 2-char region prefix.
    """
    # Common format: 2-char prefix + serial (e.g. "BASLUS-21005...")
    if len(dirname) >= 12:
        potential = dirname[2:13]  # e.g. "SLUS-21005I"
        # Check if it looks like a serial (XXXX-NNNNN)
        if len(potential) >= 10 and potential[4] == "-" and potential[5:10].isdigit():
            return potential[:10]  # e.g. "SLUS-21005"
    # Fallback: return the directory name itself
    return dirname


def _scan_memcard_file(memcard_path: Path) -> list[GameSave]:
    """Parse a PS2 memory card image file and extract game saves."""
    saves: list[GameSave] = []
    try:
        raw = memcard_path.read_bytes()
    except Exception as e:
        logger.warning("Cannot read memory card {}: {}", memcard_path, e)
        return saves

    # Strip ECC if present
    if _has_ecc(len(raw)):
        raw = _strip_ecc(raw)

    superblock = _read_ps2_superblock(raw)
    if superblock is None:
        # Check for PS1 memory card
        if raw[:2] == PS1_MEMCARD_MAGIC:
            logger.debug("PS1 memory card detected: {}", memcard_path)
            # Basic PS1 detection — treat the whole card as one save
            stat = memcard_path.stat()
            saves.append(GameSave(
                emulator="PCSX2",
                game_name=memcard_path.stem,
                game_id=memcard_path.stem,
                platform="PS1",
                save_files=[SaveFile(
                    path=memcard_path,
                    save_type=SaveType.MEMCARD,
                    size=stat.st_size,
                    modified=datetime.fromtimestamp(stat.st_mtime),
                )],
            ))
        else:
            logger.debug("Unknown memory card format: {}", memcard_path)
        return saves

    # Parse directory entries to find individual game saves
    entries = _read_root_directory_entries(raw, superblock)
    if not entries:
        # If parsing fails, treat the whole card as one unit
        stat = memcard_path.stat()
        saves.append(GameSave(
            emulator="PCSX2",
            game_name=memcard_path.stem,
            game_id=memcard_path.stem,
            platform="PS2",
            save_files=[SaveFile(
                path=memcard_path,
                save_type=SaveType.MEMCARD,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )],
        ))
        return saves

    stat = memcard_path.stat()
    for entry in entries:
        dirname = entry["name"]
        # Skip system entries
        if dirname.startswith("BADATA-SYSTEM") or dirname.startswith("!"):
            continue
        game_id = _extract_game_id_from_dirname(dirname)
        saves.append(GameSave(
            emulator="PCSX2",
            game_name=dirname,
            game_id=game_id,
            platform="PS2",
            save_files=[SaveFile(
                path=memcard_path,
                save_type=SaveType.MEMCARD,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )],
        ))
    return saves


def _scan_folder_memcard(folder_path: Path) -> list[GameSave]:
    """Scan a PCSX2 folder-type memory card.

    Folder-type memory cards have a ``_pcsx2_superblock`` file and each
    game save is a subdirectory.
    """
    saves: list[GameSave] = []
    if not (folder_path / "_pcsx2_superblock").exists():
        return saves

    for child in folder_path.iterdir():
        if not child.is_dir():
            continue
        dirname = child.name
        if dirname.startswith(".") or dirname.startswith("_"):
            continue
        # Calculate total size of this save directory
        total_size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
        latest_mtime = max(
            (f.stat().st_mtime for f in child.rglob("*") if f.is_file()),
            default=0,
        )
        game_id = _extract_game_id_from_dirname(dirname)
        save_files = [
            SaveFile(
                path=child,
                save_type=SaveType.FOLDER,
                size=total_size,
                modified=datetime.fromtimestamp(latest_mtime) if latest_mtime else datetime.now(),
            )
        ]
        saves.append(GameSave(
            emulator="PCSX2",
            game_name=dirname,
            game_id=game_id,
            platform="PS2",
            save_files=save_files,
        ))
    return saves


def _scan_savestates(sstates_path: Path) -> list[GameSave]:
    """Scan PCSX2 save state directory for .p2s files."""
    saves: list[GameSave] = []
    if not sstates_path.exists():
        return saves
    seen_games: dict[str, GameSave] = {}
    for f in sstates_path.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() not in (".p2s", ".p2s.backup"):
            continue
        # Parse filename: "SLPS-25733 (083F0E03).00.p2s"
        filename_base = f.stem.split(".")[0]  # "SLPS-25733 (083F0E03)"
        match = _SAVESTATE_RE.match(filename_base)
        if match:
            serial = match.group(1)    # "SLPS-25733"
            crc32 = match.group(2)     # "083F0E03"
        else:
            serial = filename_base
            crc32 = ""

        stat = f.stat()
        sf = SaveFile(
            path=f,
            save_type=SaveType.SAVESTATE,
            size=stat.st_size,
            modified=datetime.fromtimestamp(stat.st_mtime),
        )
        if serial in seen_games:
            seen_games[serial].save_files.append(sf)
            # Keep the CRC if we found one
            if crc32 and not seen_games[serial].crc32:
                seen_games[serial].crc32 = crc32
        else:
            gs = GameSave(
                emulator="PCSX2",
                game_name=serial,
                game_id=serial,
                platform="PS2",
                save_files=[sf],
                crc32=crc32,
            )
            seen_games[serial] = gs
    saves.extend(seen_games.values())
    return saves


class PCSX2Plugin(EmulatorPlugin):
    """Plugin for PCSX2 — PlayStation 2 emulator."""

    _game_db: GameDB | None = None

    @classmethod
    def _get_game_db(cls) -> GameDB:
        if cls._game_db is None:
            cache_dir = _default_data_dir() / "cache"
            cls._game_db = GameDB(cache_dir)
        return cls._game_db

    @property
    def name(self) -> str:
        return "PCSX2"

    @property
    def display_name(self) -> str:
        return "PCSX2 (PlayStation 2)"

    @property
    def supported_platforms(self) -> list[str]:
        return ["PS2", "PS1"]

    def detect_installation(self) -> list[EmulatorInfo]:
        """Detect PCSX2 installations by checking common locations."""
        installations: list[EmulatorInfo] = []
        if platform.system() != "Windows":
            # TODO: Linux/macOS support
            return installations

        candidates: list[Path] = []

        # 1. Documents/PCSX2 (default data dir)
        docs_path = Path.home() / "Documents" / "PCSX2"
        if docs_path.exists():
            candidates.append(docs_path)

        # 2. Check registry for install path
        try:
            import winreg
            for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for sub in ("SOFTWARE\\PCSX2", "SOFTWARE\\WOW6432Node\\PCSX2"):
                    try:
                        key = winreg.OpenKey(root_key, sub)
                        install_dir, _ = winreg.QueryValueEx(key, "Install_Dir")
                        winreg.CloseKey(key)
                        p = Path(install_dir)
                        if p.exists() and p not in candidates:
                            candidates.append(p)
                    except (FileNotFoundError, OSError):
                        continue
        except ImportError:
            pass

        # 3. Common install locations
        for prog_dir in [
            Path("C:/Program Files/PCSX2"),
            Path("C:/Program Files (x86)/PCSX2"),
            Path.home() / "scoop" / "apps" / "pcsx2",
        ]:
            if prog_dir.exists() and prog_dir not in candidates:
                candidates.append(prog_dir)

        # Evaluate candidates
        for candidate in candidates:
            # Determine if this is a data directory or install directory
            memcards_dir = candidate / "memcards"
            inis_dir = candidate / "inis"
            is_portable = (candidate / "portable.ini").exists()

            if memcards_dir.exists() or inis_dir.exists():
                data_path = candidate
            else:
                # This might be just the install dir; data is in Documents
                data_path = docs_path if docs_path.exists() else candidate

            installations.append(EmulatorInfo(
                name="PCSX2",
                install_path=candidate,
                data_path=data_path,
                supported_platforms=["PS2", "PS1"],
                is_portable=is_portable,
            ))

        # De-duplicate based on data_path
        seen: set[str] = set()
        unique: list[EmulatorInfo] = []
        for info in installations:
            key = str(info.data_path)
            if key not in seen:
                seen.add(key)
                unique.append(info)
                logger.info("Detected PCSX2: data_path={}", info.data_path)

        return unique

    def scan_saves(
        self,
        emulator_info: EmulatorInfo,
        custom_paths: list[Path] | None = None,
    ) -> list[GameSave]:
        """Scan PCSX2 memory cards and save states for game saves."""
        all_saves: list[GameSave] = []
        dirs = self.get_save_directories(emulator_info)

        # Scan memory cards
        memcards_dir = dirs.get("memcards")
        if memcards_dir and memcards_dir.exists():
            logger.info("Scanning PCSX2 memory cards in {}", memcards_dir)
            for item in memcards_dir.iterdir():
                if item.is_file() and item.suffix.lower() in (".ps2", ".mcr", ".mcd", ".bin", ".mc2"):
                    all_saves.extend(_scan_memcard_file(item))
                elif item.is_dir() and (item / "_pcsx2_superblock").exists():
                    all_saves.extend(_scan_folder_memcard(item))

        # Scan save states
        sstates_dir = dirs.get("savestates")
        if sstates_dir and sstates_dir.exists():
            logger.info("Scanning PCSX2 save states in {}", sstates_dir)
            all_saves.extend(_scan_savestates(sstates_dir))

        # Custom paths
        if custom_paths:
            for cp in custom_paths:
                if cp.is_dir():
                    for item in cp.iterdir():
                        if item.is_file() and item.suffix.lower() in (".ps2", ".mcr", ".mcd"):
                            all_saves.extend(_scan_memcard_file(item))
                        elif item.is_dir() and (item / "_pcsx2_superblock").exists():
                            all_saves.extend(_scan_folder_memcard(item))

        # --- Merge CRC32 across save types for the same serial ---
        crc_map: dict[str, str] = {}
        for s in all_saves:
            if s.crc32:
                crc_map[s.game_id] = s.crc32
        for s in all_saves:
            if not s.crc32 and s.game_id in crc_map:
                s.crc32 = crc_map[s.game_id]

        # --- Resolve display names from GameDB ---
        self._resolve_display_names(
            all_saves,
            pcsx2_paths=[emulator_info.install_path, emulator_info.data_path],
        )

        logger.info("PCSX2: found {} game saves", len(all_saves))
        return all_saves

    def _resolve_display_names(
        self,
        saves: list[GameSave],
        pcsx2_paths: list[Path],
    ) -> None:
        """Set human-readable game names from the PCSX2 GameIndex DB."""
        from app.i18n import get_current_language

        db = self._get_game_db()
        if not db.is_loaded:
            db.load(pcsx2_paths)

        if not db.is_loaded:
            logger.warning("GameDB not available — using serial as display name")
            return

        lang = get_current_language()
        for save in saves:
            display = db.get_name(save.game_id, lang)
            if display:
                save.game_name = display
            # else keep the existing name (serial or directory name)

    def get_save_directories(self, emulator_info: EmulatorInfo) -> dict[str, Path]:
        dp = emulator_info.data_path
        return {
            "memcards": dp / "memcards",
            "savestates": dp / "sstates",
        }
