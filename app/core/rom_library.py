"""ROM library scanner (roadmap ROM Stage A + DAT verification).

Scans user-configured ROM directories, hashes each file with CRC32
(the checksum No-Intro DATs key on) behind an mtime/size cache, groups
duplicate dumps by content, verifies CRCs against the loaded DAT index
and links ROMs to scanned saves by filename stem — the same key the
cartridge-family plugins use as ``game_id``.

Write policy: the scan never moves, renames or deletes a file, but
*convergent repairs* are allowed — edits whose result lands exactly on
a known-good DAT hash.  Concretely: a ``.nes`` file whose CRC misses
the DAT is re-tried with every header seen in the headered NES DAT
(iNES 1.0 / NES 2.0 container differences), and on a hit the header is
fixed in place — original preserved as a sibling ``.bak`` (first write
wins), new content written atomically.  The rewrite is self-verifying:
it only happens when ``known_header + body`` hashes to a DAT entry.
The save-aware rename/normalize work is Stage B (see ROADMAP.md).
"""

from __future__ import annotations

import json
import os
import shutil
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from loguru import logger

from app.config import Config
from app.core.custom_db import DB_FILENAME, load_custom_db
from app.core.dat_index import DatGame, DatIndex, load_dat_index
from app.core.rom_headers import parse_embedded_identity
from app.models.game_save import GameSave

CACHE_VERSION = 1

#: Lower-case extension -> platform display label.  ``.zip`` is handled
#: separately as a container (the inner ROM's extension decides).
ROM_EXTENSIONS: dict[str, str] = {
    ".nes": "NES",
    ".fds": "NES",
    ".sfc": "SNES",
    ".smc": "SNES",
    ".gb": "Game Boy",
    ".gbc": "Game Boy",
    ".gba": "GBA",
    ".nds": "NDS",
    ".dsi": "NDS",
    ".3ds": "3DS",
    ".cci": "3DS",
    ".cxi": "3DS",
    ".n64": "N64",
    ".z64": "N64",
    ".v64": "N64",
    ".gcm": "GameCube",
    ".gcz": "GameCube",
    ".rvz": "GameCube/Wii",
    ".wbfs": "Wii",
    ".pce": "PC Engine",
    ".sms": "SMS",
    ".gg": "Game Gear",
    ".md": "Mega Drive",
    ".gen": "Mega Drive",
    ".smd": "Mega Drive",
    ".ws": "WonderSwan",
    ".wsc": "WonderSwan",
    ".chd": "Disc",
    ".cso": "Disc",
    ".iso": "Disc",
}

#: Plugins whose ``game_id`` is the ROM filename stem.  Stem-matching
#: (and save-orphaning on rename) is only meaningful for these; the
#: serial/title-id keyed plugins (PCSX2, Citra, Dolphin) are excluded
#: so their saves don't show up as noise in "saves without ROMs".
FILENAME_KEYED_EMULATORS = frozenset({"Snes9x", "Mesen", "melonDS", "RetroArch"})

#: Container extensions we look inside. Dispatch is by magic bytes, not
#: by this suffix — scene sets have been seen shipping RAR files with a
#: ``.7z`` extension.
ARCHIVE_EXTENSIONS = frozenset({".zip", ".7z", ".rar"})

_ZIP_MAGIC = b"PK"
_7Z_MAGIC = b"7z\xbc\xaf\x27\x1c"
_RAR_MAGIC = b"Rar!"

_HASH_CHUNK = 1024 * 1024
_NES_MAGIC = b"NES\x1a"
_REPAIR_MAX_SIZE = 32 * 1024 * 1024


