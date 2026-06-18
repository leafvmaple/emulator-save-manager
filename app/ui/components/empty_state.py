"""Centered empty-state placeholder — an icon, a title and a hint.

Replaces the lone grey "no data" label the pages used to show, so an empty
scan / backup / restore list reads as intentional rather than broken.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout
from qfluentwidgets import IconWidget, StrongBodyLabel, CaptionLabel, FluentIconBase

from app.ui import theme


class EmptyState(QWidget):
    """A vertically-centered icon + title + description block."""

    def __init__(
        self,
        icon: FluentIconBase,
        title: str,
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.setSpacing(theme.GAP_SM)
        box.setContentsMargins(theme.GAP_XL, theme.GAP_XL, theme.GAP_XL, theme.GAP_XL)

        self._icon = IconWidget(icon, self)
        self._icon.setFixedSize(48, 48)
        # Muted icon so it recedes like the surrounding text.
        self._icon.setStyleSheet(f"color:{theme.text_muted()};")
        box.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignHCenter)

        self._title = StrongBodyLabel(title, self)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self._title)

        self._desc = CaptionLabel(description, self)
        self._desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._desc.setWordWrap(True)
        self._desc.setStyleSheet(f"color:{theme.text_muted()};")
        self._desc.setVisible(bool(description))
        box.addWidget(self._desc)

    def set_text(self, title: str, description: str = "") -> None:
        self._title.setText(title)
        self._desc.setText(description)
        self._desc.setVisible(bool(description))
