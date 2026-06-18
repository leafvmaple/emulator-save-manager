"""Reusable page header — a title plus a muted, wrapping description line.

Every top-level page opened the same way (a ``SubtitleLabel`` title followed
by a wrapped ``BodyLabel`` description).  ``PageHeader`` captures that so the
five pages stay visually identical and gain a single place to tune.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout
from qfluentwidgets import SubtitleLabel, BodyLabel

from app.ui import theme


class PageHeader(QWidget):
    """Title + description block used at the top of each page."""

    def __init__(
        self,
        title: str,
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.GAP_XS)

        self._title = SubtitleLabel(title, self)
        box.addWidget(self._title)

        self._desc = BodyLabel(description, self)
        self._desc.setWordWrap(True)
        self._desc.setVisible(bool(description))
        box.addWidget(self._desc)

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_description(self, text: str) -> None:
        self._desc.setText(text)
        self._desc.setVisible(bool(text))
