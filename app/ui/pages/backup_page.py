"""Backup page — create and manage versioned backups of game saves."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QHeaderView, QTableWidgetItem,
    QAbstractItemView,
)
from qfluentwidgets import (
    SubtitleLabel, BodyLabel, PrimaryPushButton, PushButton,
    TableWidget, FluentIcon as FIF, InfoBar, InfoBarPosition,
    CheckBox, ProgressRing,
)
from loguru import logger

from app.i18n import t
from app.models.game_save import GameSave


class _BackupWorker(QThread):
    """Background thread for performing backups."""

    progress = Signal(int, int)  # (current, total)
    finished = Signal(int, list)  # (success_count, errors)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backup_manager = None
        self._saves: list[GameSave] = []

    def set_data(self, backup_manager, saves: list[GameSave]) -> None:  # noqa: ANN001
        self._backup_manager = backup_manager
        self._saves = saves

    def run(self) -> None:
        success = 0
        errors: list[str] = []
        total = len(self._saves)
        for i, save in enumerate(self._saves, start=1):
            try:
                self._backup_manager.create_backup(save)
                success += 1
            except Exception as e:
                errors.append(f"{save.game_name}: {e}")
            self.progress.emit(i, total)
        self.finished.emit(success, errors)


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


class BackupPage(QWidget):
    """Page for backing up scanned game saves."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("backup_page")
        self._saves: list[GameSave] = []
        self._backup_manager = None
        self._worker: _BackupWorker | None = None
        self._init_ui()

    def set_backup_manager(self, bm) -> None:  # noqa: ANN001
        self._backup_manager = bm

    def update_saves(self, saves: list[GameSave]) -> None:
        self._saves = saves
        self._refresh_table()

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(16)

        title = SubtitleLabel(t("backup.title"), self)
        desc = BodyLabel(t("backup.description"), self)
        desc.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(desc)

        # Actions
        action_bar = QHBoxLayout()
        self._select_all_cb = CheckBox(t("common.select_all"), self)
        self._select_all_cb.stateChanged.connect(self._on_select_all)
        action_bar.addWidget(self._select_all_cb)

        self._backup_btn = PrimaryPushButton(FIF.SAVE, t("backup.backup_selected"), self)
        self._backup_btn.setFixedWidth(160)
        self._backup_btn.clicked.connect(self._on_backup)
        action_bar.addWidget(self._backup_btn)

        self._progress = ProgressRing(self)
        self._progress.setFixedSize(24, 24)
        self._progress.hide()
        action_bar.addWidget(self._progress)

        self._status_label = BodyLabel("", self)
        action_bar.addWidget(self._status_label)
        action_bar.addStretch()
        layout.addLayout(action_bar)

        # Table
        self._table = TableWidget(self)
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels([
            "",  # checkbox column
            t("scan.emulator"),
            t("scan.game_name"),
            t("scan.game_id"),
            t("scan.size"),
            t("scan.last_modified"),
        ])
        self._table.setColumnWidth(0, 40)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().hide()
        self._table.setBorderVisible(True)
        self._table.setBorderRadius(8)
        layout.addWidget(self._table, stretch=1)

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def _refresh_table(self) -> None:
        self._table.setRowCount(0)
        for save in self._saves:
            row = self._table.rowCount()
            self._table.insertRow(row)

            cb = CheckBox(self)
            self._table.setCellWidget(row, 0, cb)
            self._table.setItem(row, 1, QTableWidgetItem(save.emulator))
            self._table.setItem(row, 2, QTableWidgetItem(save.game_name))
            self._table.setItem(row, 3, QTableWidgetItem(save.game_id))
            self._table.setItem(row, 4, QTableWidgetItem(_format_size(save.total_size)))
            lm = save.last_modified
            self._table.setItem(row, 5, QTableWidgetItem(
                lm.strftime("%Y/%m/%d %H:%M") if lm else "-"
            ))

    def _on_select_all(self, state: int) -> None:
        checked = state == Qt.CheckState.Checked.value
        for row in range(self._table.rowCount()):
            cb = self._table.cellWidget(row, 0)
            if isinstance(cb, CheckBox):
                cb.setChecked(checked)

    def _get_selected_saves(self) -> list[GameSave]:
        selected: list[GameSave] = []
        for row in range(self._table.rowCount()):
            cb = self._table.cellWidget(row, 0)
            if isinstance(cb, CheckBox) and cb.isChecked():
                if row < len(self._saves):
                    selected.append(self._saves[row])
        return selected

    # ------------------------------------------------------------------
    # Backup action
    # ------------------------------------------------------------------

    def _on_backup(self) -> None:
        if self._backup_manager is None:
            InfoBar.warning(
                title=t("common.warning"),
                content="Backup manager not initialized",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )
            return

        selected = self._get_selected_saves()
        if not selected:
            InfoBar.warning(
                title=t("common.warning"),
                content=t("backup.backup_selected"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )
            return

        self._backup_btn.setEnabled(False)
        self._backup_btn.setText(t("backup.backing_up"))
        self._progress.show()

        self._worker = _BackupWorker(self)
        self._worker.set_data(self._backup_manager, selected)
        self._worker.progress.connect(self._on_backup_progress)
        self._worker.finished.connect(self._on_backup_finished)
        self._worker.start()

    def _on_backup_progress(self, current: int, total: int) -> None:
        self._status_label.setText(f"{current}/{total}")

    def _on_backup_finished(self, success: int, errors: list) -> None:
        self._backup_btn.setEnabled(True)
        self._backup_btn.setText(t("backup.backup_selected"))
        self._progress.hide()
        self._status_label.setText("")

        if errors:
            InfoBar.warning(
                title=t("backup.backup_complete"),
                content=t("backup.backup_success", count=str(success)) + f"\n{len(errors)} errors",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
        else:
            InfoBar.success(
                title=t("backup.backup_complete"),
                content=t("backup.backup_success", count=str(success)),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )
