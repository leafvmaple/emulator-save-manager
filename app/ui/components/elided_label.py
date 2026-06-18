"""Qt labels that shrink with right-side ellipsis instead of widening rows."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy
from qfluentwidgets import setFont


class ElidedLabel(QLabel):
    """QLabel with right-side elision that does not force horizontal scroll."""

    def __init__(self, text: str = "", parent=None) -> None:  # noqa: ANN001
        super().__init__("", parent)
        self._full_text = ""
        self._display_text = ""
        self._is_setting_elided = False
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.setText(text)

    @property
    def full_text(self) -> str:
        return self._full_text

    def setText(self, text: str) -> None:  # noqa: N802
        if self._is_setting_elided:
            super().setText(self._display_text)
            return
        self._full_text = "" if text is None else str(text)
        self.setToolTip(self._full_text)
        self._update_elided_text()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._update_elided_text()

    def changeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.FontChange,
            QEvent.Type.ApplicationFontChange,
            QEvent.Type.StyleChange,
        }:
            self._update_elided_text()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        size = super().minimumSizeHint()
        size.setWidth(0)
        return size

    def sizeHint(self) -> QSize:  # noqa: N802
        size = super().sizeHint()
        size.setWidth(min(size.width(), 320))
        return size

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        rect = self.contentsRect()
        painter.setClipRect(rect)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        painter.drawText(
            rect,
            self.alignment() | Qt.AlignmentFlag.AlignVCenter,
            self._display_text,
        )

    def _update_elided_text(self) -> None:
        width = self.contentsRect().width()
        if width <= 0:
            width = self.sizeHint().width()
        self._display_text = self.fontMetrics().elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            max(0, width),
        )
        self._is_setting_elided = True
        try:
            super().setText(self._display_text)
        finally:
            self._is_setting_elided = False
        self.update()


class ElidedCaptionLabel(ElidedLabel):
    """Caption-style elided label."""

    def __init__(self, text: str = "", parent=None) -> None:  # noqa: ANN001
        super().__init__(text, parent)
        setFont(self, 12)


class ElidedStrongBodyLabel(ElidedLabel):
    """Strong body-style elided label."""

    def __init__(self, text: str = "", parent=None) -> None:  # noqa: ANN001
        super().__init__(text, parent)
        setFont(self, 14, QFont.Weight.DemiBold)