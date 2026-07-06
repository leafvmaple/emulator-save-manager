"""Embedded cartridge-identity parsers (GB / GBA / NDS)."""

from __future__ import annotations

import zipfile

from app.core.rom_headers import parse_embedded_identity

_GB_LOGO_PREFIX = bytes.fromhex("CEED6666CC0D000B")


def gba_rom(code: bytes = b"A3UJ", title: bytes = b"MOTHER3",
            body: bytes = b"GBA-BODY") -> bytes:
    head = bytearray(0xC0)
    head[0xA0:0xA0 + len(title)] = title
    head[0xAC:0xB0] = code
    head[0xB2] = 0x96
    return bytes(head) + body


def nds_rom(code: bytes = b"IPKJ", title: bytes = b"POKEMON HG",
            body: bytes = b"NDS-BODY") -> bytes:
    head = bytearray(0x200)
    head[0x00:0x00 + len(title)] = title
    head[0x0C:0x10] = code
    return bytes(head) + body


def gb_rom(title: bytes = b"POKEMON GOLD", cgb_flag: int = 0,
           body: bytes = b"GB-BODY") -> bytes:
    head = bytearray(0x150)
    head[0x104:0x104 + len(_GB_LOGO_PREFIX)] = _GB_LOGO_PREFIX
    head[0x134:0x134 + len(title)] = title
    if cgb_flag:
        head[0x143] = cgb_flag
    return bytes(head) + body


def test_gba_identity(tmp_path):
    p = tmp_path / "mother3.gba"
    p.write_bytes(gba_rom())
    assert parse_embedded_identity(p) == ("A3UJ", "MOTHER3")


def test_gba_without_fixed_byte_rejected(tmp_path):
    data = bytearray(gba_rom())
    data[0xB2] = 0x00
    p = tmp_path / "broken.gba"
    p.write_bytes(bytes(data))
    assert parse_embedded_identity(p) == ("", "")


def test_nds_identity(tmp_path):
    p = tmp_path / "hg.nds"
    p.write_bytes(nds_rom())
    assert parse_embedded_identity(p) == ("IPKJ", "POKEMON HG")


def test_gb_identity_and_cgb_flag(tmp_path):
    p = tmp_path / "gold.gb"
    p.write_bytes(gb_rom())
    assert parse_embedded_identity(p) == ("", "POKEMON GOLD")

    # CGB flag steals the title field's last byte — must not leak into it.
    p2 = tmp_path / "crystal.gbc"
    p2.write_bytes(gb_rom(title=b"PM_CRYSTAL", cgb_flag=0xC0))
    assert parse_embedded_identity(p2) == ("", "PM_CRYSTAL")


def test_gb_without_logo_rejected(tmp_path):
    p = tmp_path / "junk.gb"
    p.write_bytes(b"\x00" * 0x150)
    assert parse_embedded_identity(p) == ("", "")


def test_zip_member_identity(tmp_path):
    p = tmp_path / "hg.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("Pokemon HG (Japan).nds", nds_rom())
        zf.writestr("readme.txt", b"scene notes")
    assert parse_embedded_identity(p) == ("IPKJ", "POKEMON HG")


def test_unknown_extension_yields_nothing(tmp_path):
    p = tmp_path / "game.sfc"
    p.write_bytes(b"\x00" * 0x200)
    assert parse_embedded_identity(p) == ("", "")
