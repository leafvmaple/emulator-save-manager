"""Embedded ROM identity — cartridge-header parsers for GB / GBA / NDS.

Fan translations and ROM hacks patch graphics and text but almost never
touch the cartridge header, so the embedded game code / title survives
patching and still identifies the *base* game even when the file's CRC
no longer matches any official dump.  ``rom_library.build_report`` uses
this to label a CRC-missing ROM as "derived from X" whenever a
DAT-verified ROM in the same library shares its embedded identity.

All parsers are read-only, need at most the first 0x200 bytes, and
return ``("", "")`` for anything that doesn't validate — a wrong ID is
worse than no ID.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from loguru import logger

_HEAD_LEN = 0x200

#: First bytes of the Nintendo logo bitmap at 0x104 — constant in every
#: valid GB/GBC dump (the boot ROM verifies it), so it doubles as a
#: cheap validity check.
_GB_LOGO_PREFIX = bytes.fromhex("CEED6666CC0D000B")

_GBA_FIXED_BYTE = 0x96  # mandatory value at 0xB2, checked by the BIOS

#: Extensions with an embedded identity we know how to read.
_PARSEABLE = {".gb", ".gbc", ".gba", ".nds", ".dsi"}

#: Container extensions worth probing. Only zip supports the cheap
#: partial member read this module needs — 7z/RAR members can't be
#: peeked without decompressing (or an external tool), so those parse
#: as unknown identity and matching falls back to CRC.
_ARCHIVES = {".zip", ".7z", ".rar"}


def _ascii(data: bytes) -> str:
    """Decode printable ASCII up to the first NUL ('' if anything odd)."""
    data = data.split(b"\x00", 1)[0]
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        return ""
    if any(not c.isprintable() for c in text):
        return ""
    return text.strip()


def _is_game_code(text: str) -> bool:
    return len(text) == 4 and all(c.isascii() and c.isalnum() for c in text)


def _parse_gba(head: bytes) -> tuple[str, str]:
    """GBA: title at 0xA0 (12), game code at 0xAC (4), 0x96 at 0xB2."""
    if len(head) < 0xB3 or head[0xB2] != _GBA_FIXED_BYTE:
        return "", ""
    code = _ascii(head[0xAC:0xB0])
    if not _is_game_code(code):
        return "", ""
    return code, _ascii(head[0xA0:0xAC])


def _parse_nds(head: bytes) -> tuple[str, str]:
    """NDS: title at 0x00 (12), game code at 0x0C (4)."""
    if len(head) < 0x10:
        return "", ""
    code = _ascii(head[0x0C:0x10])
    if not _is_game_code(code):
        return "", ""
    return code, _ascii(head[0x00:0x0C])


def _parse_gb(head: bytes) -> tuple[str, str]:
    """GB/GBC: title at 0x134 (16, or 15 when the CGB flag is set).

    GB carts carry no game code, so identity is the title string alone;
    both an official dump and a translation patched from it parse to the
    same value, which is all the derived-matching needs.
    """
    if len(head) < 0x150 or head[0x104:0x10C] != _GB_LOGO_PREFIX:
        return "", ""
    raw = head[0x134:0x144]
    if raw[-1] in (0x80, 0xC0):  # CGB flag shares the title field's last byte
        raw = raw[:15]
    return "", _ascii(raw)


_PARSERS = {
    ".gb": _parse_gb,
    ".gbc": _parse_gb,
    ".gba": _parse_gba,
    ".nds": _parse_nds,
    ".dsi": _parse_nds,
}


def _read_head(path: Path) -> tuple[str, bytes] | None:
    """Return ``(extension, first bytes)`` of *path* or its zip member."""
    ext = path.suffix.lower()
    try:
        if ext in _ARCHIVES:
            with zipfile.ZipFile(path, "r") as zf:
                members = [
                    m for m in zf.infolist()
                    if not m.is_dir()
                    and Path(m.filename).suffix.lower() in _PARSEABLE
                ]
                if not members:
                    return None
                best = max(members, key=lambda m: m.file_size)
                with zf.open(best.filename) as fh:
                    return Path(best.filename).suffix.lower(), fh.read(_HEAD_LEN)
        if ext in _PARSEABLE:
            with open(path, "rb") as fh:
                return ext, fh.read(_HEAD_LEN)
    except (OSError, zipfile.BadZipFile) as e:
        logger.debug("Cannot read header of {}: {}", path, e)
    return None


def parse_embedded_identity(path: Path) -> tuple[str, str]:
    """Return ``(rom_id, rom_title)`` embedded in *path* ('' when unknown).

    ``rom_id`` is the 4-char game code (GBA/NDS); GB carts only yield a
    title.  Zip archives are parsed through their largest parseable
    member without extraction.
    """
    head = _read_head(path)
    if head is None:
        return "", ""
    ext, data = head
    return _PARSERS[ext](data)
