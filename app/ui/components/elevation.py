"""Hover-elevation helper — lift a card with a soft drop shadow on hover.

Attaches a :class:`QGraphicsDropShadowEffect` that animates in on mouse-enter
and out on leave, giving interactive cards a tactile "raise" without changing
their layout footprint.  The effect is disabled while at rest so idle cards pay
no rasterisation cost (important for long scrolling lists).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QEvent, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget
from qfluentwidgets import isDarkTheme


class _HoverElevation(QObject):
    def __init__(self, widget: QWidget, hover_blur: int, dy: int) -> None:
        super().__init__(widget)
        self._w = widget
        self._dy = dy
        self._hover_blur = hover_blur
        self._target = 0

        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(0)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 160 if isDarkTheme() else 70))
        shadow.setEnabled(False)  # no cost until hovered
        widget.setGraphicsEffect(shadow)
        self._shadow = shadow

        self._ani = QPropertyAnimation(shadow, b"blurRadius", self)
        self._ani.setDuration(130)
        self._ani.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._ani.finished.connect(self._on_finished)

        widget.installEventFilter(self)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: D401
        if e.type() == QEvent.Type.Enter:
            self._shadow.setEnabled(True)
            self._w.raise_()  # so the shadow renders over sibling cards
            self._shadow.setOffset(0, self._dy)
            self._animate(self._hover_blur)
        elif e.type() == QEvent.Type.Leave:
            self._animate(0)
        return False

    def _animate(self, target: int) -> None:
        self._target = target
        self._ani.stop()
        self._ani.setStartValue(self._shadow.blurRadius())
        self._ani.setEndValue(target)
        self._ani.start()

    def _on_finished(self) -> None:
        if self._target == 0:
            self._shadow.setOffset(0, 0)
            self._shadow.setEnabled(False)


def add_hover_elevation(widget: QWidget, hover_blur: int = 24, dy: int = 4) -> None:
    """Give *widget* a soft drop shadow that animates in on hover."""
    _HoverElevation(widget, hover_blur, dy)
