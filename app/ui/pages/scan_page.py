"""Scan page — emulator detection and save scanning."""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import Qt, Signal, QThread, QPoint
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QHeaderView, QTableWidgetItem,
    QAbstractItemView,
)
from qfluentwidgets import (
    SubtitleLabel, BodyLabel, PrimaryPushButton, PushButton,
    TableWidget, CardWidget, FluentIcon as FIF, InfoBar,
    InfoBarPosition, ProgressRing, setFont, RoundMenu, Action,
)
from loguru import logger

from app.i18n import t
from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave


class _ScanWorker(QThread):
    """Background thread for scanning."""

    finished = Signal(list, list)  # (emulators, saves)
    error = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scanner = None

    def set_scanner(self, scanner):  # noqa: ANN001
        self._scanner = scanner

    def run(self) -> None:
        try:
            emulators, saves = self._scanner.full_scan()
            self.finished.emit(emulators, saves)
        except Exception as e:
            self.error.emit(str(e))


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


class ScanPage(QWidget):
    """Page for detecting emulators and scanning game saves."""

    saves_updated = Signal(list)  # list[GameSave]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("scan_page")
        self._emulators: list[EmulatorInfo] = []
        self._saves: list[GameSave] = []
        self._scanner = None
        self._worker: _ScanWorker | None = None
        self._init_ui()

    def set_scanner(self, scanner) -> None:  # noqa: ANN001
        self._scanner = scanner

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(16)

        # Title
        title = SubtitleLabel(t("scan.title"), self)
        desc = BodyLabel(t("scan.description"), self)
        desc.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(desc)

        # Action bar
        action_bar = QHBoxLayout()
        self._scan_btn = PrimaryPushButton(FIF.SEARCH, t("scan.start_scan"), self)
        self._scan_btn.setFixedWidth(160)
        self._scan_btn.clicked.connect(self._on_scan)
        action_bar.addWidget(self._scan_btn)

        self._progress = ProgressRing(self)
        self._progress.setFixedSize(24, 24)
        self._progress.hide()
        action_bar.addWidget(self._progress)

        self._status_label = BodyLabel("", self)
        action_bar.addWidget(self._status_label)
        action_bar.addStretch()
        layout.addLayout(action_bar)

        # Emulator cards area
        self._emu_cards_layout = QHBoxLayout()
        self._emu_cards_layout.setSpacing(12)
        layout.addLayout(self._emu_cards_layout)

        # Save table
        self._table = TableWidget(self)
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            t("scan.emulator"),
            t("scan.game_name"),
            t("scan.game_id"),
            "CRC32",
            t("scan.save_type"),
            t("scan.size"),
            t("scan.last_modified"),
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().hide()
        self._table.setBorderVisible(True)
        self._table.setBorderRadius(8)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        layout.addWidget(self._table, stretch=1)

    # ------------------------------------------------------------------
    # Scan logic
    # ------------------------------------------------------------------

    def _on_scan(self) -> None:
        if self._scanner is None:
            InfoBar.warning(
                title=t("common.warning"),
                content="Scanner not initialized",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )
            return

        self._scan_btn.setEnabled(False)
        self._scan_btn.setText(t("scan.scanning"))
        self._progress.show()
        self._status_label.setText(t("scan.scanning"))

        self._worker = _ScanWorker(self)
        self._worker.set_scanner(self._scanner)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _on_scan_finished(self, emulators: list, saves: list) -> None:
        self._emulators = emulators
        self._saves = saves
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText(t("scan.start_scan"))
        self._progress.hide()
        self._status_label.setText(
            f"{t('scan.found_emulators', count=str(len(emulators)))} | "
            f"{t('scan.found_saves', count=str(len(saves)))}"
        )
        self._refresh_emulator_cards()
        self._refresh_table()
        self.saves_updated.emit(saves)

        InfoBar.success(
            title=t("scan.scan_complete"),
            content=t("scan.found_saves", count=str(len(saves))),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )

    def _on_scan_error(self, error: str) -> None:
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText(t("scan.start_scan"))
        self._progress.hide()
        self._status_label.setText("")
        InfoBar.error(
            title=t("common.error"),
            content=error,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _refresh_emulator_cards(self) -> None:
        # Clear existing cards
        while self._emu_cards_layout.count():
            item = self._emu_cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for emu in self._emulators:
            card = self._create_emu_card(emu)
            self._emu_cards_layout.addWidget(card)
        self._emu_cards_layout.addStretch()

    def _create_emu_card(self, emu: EmulatorInfo) -> CardWidget:
        card = CardWidget(self)
        card.setFixedSize(220, 100)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 12, 16, 12)
        name_label = SubtitleLabel(emu.name, card)
        setFont(name_label, 14)
        vbox.addWidget(name_label)

        path_label = BodyLabel(str(emu.data_path), card)
        path_label.setWordWrap(True)
        setFont(path_label, 11)
        vbox.addWidget(path_label)

        save_count = sum(
            1 for s in self._saves if s.emulator == emu.name
        )
        count_label = BodyLabel(
            f"{t('scan.saves_count')}: {save_count}", card
        )
        setFont(count_label, 11)
        vbox.addWidget(count_label)
        return card

    def _refresh_table(self) -> None:
        self._table.setRowCount(0)
        for save in self._saves:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(save.emulator))
            self._table.setItem(row, 1, QTableWidgetItem(save.game_name))
            self._table.setItem(row, 2, QTableWidgetItem(save.game_id))
            self._table.setItem(row, 3, QTableWidgetItem(save.crc32 or "-"))

            save_types = ", ".join(set(
                t(f"save_type.{sf.save_type.value}") for sf in save.save_files
            ))
            self._table.setItem(row, 4, QTableWidgetItem(save_types))
            self._table.setItem(row, 5, QTableWidgetItem(_format_size(save.total_size)))

            lm = save.last_modified
            lm_str = lm.strftime("%Y/%m/%d %H:%M") if lm else "-"
            self._table.setItem(row, 6, QTableWidgetItem(lm_str))

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _on_table_context_menu(self, pos: QPoint) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._saves):
            return

        save = self._saves[row]
        menu = RoundMenu(parent=self)

        open_dir_action = Action(FIF.FOLDER, t("common.open_folder"), self)
        open_dir_action.triggered.connect(lambda: self._open_save_directory(save))
        menu.addAction(open_dir_action)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    @staticmethod
    def _open_save_directory(save: GameSave) -> None:
        """Open the directory containing the first save file in the OS file manager."""
        if not save.save_files:
            return
        target = save.save_files[0].path
        folder = target.parent if target.is_file() else target
        if not folder.exists():
            return
        if sys.platform == "win32":
            os.startfile(str(folder))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])  # noqa: S603
        else:
            subprocess.Popen(["xdg-open", str(folder)])  # noqa: S603

    def get_saves(self) -> list[GameSave]:
        return list(self._saves)
