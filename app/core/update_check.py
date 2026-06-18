"""GitHub Release update checking."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable
from urllib.request import Request, urlopen

REPO_SLUG = "leafvmaple/emulator-save-manager"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPO_SLUG}/releases/latest"


@dataclass(frozen=True)
class UpdateInfo:
    """Result of checking the latest GitHub Release."""

    current_version: str
    latest_version: str
    release_url: str
    release_name: str = ""
    published_at: str = ""

    @property
    def is_update_available(self) -> bool:
        return is_newer_version(self.latest_version, self.current_version)


def _version_tuple(version: str) -> tuple[int, ...]:
    """Return numeric version parts, ignoring a leading ``v`` and suffixes."""
    cleaned = version.strip().removeprefix("v").removeprefix("V")
    parts = re.findall(r"\d+", cleaned)
    return tuple(int(p) for p in parts) or (0,)


def is_newer_version(candidate: str, current: str) -> bool:
    """True if *candidate* is newer than *current* using semantic-ish numbers."""
    left = list(_version_tuple(candidate))
    right = list(_version_tuple(current))
    width = max(len(left), len(right))
    left.extend([0] * (width - len(left)))
    right.extend([0] * (width - len(right)))
    return tuple(left) > tuple(right)


def check_latest_release(
    current_version: str,
    fetcher: Callable | None = None,
    timeout: float = 8,
) -> UpdateInfo:
    """Fetch the latest GitHub Release and compare it with *current_version*.

    ``fetcher`` is injectable for tests; it must behave like
    :func:`urllib.request.urlopen`.
    """
    fetch = fetcher or urlopen
    req = Request(
        LATEST_RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "EmulatorSaveManager",
        },
    )
    with fetch(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    latest = str(payload.get("tag_name") or payload.get("name") or "").strip()
    if not latest:
        raise ValueError("Latest release response did not include a tag")

    return UpdateInfo(
        current_version=current_version,
        latest_version=latest.removeprefix("v").removeprefix("V"),
        release_url=str(payload.get("html_url") or ""),
        release_name=str(payload.get("name") or latest),
        published_at=str(payload.get("published_at") or ""),
    )
