"""Deterministic letter-avatar placeholder for games without cover art.

A rounded tile coloured from a hash of the game's name with its first glyph in
white — distinct per game and far more polished than repeating the emulator
logo on every card.
"""

from __future__ import annotations

import hashlib

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPixmap, QPainter, QColor, QFont, QPainterPath

# Saturated, white-text-friendly tints (read on light and dark surfaces).
_PALETTE = [
    "#0078d4", "#107c10", "#8764b8", "#d83b01", "#0099bc",
    "#c30052", "#498205", "#b146c2", "#005b70", "#ca5010",
]


def _color_for(text: str) -> QColor:
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()
    return QColor(_PALETTE[int(digest, 16) % len(_PALETTE)])


def letter_avatar(text: str, size: int) -> QPixmap:
    """Return a *size*×*size* rounded letter-avatar for *text*."""
    text = (text or "?").strip() or "?"
    glyph = text[0].upper()

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
    p.fillPath(path, _color_for(text))

    p.setPen(QColor("white"))
    font = QFont()
    font.setPixelSize(int(size * 0.5))
    font.setWeight(QFont.Weight.DemiBold)
    p.setFont(font)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, glyph)
    p.end()
    return pm
