"""ROM index models used to enrich save identities."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class RomFileType(str, Enum):
    """ROM file classification."""

    BASE = "base"
    UPDATE = "update"
    DLC = "dlc"
    UNKNOWN = "unknown"


class RomContentType(str, Enum):
    """ROM container format."""

    RAW = "raw"
    ZIP = "zip"
    XCI = "xci"
    NSP = "nsp"
    NSZ = "nsz"
    XCZ = "xcz"
    NRO = "nro"


@dataclass
class RomInfo:
    """ROM metadata extracted from headers, DAT files or scrapers."""

    title_id: str = ""
    title_name: str = ""
    title_name_zh: str = ""
    title_name_en: str = ""
    title_name_ja: str = ""
    publisher: str = ""
    version: str = ""
    version_raw: int = 0
    file_type: str = RomFileType.UNKNOWN.value
    content_type: str = RomContentType.RAW.value
    region: str = ""
    languages: str = ""
    min_system_version: str = ""
    build_id: str = ""
    signature_valid: bool | None = None
    icon_path: str = ""
    dat_crc32: list[str] = field(default_factory=list)
    dat_id: int = -1


@dataclass
class RomEntry:
    """One indexed ROM file."""

    rom_path: str
    platform: str
    game_id: str
    emulator: str = ""
    file_size: int = 0
    hash_crc32: str = ""
    hash_sha1: str = ""
    added_at: str = ""
    rom_info: RomInfo | None = None
    scrape_status: str = "none"

    @property
    def path(self) -> Path:
        return Path(self.rom_path)

    @property
    def file_stem(self) -> str:
        return self.path.stem

    @property
    def display_name(self) -> str:
        """Best available human-readable title for the current UI language."""
        if self.rom_info is None:
            return self.game_id or self.file_stem

        try:
            from app.i18n import get_current_language

            lang = get_current_language()
        except Exception:
            lang = "zh_CN"

        if lang == "zh_CN":
            order = (
                self.rom_info.title_name_zh,
                self.rom_info.title_name_en,
                self.rom_info.title_name_ja,
                self.rom_info.title_name,
            )
        elif lang == "ja_JP":
            order = (
                self.rom_info.title_name_ja,
                self.rom_info.title_name_en,
                self.rom_info.title_name_zh,
                self.rom_info.title_name,
            )
        else:
            order = (
                self.rom_info.title_name_en,
                self.rom_info.title_name_zh,
                self.rom_info.title_name_ja,
                self.rom_info.title_name,
            )

        for name in order:
            if name:
                return name
        return self.game_id or self.file_stem