def _match_nes_dump(hdr: bytes, body: bytes, dat: DatIndex) -> DatGame | None:
    """Return the NES DAT entry that ``hdr + body`` is, or ``None``.

    CRC32 alone is 32 bits and the repair tries ~1000 headers per file,
    so a bare hash lookup across a multi-platform index WILL eventually
    collide (observed in the field: an aftermarket NES dump "matched" a
    GBC game).  A candidate must clear all three factors: CRC, platform
    ``NES``, and — when the DAT records it — the exact dump size.
    """
    crc_str = f"{zlib.crc32(hdr + body) & 0xFFFFFFFF:08X}"
    game = dat.by_crc.get(crc_str)
    if game is None or game.platform != "NES":
        return None
    if game.size and game.size != len(hdr) + len(body):
        return None
    return game


def _repair_nes_header(path: Path, dat: DatIndex) -> tuple[str, DatGame] | None:
    """Try every known iNES header against *path*; fix in place on a DAT hit.

    Self-verifying: the file is only rewritten when ``known_header +
    body`` hashes to a DAT entry, so the result is byte-identical to the
    canonical dump.  The original is preserved once as a sibling
    ``<name>.nes.bak`` — never overwritten, so it always holds the
    pristine pre-repair file — and the fix lands via an atomic replace.

    Returns ``(crc32, DatGame)`` on success, ``None`` otherwise.
    """
    try:
        size = path.stat().st_size
        if size > _REPAIR_MAX_SIZE or size < 16:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if data[:4] != _NES_MAGIC:
        return None

    body = data[16:]
    for hdr in dat.nes_headers:
        game = _match_nes_dump(hdr, body, dat)
        if game is None:
            continue
        crc_str = f"{zlib.crc32(hdr + body) & 0xFFFFFFFF:08X}"
        try:
            bak = path.with_suffix(path.suffix + ".bak")
            if not bak.exists():
                shutil.copy2(path, bak)
            tmp = path.with_suffix(path.suffix + ".repair-tmp")
            tmp.write_bytes(hdr + body)
            os.replace(tmp, path)
        except OSError as e:
            logger.error("Header repair failed for {}: {}", path, e)
            return None
        logger.info(
            "Repaired iNES header: {} → {} ({})", path.name, crc_str, game.name)
        return crc_str, game
    return None


def _repair_nes_in_zip(zip_path: Path, dat: DatIndex) -> tuple[str, DatGame] | None:
    """Fix a non-standard iNES header inside a ``.zip`` archive.

    Same contract as :func:`_repair_nes_header`: only rewrites when a
    known header + body hashes to a DAT entry.  The whole archive is
    preserved once as ``<name>.zip.bak`` (first write wins) and the
    rewritten archive — repaired member plus every other member byte
    for byte — replaces the original atomically.
    """
    try:
        if zip_path.stat().st_size > _REPAIR_MAX_SIZE:
            return None
        with zipfile.ZipFile(zip_path, "r") as zf:
            nes_members = [
                m for m in zf.infolist()
                if not m.is_dir()
                and Path(m.filename).suffix.lower() == ".nes"
            ]
            if not nes_members:
                return None
            target = max(nes_members, key=lambda m: m.file_size)
            data = zf.read(target.filename)
    except (OSError, zipfile.BadZipFile) as e:
        logger.warning("Cannot read zip for repair {}: {}", zip_path, e)
        return None

    if len(data) < 16 or data[:4] != _NES_MAGIC:
        return None
    body = data[16:]
    for hdr in dat.nes_headers:
        game = _match_nes_dump(hdr, body, dat)
        if game is None:
            continue
        crc_str = f"{zlib.crc32(hdr + body) & 0xFFFFFFFF:08X}"
        tmp = zip_path.with_suffix(zip_path.suffix + ".repair-tmp")
        try:
            bak = zip_path.with_suffix(zip_path.suffix + ".bak")
            if not bak.exists():
                shutil.copy2(zip_path, bak)
            with zipfile.ZipFile(zip_path, "r") as src, \
                    zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
                for m in src.infolist():
                    if m.is_dir():
                        dst.writestr(m, b"")
                    elif m.filename == target.filename:
                        dst.writestr(m, hdr + body)
                    else:
                        dst.writestr(m, src.read(m.filename))
            os.replace(tmp, zip_path)
        except (OSError, zipfile.BadZipFile) as e:
            logger.error("Zip header repair failed for {}: {}", zip_path, e)
            try:
                tmp.unlink()
            except OSError:
                pass
            return None
        logger.info(
            "Repaired iNES header in zip: {} → {} ({})",
            zip_path.name, crc_str, game.name)
        return crc_str, game
    return None


