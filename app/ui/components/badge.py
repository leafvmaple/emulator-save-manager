"""Reusable coloured pill badge for save types / statuses.

Replaces the three near-identical ``_TypeBadge`` copies that lived in the
scan / backup / restore pages.  The fill is a solid theme-aware status colour
with white text on top; sizing hugs the text.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget
from qfluentwidgets import setFont

from app.i18n import t
from app.ui import theme


class TypeBadge(QLabel):
    """A small solid-coloured pill, e.g. a save-type or diff-status tag."""

    def __init__(self, text: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFixedHeight(18)
        setFont(self, 10)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedWidth(max(24, self.fontMetrics().horizontalAdvance(text) + 14))
        self.setStyleSheet(
            f"background:{color}; color:{theme.on_accent()}; "
            f"border-radius:{theme.RADIUS_PILL}px; padding:0 5px; font-weight:500;"
        )

    @classmethod
    def for_save_type(cls, type_key: str, parent: QWidget | None = None) -> "TypeBadge":
        """Build a badge for a save-type / status key (e.g. ``"savestate"``).

        The label is localized via ``save_type.<key>`` and the fill is the
        theme status colour for that key.
        """
        return cls(t(f"save_type.{type_key}"), theme.status_fill(type_key), parent)
