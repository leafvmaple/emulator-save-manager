"""Progress card — small card showing a single operation progress."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout
from qfluentwidgets import (
    CardWidget, BodyLabel, CaptionLabel, ProgressBar, setFont,
)


class ProgressCard(CardWidget):
    """Compact card showing an operation name, status text, and progress bar."""

    cancelled = Signal()

    def __init__(
        self,
        title: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setFixedHeight(90)
        self._init_ui(title)

    def _init_ui(self, title: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self._title = BodyLabel(title, self)
        setFont(self._title, 13)
        top.addWidget(self._title)
        top.addStretch()
        self._status = CaptionLabel("", self)
        top.addWidget(self._status)
        layout.addLayout(top)

        self._progress = ProgressBar(self)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._detail = CaptionLabel("", self)
        layout.addWidget(self._detail)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def set_detail(self, text: str) -> None:
        self._detail.setText(text)

    def set_progress(self, current: int, total: int) -> None:
        if total > 0:
            pct = int(current / total * 100)
            self._progress.setValue(pct)
            self._status.setText(f"{current}/{total}")
        else:
            self._progress.setValue(0)

    def set_indeterminate(self, on: bool = True) -> None:
        if on:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, 100)