@dataclass
class RomFile:
    """A single ROM file (or single-ROM archive) found in the library."""

    path: Path
    size: int
    modified: datetime
    platform: str
    crc32: str = ""
    """CRC32 of the ROM content (upper-case hex, '' when unhashable).
    For ``.zip`` archives this is the inner ROM's CRC from the central
    directory — the value No-Intro DATs record — not the archive's."""

    dat_name: str = ""
    """Canonical No-Intro name when the CRC matches the DAT index."""

    repaired: bool = False
    """True when this scan fixed the file's header to the DAT-canonical
    one (original kept as a sibling ``.bak``)."""

    rom_id: str = ""
    """Embedded 4-char game code (GBA/NDS) — survives fan patching."""

    rom_title: str = ""
    """Embedded cartridge title (GB/GBA/NDS)."""

    custom_name: str = ""
    """Display name from the user-curated custom DB (games_custom.json)."""

    derived_from: str = ""
    """DAT name of the base game when this ROM misses the DAT but a
    verified library sibling shares its embedded identity (typical for
    fan translations and hacks)."""

    @property
    def stem(self) -> str:
        """Filename without extension — what filename-keyed plugins use."""
        return self.path.stem


@dataclass
class RomLibraryReport:
    """Analysis of a scanned library against the current save scan."""

    roms: list[RomFile] = field(default_factory=list)
    duplicate_groups: list[list[RomFile]] = field(default_factory=list)
    """Groups of 2+ ROMs sharing the same content CRC32."""
    matched: dict[Path, GameSave] = field(default_factory=dict)
    """ROM path -> the GameSave whose game_id equals the ROM stem."""
    saves_without_roms: list[GameSave] = field(default_factory=list)
    """Filename-keyed saves with no ROM in the library (moved/renamed?)."""

    dat_games: int = 0
    """Number of entries in the loaded DAT index (0 = no DATs)."""

    @property
    def roms_without_saves(self) -> list[RomFile]:
        return [r for r in self.roms if r.path not in self.matched]

    @property
    def duplicate_count(self) -> int:
        """Number of redundant files (each group counts size-1)."""
        return sum(len(g) - 1 for g in self.duplicate_groups)

    @property
    def verified_count(self) -> int:
        """ROMs whose CRC matched a DAT entry (incl. repaired ones)."""
        return sum(1 for r in self.roms if r.dat_name)

    @property
    def repaired_count(self) -> int:
        """ROMs whose header this scan fixed to the DAT-canonical one."""
        return sum(1 for r in self.roms if r.repaired)

    @property
    def derived_count(self) -> int:
        """ROMs identified as derived versions (translations / hacks)."""
        return sum(1 for r in self.roms if r.derived_from)

    @property
    def custom_count(self) -> int:
        """ROMs identified through the user-curated custom DB."""
        return sum(1 for r in self.roms if r.custom_name)


