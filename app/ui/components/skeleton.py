"""Loading-skeleton placeholder card, shown while a scan is in progress.

A few of these convey "loading" far better than a lone spinner and steady the
layout so real cards don't pop in from an empty page.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout
from qfluentwidgets import SimpleCardWidget, isDarkTheme

from app.ui import theme


def _block(width: int, height: int, parent: QWidget | None = None) -> QWidget:
    """A single rounded grey placeholder rectangle."""
    fill = "rgba(255, 255, 255, 0.13)" if isDarkTheme() else "rgba(0, 0, 0, 0.08)"
    blk = QWidget(parent)
    blk.setFixedSize(width, height)
    blk.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    blk.setStyleSheet(f"background:{fill}; border-radius:{theme.RADIUS_SM}px;")
    return blk


class SkeletonCard(SimpleCardWidget):
    """A card-shaped placeholder mirroring the game-save card layout."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(78)
        root = QHBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 12)
        root.setSpacing(theme.GAP_MD)

        root.addWidget(_block(42, 42, self), 0, Qt.AlignmentFlag.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(theme.GAP_SM)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(_block(180, 14, self))
        col.addWidget(_block(120, 12, self))
        col.addWidget(_block(240, 12, self))
        root.addLayout(col, 1)
        root.addStretch()
