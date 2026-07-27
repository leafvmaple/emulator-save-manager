"""Save-info plugin contract (version 1).

A save-info plugin reads a game's save file and extracts key facts (hero,
progress, money, location, …) so the UI can identify a save without
launching the game.  Plugins are pure Python and run in-process — the
manager must stay self-contained, so a plugin may not shell out to
external tools (see docs/save-info-plugins.md).

Contract
~~~~~~~~
Input   the save file path, plus a platform hint and the UI language.
Output  a :class:`SaveInfoResult`: a ``matched`` verdict, a one-line
        ``summary``, and ``groups`` of already-localized key/value strings.
        The manager renders them verbatim — it knows nothing about games.

``matches()`` is a cheap pre-filter (extension / size / platform) used to
decide whether to offer the info action at all; ``extract()`` is the
authority — it must validate the file's own structure (magic, checksums)
and return ``matched=False`` with a human-readable ``reason`` for foreign
files, rather than guessing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SaveInfoItem:
    """One display fact: a localized key and a display-ready value."""

    key: str
    value: str


@dataclass
class SaveInfoGroup:
    """A titled section of items (e.g. one in-game save slot)."""

    label: str
    items: list[SaveInfoItem] = field(default_factory=list)


@dataclass
class SaveInfoResult:
    """What a plugin learned about one save file."""

    matched: bool
    """True if the file was positively identified as this plugin's game."""

    plugin_id: str = ""
    plugin_name: str = ""

    title: str = ""
    """Localized game title as recognised by the plugin."""

    summary: str = ""
    """One-line digest of the most relevant save slot (for rows/tooltips)."""

    reason: str = ""
    """Only when ``matched`` is False: localized 'why not' text."""

    groups: list[SaveInfoGroup] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)
    """Localized anomalies worth highlighting (e.g. bad checksum)."""

    error: str = ""
    """Set by the manager when the plugin raised instead of returning."""


class SaveInfoPlugin(ABC):
    """Base class every save-info plugin must implement."""

    @property
    @abstractmethod
    def plugin_id(self) -> str:
        """Stable unique id, kebab-case (e.g. ``metal-max-fc``)."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable plugin name shown in logs/UI."""
        ...

    @abstractmethod
    def matches(self, path: Path, size: int, platform: str = "") -> bool:
        """Cheap pre-filter: could *path* plausibly be this game's save?

        Called for every scanned save file — keep it to extension, size and
        platform checks; do not read the file here.
        """
        ...

    @abstractmethod
    def extract(self, path: Path, lang: str = "en_US") -> SaveInfoResult:
        """Read the file and return localized facts, or ``matched=False``.

        Must not raise for unrecognized/corrupt content — that is a normal
        outcome (``matched=False`` + ``reason``).  Only genuine I/O errors
        may propagate; the manager converts them into ``error`` results.
        """
        ...
