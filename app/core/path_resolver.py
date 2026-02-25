"""Cross-platform path resolver with placeholder support.

On Windows the "Documents" folder can be relocated by the user (e.g. to
``D:\\Documents``).  ``Path.home() / "Documents"`` does **not** reflect
this — it always returns ``C:\\Users\\<user>\\Documents``.  We must use
the Windows Shell API to obtain the real location.

Placeholders
~~~~~~~~~~~~
When persisting paths (backup metadata, config JSON …) we replace known
OS-specific roots with placeholders so the data stays portable across
machines and across Documents-folder moves:

    ``${DOCUMENTS}``     → user's Documents folder
    ``${HOME}``          → user's home directory
    ``${APPDATA}``       → ``%APPDATA%``  (Roaming)
    ``${LOCALAPPDATA}``  → ``%LOCALAPPDATA%``

Usage::

    from app.path_resolver import resolve_path, to_portable_path, get_documents_dir

    real = get_documents_dir()              # Path("D:/Documents")
    portable = to_portable_path(some_abs)   # "${DOCUMENTS}/PCSX2/memcards/..."
    absolute = resolve_path(portable)       # Path("D:/Documents/PCSX2/memcards/...")
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

from loguru import logger

# ---------------------------------------------------------------------------
# Actual directory lookups (cached)
# ---------------------------------------------------------------------------

_cache: dict[str, Path] = {}


def _get_windows_known_folder(folder_id: str) -> Path | None:
    """Use the Windows Shell API to retrieve a known-folder path.

    *folder_id* is one of: ``"Documents"``, ``"RoamingAppData"``,
    ``"LocalAppData"``, ``"Personal"``.
    """
    try:
        import ctypes
        import ctypes.wintypes

        # SHGetFolderPath CSIDL constants
        _CSIDL = {
            "Documents": 0x0005,       # CSIDL_PERSONAL / My Documents
            "RoamingAppData": 0x001A,  # CSIDL_APPDATA
            "LocalAppData": 0x001C,    # CSIDL_LOCAL_APPDATA
        }

        csidl = _CSIDL.get(folder_id)
        if csidl is None:
            return None

        buf = ctypes.create_unicode_buffer(1024)
        # SHGetFolderPathW(hwnd, nFolder, hToken, dwFlags, pszPath)
        result = ctypes.windll.shell32.SHGetFolderPathW(  # type: ignore[union-attr]
            0, csidl, 0, 0, buf
        )
        if result == 0:  # S_OK
            return Path(buf.value)
    except Exception as e:
        logger.debug("SHGetFolderPathW failed for {}: {}", folder_id, e)
    return None


def get_documents_dir() -> Path:
    """Return the real user Documents directory.

    On Windows this queries the Shell API so it respects any relocation.
    On other platforms it falls back to ``~/Documents``.
    """
    if "documents" in _cache:
        return _cache["documents"]

    result: Path | None = None
    if platform.system() == "Windows":
        result = _get_windows_known_folder("Documents")

    if result is None or not result.exists():
        result = Path.home() / "Documents"

    _cache["documents"] = result
    logger.debug("Documents directory resolved to: {}", result)
    return result


def get_appdata_dir() -> Path:
    """Return ``%APPDATA%`` (Roaming) on Windows, ``~`` elsewhere."""
    if "appdata" in _cache:
        return _cache["appdata"]

    result: Path | None = None
    if platform.system() == "Windows":
        env = os.environ.get("APPDATA")
        if env:
            result = Path(env)
        else:
            result = _get_windows_known_folder("RoamingAppData")

    if result is None:
        result = Path.home()

    _cache["appdata"] = result
    return result


def get_localappdata_dir() -> Path:
    """Return ``%LOCALAPPDATA%`` on Windows, ``~`` elsewhere."""
    if "localappdata" in _cache:
        return _cache["localappdata"]

    result: Path | None = None
    if platform.system() == "Windows":
        env = os.environ.get("LOCALAPPDATA")
        if env:
            result = Path(env)
        else:
            result = _get_windows_known_folder("LocalAppData")

    if result is None:
        result = Path.home()

    _cache["localappdata"] = result
    return result


def get_home_dir() -> Path:
    """Return the user home directory."""
    return Path.home()


# ---------------------------------------------------------------------------
# Placeholder table  (ordered longest-match-first for ``to_portable_path``)
# ---------------------------------------------------------------------------

def _placeholder_map() -> list[tuple[str, Path]]:
    """Return ``[(placeholder, real_path), ...]`` sorted longest path first.

    Sorting ensures that more specific directories (e.g. ``${DOCUMENTS}``)
    are matched before shorter prefixes (e.g. ``${HOME}``).
    """
    entries = [
        ("${DOCUMENTS}", get_documents_dir()),
        ("${APPDATA}", get_appdata_dir()),
        ("${LOCALAPPDATA}", get_localappdata_dir()),
        ("${HOME}", get_home_dir()),
    ]
    # Sort by path length descending so the most specific match wins.
    entries.sort(key=lambda x: len(str(x[1])), reverse=True)
    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_path(portable: str) -> Path:
    """Expand placeholders in *portable* and return an absolute ``Path``.

    If the string contains no placeholder it is returned as-is (assumed
    to already be absolute).
    """
    for placeholder, real in _placeholder_map():
        if portable.startswith(placeholder):
            rest = portable[len(placeholder):]
            # Strip leading separator that might follow the placeholder
            rest = rest.lstrip("/").lstrip("\\")
            return real / rest
    return Path(portable)


def to_portable_path(absolute: str | Path) -> str:
    """Replace the OS-specific prefix of *absolute* with a placeholder.

    Returns a forward-slash string like ``${DOCUMENTS}/PCSX2/memcards/…``
    that is safe to persist in JSON and resolves correctly on any machine.
    """
    abs_str = str(Path(absolute)).replace("\\", "/")
    for placeholder, real in _placeholder_map():
        real_str = str(real).replace("\\", "/")
        # Case-insensitive match on Windows
        if abs_str.lower().startswith(real_str.lower()):
            rest = abs_str[len(real_str):].lstrip("/")
            return f"{placeholder}/{rest}" if rest else placeholder
    # No match — return normalised path as-is
    return abs_str

