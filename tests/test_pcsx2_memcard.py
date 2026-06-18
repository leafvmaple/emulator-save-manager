"""PCSX2 file-based memory card scanning.

Regression guard: the default PCSX2 setup uses single-file memory cards
(``Mcd001.ps2``).  These were previously skipped entirely — only folder-type
cards and save states were recognized.

A file card is one filesystem image, so it is modeled as a single whole-card
:class:`GameSave` (backup/restore is atomic over the whole file); the contained
game serials are surfaced in the display label.
"""

from __future__ import annotations

import struct
from pathlib import Path

from app.models.game_save import SaveType
from app.plugins.pcsx2.plugin import (
    _MEMCARD_FILE_EXTS,
    _build_card_name,
    _scan_memcard_file,
    _scan_memcards_dir,
    _scan_savestates,
)


def _make_ps2_card(
    tmp_path: Path,
    name: str = "Mcd001.ps2",
    game_dir: str | list[str] = "BISLPS-25733OGS",
) -> Path:
    """Build a minimal but structurally-valid 2-cluster PS2 memory card.

    Layout: cluster 0 = superblock, cluster 1 = root directory holding
    ``.``, ``..`` and one game-save directory entry.  The entry name lives at
    offset 0x40 (matching the real on-disk format).
    """
    page, cluster = 512, 1024

    sb = bytearray(cluster)
    sb[0:35] = b"Sony PS2 Memory Card Format 1.2.0.0"
    struct.pack_into("<H", sb, 0x28, page)      # page_size
    struct.pack_into("<H", sb, 0x2A, 2)         # pages_per_cluster
    struct.pack_into("<H", sb, 0x2C, 16)        # pages_per_block
    struct.pack_into("<I", sb, 0x30, 8192)      # clusters_per_card
    struct.pack_into("<I", sb, 0x34, 1)         # alloc_offset → root at byte 1024
    struct.pack_into("<I", sb, 0x38, 8192)      # alloc_end
    struct.pack_into("<I", sb, 0x3C, 0)         # rootdir_cluster

    def direntry(mode: int, name_: str = "") -> bytes:
        e = bytearray(512)
        struct.pack_into("<I", e, 0, mode)
        struct.pack_into("<I", e, 4, 1024)      # length
        struct.pack_into("<I", e, 0x10, 5)      # first cluster
        nm = name_.encode("ascii")
        e[0x40:0x40 + len(nm)] = nm             # name field at 0x40
        return bytes(e)

    game_dirs = [game_dir] if isinstance(game_dir, str) else game_dir
    dot = bytearray(direntry(0x8427, "."))
    struct.pack_into("<I", dot, 4, 2 + len(game_dirs))  # num_entries: ., .., games
    root = bytes(dot) + direntry(0x8427, "..")
    for dirname in game_dirs:
        root += direntry(0x8427, dirname)

    p = tmp_path / name
    p.write_bytes(bytes(sb) + root)
    return p


def test_scan_file_card_is_one_whole_card_save(tmp_path):
    saves = _scan_memcard_file(_make_ps2_card(tmp_path))
    assert len(saves) == 1                       # whole card = one unit
    (s,) = saves
    assert s.emulator == "PCSX2" and s.platform == "PS2"
    assert s.game_id == "SLPS-25733"             # groups with save states
    assert "SLPS-25733" in s.game_name           # contained game surfaced
    assert s.save_files[0].save_type == SaveType.MEMCARD
    assert s.save_files[0].path.name == "Mcd001.ps2"


def test_file_card_label_localizes_via_lookup(tmp_path):
    saves = _scan_memcard_file(
        _make_ps2_card(tmp_path),
        name_lookup=lambda serial: "三国志" if serial == "SLPS-25733" else None,
    )
    assert saves[0].game_name == "三国志 (Mcd001)"


def test_blank_card_yields_nothing(tmp_path):
    # An unformatted card is all 0xFF — no superblock, not a PS1 card.
    p = tmp_path / "Mcd002.ps2"
    p.write_bytes(b"\xff" * 8192)
    assert _scan_memcard_file(p) == []


def test_scan_memcards_dir_includes_file_cards(tmp_path):
    # The default-named card must now be picked up (was silently skipped).
    _make_ps2_card(tmp_path, name="Mcd001.ps2")
    saves = _scan_memcards_dir(tmp_path)
    assert saves, "file-based .ps2 memory cards must be scanned"


def test_scan_memcards_dir_dispatches_folder_and_file(tmp_path):
    # File card …
    _make_ps2_card(tmp_path, name="Mcd001.ps2")
    # … and a folder card side by side.
    folder = tmp_path / "Mcd-folder"
    folder.mkdir()
    (folder / "_pcsx2_superblock").write_bytes(b"x")
    (folder / "BASLUS-21005INGS").mkdir()
    (folder / "BASLUS-21005INGS" / "save.bin").write_bytes(b"DATA")

    saves = _scan_memcards_dir(tmp_path)
    ids = {s.game_id for s in saves}
    assert "SLPS-25733" in ids    # single-game file card groups by game
    assert "SLUS-21005" in ids    # per-game folder-card entry (separate dirs)
    file_card = next(s for s in saves if s.game_id == "SLPS-25733")
    assert "SLPS-25733" in file_card.game_name


def test_single_game_file_card_groups_with_savestates(tmp_path):
    card = _scan_memcard_file(_make_ps2_card(tmp_path))[0]
    states_dir = tmp_path / "sstates"
    states_dir.mkdir()
    (states_dir / "SLPS-25733 (083F0E03).00.p2s").write_bytes(b"STATE")

    state = _scan_savestates(states_dir)[0]

    assert card.game_id == state.game_id == "SLPS-25733"
    assert state.crc32 == "083F0E03"


def test_multi_game_file_card_keeps_card_identity(tmp_path):
    saves = _scan_memcard_file(_make_ps2_card(
        tmp_path,
        game_dir=["BISLPS-25733OGS", "BASLUS-21005INGS"],
    ))
    assert saves[0].game_id == "Mcd001"


def test_unknown_extension_ignored(tmp_path):
    (tmp_path / "readme.txt").write_bytes(b"not a card")
    assert _scan_memcards_dir(tmp_path) == []
    assert ".ps2" in _MEMCARD_FILE_EXTS


def test_build_card_name_formats():
    assert _build_card_name("Mcd001", []) == "Mcd001"
    assert _build_card_name("Mcd001", ["SLPS-25733"]) == "SLPS-25733 (Mcd001)"
    many = ["SLPS-1", "SLPS-2", "SLPS-3", "SLPS-4"]
    assert _build_card_name("Mcd001", many) == "SLPS-1, SLPS-2, SLPS-3 +1 (Mcd001)"