class RomLibrary:
    """Scans configured ROM directories with a persistent hash cache."""

    def __init__(self, config: Config) -> None:
        self._cfg = config
        self.dat_games = 0
        """DAT index size seen by the most recent scan()."""

    @property
    def rom_dirs(self) -> list[Path]:
        return [Path(p) for p in self._cfg.rom_dirs if p]

    @property
    def dat_dir(self) -> Path:
        return self._cfg.dat_dir

    @property
    def custom_db_path(self) -> Path:
        return self._cfg.data_dir / DB_FILENAME

    @property
    def _cache_path(self) -> Path:
        return self._cfg.data_dir / "rom_hash_cache.json"

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan(
        self,
        should_cancel: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[RomFile]:
        """Enumerate + hash all ROM files under the configured directories.

        Unchanged files (same size + mtime) reuse their cached CRC32, so
        rescans only hash new or modified ROMs.  *progress* is called as
        ``progress(done, total)`` per file.
        """
        candidates: list[Path] = []
        seen: set[Path] = set()
        for d in self.rom_dirs:
            if not d.is_dir():
                logger.warning("ROM directory does not exist: {}", d)
                continue
            for f in sorted(d.rglob("*")):
                ext = f.suffix.lower()
                if ext not in ROM_EXTENSIONS and ext not in ARCHIVE_EXTENSIONS:
                    continue
                if not f.is_file():
                    continue
                try:
                    resolved = f.resolve()
                except OSError:
                    resolved = f
                if resolved in seen:  # overlapping configured dirs
                    continue
                seen.add(resolved)
                candidates.append(f)

        dat = load_dat_index(self.dat_dir)
        self.dat_games = dat.game_count
        custom = load_custom_db(self.custom_db_path)

        cache = self._load_cache()
        roms: list[RomFile] = []
        dirty = False
        total = len(candidates)
        for i, f in enumerate(candidates, start=1):
            if should_cancel and should_cancel():
                logger.info("ROM scan cancelled ({}/{})", i - 1, total)
                break
            if progress:
                progress(i, total)
            try:
                stat = f.stat()
            except OSError:
                continue

            key = str(f)
            entry = cache.get(key)
            if (
                entry
                and not entry.get("crc32")
                and entry.get("platform", "Unknown") in ("", "Unknown")
            ):
                # A cached total failure (written by older versions, or a
                # transient NAS error) must not be sticky — rehash it.
                entry = None
            if (
                entry
                and entry.get("size") == stat.st_size
                and entry.get("mtime") == int(stat.st_mtime)
            ):
                crc = entry.get("crc32", "")
                platform = entry.get("platform", "") or "Unknown"
            else:
                crc, platform = self._hash_file(f)
                entry = {
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                    "crc32": crc,
                    "platform": platform,
                }
                if crc or platform != "Unknown":
                    cache[key] = entry
                    dirty = True
                else:
                    # Total failure (unreadable archive, transient NAS
                    # error, …) — don't make it sticky; retry next scan.
                    cache.pop(key, None)

            # Embedded identity (survives fan patching). Cached alongside
            # the hash; pre-existing cache entries upgrade in place
            # without re-hashing.
            if "rom_id" in entry:
                rom_id = entry.get("rom_id", "")
                rom_title = entry.get("rom_title", "")
            else:
                rom_id, rom_title = parse_embedded_identity(f)
                entry["rom_id"] = rom_id
                entry["rom_title"] = rom_title
                dirty = True

            # DAT verification; on a miss, try a convergent header repair
            # (iNES 1.0 / NES 2.0 header variance) — bare files and zips.
            repaired = False
            game = dat.lookup(crc)
            if (
                game is not None
                and game.platform
                and platform not in ("", "Unknown")
                and game.platform != platform
            ):
                # A raw 32-bit CRC hit on another platform's entry is a
                # collision, not an identification.
                game = None
            if game is None and crc and dat.nes_headers:
                ext = f.suffix.lower()
                if ext == ".nes":
                    fixed = _repair_nes_header(f, dat)
                elif ext == ".zip":
                    fixed = _repair_nes_in_zip(f, dat)
                else:
                    fixed = None
                if fixed is not None:
                    crc, game = fixed
                    repaired = True
                    try:
                        stat = f.stat()
                    except OSError:
                        pass
                    entry.update({
                        "size": stat.st_size,
                        "mtime": int(stat.st_mtime),
                        "crc32": crc,
                    })
                    dirty = True

            # User-curated identity for ROMs no DAT will ever match
            # (translations, hacks, unlicensed originals).
            custom_entry = custom.get(crc) if crc else None

            roms.append(RomFile(
                path=f,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
                platform=platform,
                crc32=crc,
                dat_name=game.name if game else "",
                repaired=repaired,
                rom_id=rom_id,
                rom_title=rom_title,
                custom_name=custom_entry.name if custom_entry else "",
                derived_from=custom_entry.base if custom_entry else "",
            ))

        if dirty:
            self._save_cache(cache)
        logger.info("ROM scan found {} files in {} dirs", len(roms), len(self.rom_dirs))
        return roms

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @property
    def _passwords(self) -> list[str]:
        return list(getattr(self._cfg, "archive_passwords", []) or [])

    def _list_archive_members(self, f: Path) -> list[tuple[str, int, int]] | None:
        """List ``(name, size, crc32)`` per archive member, or ``None``.

        The backend is chosen by magic bytes — extensions lie (a scene
        NDS set shipped RAR archives named ``.7z``).  No archive is ever
        decompressed: zip central directories, 7z headers and RAR
        headers all store each member's CRC32 — the value No-Intro DATs
        record.  Header-encrypted archives are retried with each
        configured ``archive_passwords`` entry (listing only — nothing
        is extracted).
        """
        try:
            with open(f, "rb") as fh:
                magic = fh.read(8)
        except OSError as e:
            logger.warning("Cannot read {}: {}", f, e)
            return None

        try:
            if magic.startswith(_ZIP_MAGIC):
                with zipfile.ZipFile(f, "r") as zf:
                    return [
                        (m.filename, m.file_size, m.CRC)
                        for m in zf.infolist() if not m.is_dir()
                    ]
            if magic.startswith(_7Z_MAGIC):
                return self._list_7z(f)
            if magic.startswith(_RAR_MAGIC):
                return self._list_rar(f)
        except ImportError as e:
            logger.warning("Archive backend unavailable for {}: {}", f.name, e)
            return None
        except Exception as e:  # noqa: BLE001 - backend-specific errors
            logger.warning("Unreadable archive {}: {}", f, e)
            return None

        logger.warning("Unknown archive format: {}", f)
        return None

    def _list_7z(self, f: Path) -> list[tuple[str, int, int]] | None:
        import py7zr

        def _listing(password: str | None) -> list[tuple[str, int, int]]:
            with py7zr.SevenZipFile(f, "r", password=password) as zf:
                return [
                    (i.filename, i.uncompressed, i.crc32 or 0)
                    for i in zf.list() if not i.is_directory
                ]

        try:
            return _listing(None)
        except py7zr.exceptions.PasswordRequired:
            for pwd in self._passwords:
                try:
                    return _listing(pwd)
                except Exception:  # noqa: BLE001 - wrong password
                    continue
            logger.warning("Encrypted archive, no configured password fits: {}", f)
            return None

    def _list_rar(self, f: Path) -> list[tuple[str, int, int]] | None:
        import rarfile

        def _listing(password: str | None) -> list[tuple[str, int, int]]:
            with rarfile.RarFile(str(f)) as rf:
                if password is not None:
                    rf.setpassword(password)
                return [
                    (i.filename, i.file_size or 0, i.CRC or 0)
                    for i in rf.infolist() if not i.is_dir()
                ]

        members = _listing(None)
        if members:
            return members
        # An empty listing on a well-formed RAR means encrypted headers.
        with rarfile.RarFile(str(f)) as rf:
            if not rf.needs_password():
                return members  # legitimately empty archive
        for pwd in self._passwords:
            try:
                members = _listing(pwd)
                if members:
                    return members
            except Exception:  # noqa: BLE001 - wrong password
                continue
        logger.warning("Encrypted archive, no configured password fits: {}", f)
        return None

    def _hash_file(self, f: Path) -> tuple[str, str]:
        """Return ``(crc32_hex, platform)`` for a ROM file.

        Archives are identified through their stored member CRCs (see
        :meth:`_list_archive_members`); the largest ROM-extension member
        represents the archive.  Bare files are hashed by streaming.
        """
        ext = f.suffix.lower()
        if ext in ARCHIVE_EXTENSIONS:
            members = self._list_archive_members(f)
            if not members:
                return "", "Unknown"
            rom_members = [
                m for m in members
                if Path(m[0]).suffix.lower() in ROM_EXTENSIONS
            ]
            pool = rom_members or members
            name, _size, crc = max(pool, key=lambda m: m[1])
            platform = ROM_EXTENSIONS.get(Path(name).suffix.lower(), "Unknown")
            return (f"{crc & 0xFFFFFFFF:08X}" if crc else ""), platform

        platform = ROM_EXTENSIONS.get(ext, "Unknown")
        crc = 0
        try:
            with open(f, "rb") as fh:
                while chunk := fh.read(_HASH_CHUNK):
                    crc = zlib.crc32(chunk, crc)
        except OSError as e:
            logger.warning("Failed to hash {}: {}", f, e)
            return "", platform
        return f"{crc & 0xFFFFFFFF:08X}", platform

    def _load_cache(self) -> dict[str, dict]:
        path = self._cache_path
        if not path.is_file():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != CACHE_VERSION:
                return {}
            entries = data.get("entries", {})
            if isinstance(entries, dict):
                return entries
        except (OSError, ValueError) as e:
            logger.warning("Could not read ROM hash cache {}: {}", path, e)
        return {}

    def _save_cache(self, entries: dict[str, dict]) -> None:
        path = self._cache_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": CACHE_VERSION, "entries": entries}, f,
                          ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError as e:
            logger.warning("Could not write ROM hash cache {}: {}", path, e)


# ----------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------

def build_report(
    roms: list[RomFile],
    saves: list[GameSave],
    dat_games: int = 0,
) -> RomLibraryReport:
    """Cross-reference a ROM scan with the current save scan.

    Duplicates are grouped by content CRC32.  Save linking matches the
    ROM filename stem against ``game_id`` (case-insensitive) for the
    filename-keyed emulators only.
    """
    report = RomLibraryReport(roms=list(roms), dat_games=dat_games)

    by_crc: dict[str, list[RomFile]] = {}
    for rom in roms:
        if rom.crc32:
            by_crc.setdefault(rom.crc32, []).append(rom)
    report.duplicate_groups = [
        group for group in by_crc.values() if len(group) > 1
    ]

    # Derived-version detection: a CRC-missing ROM whose embedded
    # identity matches a DAT-verified sibling is a translation / hack
    # of that base game (headers survive fan patching).
    base_by_id: dict[tuple[str, str], str] = {}
    base_by_title: dict[tuple[str, str], str] = {}
    for rom in roms:
        if not rom.dat_name:
            continue
        if rom.rom_id:
            base_by_id.setdefault((rom.platform, rom.rom_id), rom.dat_name)
        if rom.rom_title:
            base_by_title.setdefault(
                (rom.platform, rom.rom_title), rom.dat_name)
    for rom in roms:
        if rom.dat_name or rom.derived_from:
            continue  # verified, or the custom DB already named the base
        base = ""
        if rom.rom_id:
            base = base_by_id.get((rom.platform, rom.rom_id), "")
        if not base and rom.rom_title:
            base = base_by_title.get((rom.platform, rom.rom_title), "")
        rom.derived_from = base

    by_stem: dict[str, list[RomFile]] = {}
    for rom in roms:
        by_stem.setdefault(rom.stem.lower(), []).append(rom)

    linked_keys: set[str] = set()
    for save in saves:
        if save.emulator not in FILENAME_KEYED_EMULATORS:
            continue
        for rom in by_stem.get(save.game_id.lower(), []):
            report.matched[rom.path] = save
            linked_keys.add(save.unique_key)

    report.saves_without_roms = [
        s for s in saves
        if s.emulator in FILENAME_KEYED_EMULATORS
        and s.unique_key not in linked_keys
    ]
    return report
