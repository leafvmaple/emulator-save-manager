"""Reusable save table component."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHeaderView, QTableWidgetItem, QAbstractItemView
from qfluentwidgets import TableWidget, SearchLineEdit

from app.i18n import t
from app.models.game_save import GameSave


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


class SaveTable(QWidget):
    """Filterable table displaying GameSave entries."""

    row_selected = Signal(int)  # row index

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._saves: list[GameSave] = []
        self._filtered: list[GameSave] = []
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._search = SearchLineEdit(self)
        self._search.setPlaceholderText(t("common.search"))
        self._search.textChanged.connect(self._on_filter)
        layout.addWidget(self._search)

        self._table = TableWidget(self)
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels([
            t("scan.emulator"), t("scan.game_name"), t("scan.game_id"),
            t("scan.save_type"), t("scan.size"), t("scan.last_modified"),
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().hide()
        self._table.setBorderVisible(True)
        self._table.setBorderRadius(8)
        self._table.cellClicked.connect(lambda row, _: self.row_selected.emit(row))
        layout.addWidget(self._table, stretch=1)

    def set_saves(self, saves: list[GameSave]) -> None:
        self._saves = saves
        self._on_filter(self._search.text())

    def _on_filter(self, text: str) -> None:
        keyword = text.strip().lower()
        if keyword:
            self._filtered = [
                s for s in self._saves
                if keyword in s.game_name.lower()
                or keyword in s.game_id.lower()
                or keyword in s.emulator.lower()
            ]
        else:
            self._filtered = list(self._saves)
        self._refresh()

    def _refresh(self) -> None:
        self._table.setRowCount(0)
        for save in self._filtered:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(save.emulator))
            self._table.setItem(row, 1, QTableWidgetItem(save.game_name))
            self._table.setItem(row, 2, QTableWidgetItem(save.game_id))
            save_types = ", ".join(set(
                t(f"save_type.{sf.save_type.value}") for sf in save.save_files
            ))
            self._table.setItem(row, 3, QTableWidgetItem(save_types))
            self._table.setItem(row, 4, QTableWidgetItem(_format_size(save.total_size)))
            lm = save.last_modified
            self._table.setItem(row, 5, QTableWidgetItem(
                lm.strftime("%Y/%m/%d %H:%M") if lm else "-"
            ))

    def get_save(self, row: int) -> GameSave | None:
        if 0 <= row < len(self._filtered):
            return self._filtered[row]
        return None
