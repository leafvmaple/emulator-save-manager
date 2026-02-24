"""PCSX2 GameIndex.yaml parser — resolve game serials to display names.

Reads PCSX2's ``GameIndex.yaml`` (shipped with every PCSX2 build) using a fast
line-by-line scanner, extracts ``serial → {name, name_en, region}`` and caches
the result as a compact JSON file for fast subsequent loads.

Lookup priority:
  - First check local PCSX2 installation for ``resources/GameIndex.yaml``
  - Fallback: download from GitHub
  - Always cache as JSON in the app data directory
"""

from __future__ import annotations

import json
import platform as _platform
import urllib.request
from pathlib import Path

from loguru import logger

GAMEINDEX_URL = (
    "https://raw.githubusercontent.com/PCSX2/pcsx2/master"
    "/bin/resources/GameIndex.yaml"
)
CACHE_FILENAME = "pcsx2_game_names.json"


class GameDB:
    """Lookup table for PS2 game names derived from PCSX2 GameIndex.yaml."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._cache_file = cache_dir / CACHE_FILENAME
        # serial (lower-case) → {"name": ..., "name_en": ..., "region": ...}
        self._db: dict[str, dict[str, str]] = {}
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded and bool(self._db)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, pcsx2_paths: list[Path] | None = None) -> bool:
        """Load the game database (cache → local YAML → download)."""
        if self._load_from_cache():
            return True

        yaml_path = self._find_gameindex_yaml(pcsx2_paths or [])
        if yaml_path:
            logger.info("Found GameIndex.yaml at {}", yaml_path)
            self._parse_gameindex(yaml_path)
            if self.is_loaded:
                self._save_cache()
            return self.is_loaded

        logger.info("GameIndex.yaml not found locally, attempting download…")
        if self._download_and_parse():
            self._save_cache()
            return self.is_loaded

        logger.warning("Could not load PCSX2 game database")
        return False

    def get_name(self, serial: str, lang: str = "en_US") -> str | None:
        """Return the best display name for *serial* in the given language.

        - ``ja_JP``: prefer native ``name``, fallback ``name_en``
        - other locales: prefer ``name_en``, fallback ``name``
        """
        entry = self._db.get(serial.lower())
        if entry is None:
            return None
        if lang == "ja_JP":
            return entry.get("name") or entry.get("name_en")
        return entry.get("name_en") or entry.get("name")

    def get_info(self, serial: str) -> dict[str, str] | None:
        """Return all stored info for *serial*, or ``None``."""
        return self._db.get(serial.lower())

    # ------------------------------------------------------------------
    # Locate GameIndex.yaml
    # ------------------------------------------------------------------

    @staticmethod
    def _find_gameindex_yaml(pcsx2_paths: list[Path]) -> Path | None:
        candidates: list[Path] = []
        for p in pcsx2_paths:
            candidates.append(p / "resources" / "GameIndex.yaml")
            candidates.append(p / "GameIndex.yaml")
            if p.parent != p:
                candidates.append(p.parent / "resources" / "GameIndex.yaml")

        if _platform.system() == "Windows":
            for prog in [
                Path("C:/Program Files/PCSX2"),
                Path("C:/Program Files (x86)/PCSX2"),
            ]:
                candidates.append(prog / "resources" / "GameIndex.yaml")
                if prog.exists():
                    for child in prog.iterdir():
                        if child.is_dir():
                            candidates.append(
                                child / "resources" / "GameIndex.yaml"
                            )

        for c in candidates:
            if c.exists():
                return c
        return None

    # ------------------------------------------------------------------
    # Fast line-by-line YAML scanner
    # ------------------------------------------------------------------

    def _parse_gameindex(self, yaml_path: Path) -> None:
        """Parse only serial/name/name-en/region from GameIndex.yaml.

        Uses a fast line-scanner instead of a full YAML parser to handle the
        potentially large file (~15-25 MB) efficiently.
        """
        self._db.clear()
        current_serial: str | None = None
        current_entry: dict[str, str] = {}

        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.rstrip("\n\r")
                    if not stripped or stripped.lstrip().startswith("#"):
                        continue

                    # Top-level key (serial) — no leading whitespace
                    if stripped[0] not in (" ", "\t") and ":" in stripped:
                        if current_serial and current_entry:
                            self._db[current_serial] = current_entry
                        key_part = stripped.split(":", 1)[0].strip().strip("\"'")
                        current_serial = key_part.lower()
                        current_entry = {}
                        continue

                    # Indented fields under current serial
                    if current_serial and stripped[0] in (" ", "\t"):
                        trimmed = stripped.strip()
                        if trimmed.startswith("name-en:"):
                            val = trimmed.split(":", 1)[1].strip().strip("\"'")
                            current_entry["name_en"] = val
                        elif trimmed.startswith("name-sort:"):
                            pass
                        elif trimmed.startswith("name:"):
                            val = trimmed.split(":", 1)[1].strip().strip("\"'")
                            current_entry["name"] = val
                        elif trimmed.startswith("region:"):
                            val = trimmed.split(":", 1)[1].strip().strip("\"'")
                            current_entry["region"] = val

            if current_serial and current_entry:
                self._db[current_serial] = current_entry

            self._loaded = True
            logger.info("Loaded {} game entries from GameIndex.yaml", len(self._db))
        except Exception as e:
            logger.error("Failed to parse GameIndex.yaml: {}", e)

    # ------------------------------------------------------------------
    # Download fallback
    # ------------------------------------------------------------------

    def _download_and_parse(self) -> bool:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = self._cache_dir / "GameIndex.yaml.tmp"
            logger.info("Downloading GameIndex.yaml from {}…", GAMEINDEX_URL)
            urllib.request.urlretrieve(GAMEINDEX_URL, str(tmp_path))
            self._parse_gameindex(tmp_path)
            tmp_path.unlink(missing_ok=True)
            return self.is_loaded
        except Exception as e:
            logger.error("Failed to download GameIndex.yaml: {}", e)
            return False

    # ------------------------------------------------------------------
    # JSON cache
    # ------------------------------------------------------------------

    def _load_from_cache(self) -> bool:
        if not self._cache_file.exists():
            return False
        try:
            with open(self._cache_file, "r", encoding="utf-8") as f:
                self._db = json.load(f)
            self._loaded = True
            logger.info("Loaded {} game entries from cache", len(self._db))
            return True
        except Exception as e:
            logger.warning("Failed to load game DB cache: {}", e)
            return False

    def _save_cache(self) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(self._db, f, ensure_ascii=False, separators=(",", ":"))
            logger.info("Saved game DB cache ({} entries)", len(self._db))
        except Exception as e:
            logger.warning("Failed to save game DB cache: {}", e)
