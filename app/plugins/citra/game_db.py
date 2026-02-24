"""3DS title database — resolve title IDs to human-readable game names.

Downloads per-region JSON files from the community 3dsdb project
(https://github.com/hax0kartik/3dsdb) and caches the result locally
as a compact JSON lookup table.

Lookup priority:
  1. Local JSON cache
  2. Download from GitHub (all regions)
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from loguru import logger

# Per-region JSON endpoints from 3dsdb
_REGION_URLS: dict[str, str] = {
    "US": "https://raw.githubusercontent.com/hax0kartik/3dsdb/master/jsons/list_US.json",
    "GB": "https://raw.githubusercontent.com/hax0kartik/3dsdb/master/jsons/list_GB.json",
    "JP": "https://raw.githubusercontent.com/hax0kartik/3dsdb/master/jsons/list_JP.json",
    "TW": "https://raw.githubusercontent.com/hax0kartik/3dsdb/master/jsons/list_TW.json",
    "KR": "https://raw.githubusercontent.com/hax0kartik/3dsdb/master/jsons/list_KR.json",
}

# Map app language codes → preferred region order for name lookup
_LANG_REGION_ORDER: dict[str, list[str]] = {
    "en_US": ["US", "GB", "JP", "TW", "KR"],
    "zh_CN": ["TW", "US", "JP", "GB", "KR"],
    "ja_JP": ["JP", "US", "GB", "TW", "KR"],
}
_DEFAULT_REGION_ORDER = ["US", "GB", "JP", "TW", "KR"]

CACHE_FILENAME = "citra_game_names.json"

# GameTDB cover-art URL template for 3DS.
# 3dsdb product codes end with a generic suffix (usually "A" for all regions),
# but GameTDB uses region-specific codes.  We replace the last character with
# a region suffix (E/P/J/K) and match the URL folder accordingly.
_GAMETDB_COVER_URL = (
    "https://art.gametdb.com/3ds/cover/{region}/{code}.jpg"
)

# (code_suffix, gametdb_region_folder) — tried in order
_REGION_VARIANTS: list[tuple[str, str]] = [
    ("E", "US"),   # NTSC-U
    ("P", "EN"),   # PAL
    ("J", "JA"),   # NTSC-J
    ("K", "KO"),   # NTSC-K
]


class GameDB:
    """Lookup table for 3DS game names derived from 3dsdb."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._cache_file = cache_dir / CACHE_FILENAME
        # title_id (upper-case, 16 hex chars) → {"name": ..., "region": ..., "product_code": ...}
        # When multiple regions have the same title, we store each region's
        # name so we can pick the best one at lookup time.
        # Format: title_id → {"names": {region: name}, "product_code": ...}
        self._db: dict[str, dict] = {}
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded and bool(self._db)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> bool:
        """Load the game database (cache → download)."""
        if self._load_from_cache():
            return True

        logger.info("3DS game DB cache not found, downloading from 3dsdb…")
        if self._download_and_build():
            self._save_cache()
            return self.is_loaded

        logger.warning("Could not load 3DS game database")
        return False

    def get_name(self, title_id: str, lang: str = "en_US") -> str | None:
        """Return the best display name for *title_id* in the given language.

        Picks the name from the region most appropriate for the locale.
        For non-English regions, 3dsdb often stores names as
        ``"EnglishName(本地名)"`` — we extract the local part when a
        non-English locale is requested.
        """
        entry = self._db.get(title_id.upper())
        if entry is None:
            return None

        names: dict[str, str] = entry.get("names", {})
        if not names:
            return None

        region_order = _LANG_REGION_ORDER.get(lang, _DEFAULT_REGION_ORDER)
        raw: str | None = None
        for region in region_order:
            if region in names:
                raw = names[region]
                break
        if raw is None:
            raw = next(iter(names.values()))

        return self._extract_local_name(raw, lang)

    @staticmethod
    def _extract_local_name(raw: str, lang: str) -> str:
        """Extract the localised portion from a 3dsdb name string.

        3dsdb non-English entries come in several formats::

            Simple:   "Pokemon Moon(ポケットモンスター ムーン)"
            Complex:  "Detective Pikachu (Chunichi version)(名探偵ピカチュウ (中日版))"
            English:  "Pokémon™ Crystal Version(English Version)"

        For ``en_US`` we return the English prefix (before the first
        top-level ``(``).
        For ``zh_CN`` / ``ja_JP`` we return the content of the *last*
        top-level paren group, stripping inner version tags like
        ``(日文版)``.
        """
        import re

        if lang == "en_US":
            # Find the first top-level '(' and take everything before it
            depth = 0
            for i, ch in enumerate(raw):
                if ch == "(":
                    if depth == 0:
                        prefix = raw[:i].strip()
                        if prefix:
                            return prefix
                    depth += 1
                elif ch == ")":
                    depth -= 1
            return raw.strip()

        # --- Non-English locale ---

        # Find all top-level parenthesized groups
        groups: list[tuple[int, int]] = []
        depth = 0
        start = -1
        for i, ch in enumerate(raw):
            if ch == "(":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and start >= 0:
                    groups.append((start, i))
                    start = -1

        if not groups:
            return raw.strip()

        _junk_re = re.compile(
            r"^(English|Japanese|Chinese|Chunichi|Korean)"
            r"\s*(Version|version|Edition|edition)?\s*$",
            re.IGNORECASE,
        )

        # Try groups from last to first, pick the first non-junk one
        for gs, ge in reversed(groups):
            inner = raw[gs + 1 : ge].strip()
            if _junk_re.match(inner):
                continue
            # Strip trailing nested version tags like " (日文版)"
            # Find the last top-level paren inside *inner*
            sub_groups: list[tuple[int, int]] = []
            d = 0
            s2 = -1
            for j, c in enumerate(inner):
                if c == "(":
                    if d == 0:
                        s2 = j
                    d += 1
                elif c == ")":
                    d -= 1
                    if d == 0 and s2 >= 0:
                        sub_groups.append((s2, j))
                        s2 = -1
            if sub_groups:
                last_s, last_e = sub_groups[-1]
                nested = inner[last_s + 1 : last_e].strip()
                if re.search(r"[版文]", nested) or _junk_re.match(nested):
                    cleaned = inner[:last_s].strip()
                    if cleaned:
                        return cleaned
            return inner

        # All groups were junk — fall back to prefix before first '('
        idx = raw.find("(")
        if idx > 0:
            return raw[:idx].strip()
        return raw.strip()

    def get_product_code(self, title_id: str) -> str | None:
        """Return the product code (e.g. CTR-N-ABEE) for a title."""
        entry = self._db.get(title_id.upper())
        if entry is None:
            return None
        return entry.get("product_code")

    def get_info(self, title_id: str) -> dict | None:
        """Return all stored info for *title_id*, or ``None``."""
        return self._db.get(title_id.upper())

    def get_cover_urls(self, title_id: str) -> list[str]:
        """Return a list of candidate GameTDB cover-art URLs for *title_id*.

        3dsdb product codes typically use a generic last character (``A``)
        while GameTDB requires region-specific codes (``E``/``P``/``J``/``K``).
        We return multiple candidate URLs so the caller can try them in
        order until one succeeds.
        """
        entry = self._db.get(title_id.upper())
        if entry is None:
            return []

        product_code: str = entry.get("product_code", "")
        if not product_code:
            return []

        # Product code formats: "CTR-P-AXCE", "KTR-P-BFGE", or just "AXCE"
        parts = product_code.split("-")
        raw_code = parts[-1].strip()
        if len(raw_code) < 3:
            return []

        # First 3 chars are game-specific; last char is region
        base = raw_code[:3]

        urls: list[str] = []
        for suffix, region in _REGION_VARIANTS:
            code = base + suffix
            urls.append(_GAMETDB_COVER_URL.format(region=region, code=code))
        return urls

    # ------------------------------------------------------------------
    # Download & parse
    # ------------------------------------------------------------------

    def _download_and_build(self) -> bool:
        """Download all region JSONs and build the lookup DB."""
        self._db.clear()
        any_success = False

        for region, url in _REGION_URLS.items():
            try:
                logger.info("Downloading 3DS title list for region {}…", region)
                req = urllib.request.Request(url, headers={"User-Agent": "EmulatorSaveManager/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self._ingest_region(data, region)
                any_success = True
            except Exception as e:
                logger.warning("Failed to download 3DS DB for region {}: {}", region, e)

        if any_success:
            self._loaded = True
            logger.info("Built 3DS game DB with {} title entries", len(self._db))

        return any_success

    def _ingest_region(self, titles: list[dict], region: str) -> None:
        """Merge a list of title entries from one region into the DB."""
        for entry in titles:
            title_id = entry.get("TitleID", "").upper().strip()
            name = entry.get("Name", "").strip()
            product_code = entry.get("Product Code", "").strip()

            if not title_id or not name:
                continue

            # Skip update titles (high = 0004000E) — they clutter the DB
            if title_id.startswith("0004000E"):
                continue

            if title_id not in self._db:
                self._db[title_id] = {"names": {}, "product_code": product_code}

            self._db[title_id]["names"][region] = name
            # Keep the product code from the first region that provides it
            if product_code and not self._db[title_id].get("product_code"):
                self._db[title_id]["product_code"] = product_code

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
            logger.info("Loaded {} 3DS game entries from cache", len(self._db))
            return True
        except Exception as e:
            logger.warning("Failed to load 3DS game DB cache: {}", e)
            return False

    def _save_cache(self) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(self._db, f, ensure_ascii=False, separators=(",", ":"))
            logger.info("Saved 3DS game DB cache ({} entries)", len(self._db))
        except Exception as e:
            logger.warning("Failed to save 3DS game DB cache: {}", e)
