"""Save-state thumbnail extraction and backup embedding helpers."""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from loguru import logger

THUMBNAIL_DIR = "thumbnails"

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
_PCSX2_STATE_SUFFIXES = (".p2s", ".p2s.backup")


def extract_state_thumbnail(save_path: Path) -> bytes | None:
    """Return screenshot bytes from a save state, if this format exposes one."""
    lower_name = save_path.name.lower()
    if lower_name.endswith(_PCSX2_STATE_SUFFIXES):
        try:
            with zipfile.ZipFile(save_path, "r") as zf:
                data = _extract_image_from_zip(zf)
                if data:
                    return data
        except (zipfile.BadZipFile, OSError) as e:
            logger.debug("No embedded thumbnail in {}: {}", save_path, e)

    return _read_sibling_thumbnail(save_path)


def extract_state_thumbnail_from_bytes(name: str, data: bytes) -> bytes | None:
    """Return screenshot bytes from an archived save-state payload."""
    if not name.lower().endswith(_PCSX2_STATE_SUFFIXES):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            return _extract_image_from_zip(zf)
    except (zipfile.BadZipFile, OSError) as e:
        logger.debug("No embedded thumbnail in archived state {}: {}", name, e)
        return None


def add_backup_thumbnail(
    zf: zipfile.ZipFile, source_path: Path, entry_index: int
) -> str:
    """Write a save-state screenshot into a backup zip and return its arcname."""
    data = extract_state_thumbnail(source_path)
    if not data:
        return ""

    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_path.stem).strip("._")
    safe_stem = safe_stem or "state"
    ext = _image_ext_from_bytes(data)
    arc_name = f"{THUMBNAIL_DIR}/{entry_index:03d}_{safe_stem}{ext}"
    try:
        zf.writestr(arc_name, data)
    except OSError as e:
        logger.debug("Cannot embed thumbnail for {}: {}", source_path, e)
        return ""
    return arc_name


def read_backup_thumbnail(backup_path: Path) -> bytes | None:
    """Read the newest display thumbnail stored in or derivable from a backup."""
    if not backup_path.exists():
        return None

    backup_paths = _read_backup_paths(backup_path.with_suffix(".json"))

    try:
        with zipfile.ZipFile(backup_path, "r") as zf:
            names = {zi.filename for zi in zf.infolist() if not zi.is_dir()}

            for bp in _thumbnail_order(backup_paths):
                thumb_path = bp.get("thumbnail_zip_path", "")
                if thumb_path in names:
                    try:
                        return zf.read(thumb_path)
                    except KeyError:
                        pass

            # New backups should use explicit thumbnail entries.  This keeps
            # old backups useful by decoding screenshots from archived .p2s.
            for bp in _thumbnail_order(backup_paths):
                zip_path = bp.get("zip_path", "")
                if (
                    bp.get("type") == "savestate"
                    and not bp.get("is_dir", False)
                    and zip_path in names
                ):
                    data = extract_state_thumbnail_from_bytes(zip_path, zf.read(zip_path))
                    if data:
                        return data

            for zi in zf.infolist():
                if zi.is_dir():
                    continue
                if zi.filename.startswith(f"{THUMBNAIL_DIR}/") and _is_image_name(
                    zi.filename
                ):
                    return zf.read(zi.filename)
                if zi.filename not in names:
                    continue
                data = extract_state_thumbnail_from_bytes(zi.filename, zf.read(zi))
                if data:
                    return data
    except (zipfile.BadZipFile, OSError) as e:
        logger.debug("Cannot read backup thumbnail {}: {}", backup_path, e)
    return None


def _read_backup_paths(meta_path: Path) -> list[dict]:
    if not meta_path.exists():
        return []
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        paths = data.get("backup_paths", [])
        return paths if isinstance(paths, list) else []
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Cannot read backup metadata {}: {}", meta_path, e)
        return []


def _thumbnail_order(backup_paths: list[dict]) -> list[dict]:
    """Newest save-state entries first; legacy entries keep metadata order."""
    with_time: list[tuple[int, dict, float]] = []
    without_time: list[tuple[int, dict]] = []
    for index, bp in enumerate(backup_paths):
        ts = _modified_timestamp(bp)
        if ts is None:
            without_time.append((index, bp))
        else:
            with_time.append((index, bp, ts))

    with_time.sort(key=lambda item: (item[2], -item[0]), reverse=True)
    return [bp for _index, bp, _ts in with_time] + [bp for _index, bp in without_time]


def _modified_timestamp(bp: dict) -> float | None:
    value = bp.get("modified_time")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    return None


def _extract_image_from_zip(zf: zipfile.ZipFile) -> bytes | None:
    names = [name for name in zf.namelist() if _is_image_name(name)]
    names.sort(key=_image_priority)
    for name in names:
        try:
            return zf.read(name)
        except KeyError:
            continue
    return None


def _image_priority(name: str) -> tuple[int, str]:
    base = Path(name).name.lower()
    is_screenshot = base == "screenshot.png" or base.startswith("screenshot.")
    return (0 if is_screenshot else 1, name.lower())


def _read_sibling_thumbnail(save_path: Path) -> bytes | None:
    for ext in _IMAGE_EXTS:
        sibling = save_path.with_name(save_path.name + ext)
        if sibling.is_file():
            try:
                return sibling.read_bytes()
            except OSError as e:
                logger.debug("Cannot read thumbnail {}: {}", sibling, e)
    return None


def _is_image_name(name: str) -> bool:
    return name.lower().endswith(_IMAGE_EXTS)


def _image_ext_from_bytes(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"BM"):
        return ".bmp"
    return ".png"
