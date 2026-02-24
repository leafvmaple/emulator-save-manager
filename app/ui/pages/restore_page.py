"""Restore page — restore game saves from versioned backups."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QHeaderView, QTableWidgetItem,
    QAbstractItemView, QTreeWidgetItem,
)
from qfluentwidgets import (
    SubtitleLabel, BodyLabel, PrimaryPushButton, PushButton,
    TableWidget, TreeWidget, FluentIcon as FIF, InfoBar, InfoBarPosition,
    MessageBox, ProgressRing,
)
from loguru import logger

from app.i18n import t
from app.models.backup_record import BackupRecord


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


class RestorePage(QWidget):
    """Page for browsing and restoring backups."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("restore_page")
        self._backup_manager = None
        self._restore_manager = None
        self._all_backups: dict[str, list[BackupRecord]] = {}
        self._init_ui()

    def set_managers(self, backup_manager, restore_manager) -> None:  # noqa: ANN001
        self._backup_manager = backup_manager
        self._restore_manager = restore_manager

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(16)

        title = SubtitleLabel(t("restore.title"), self)
        desc = BodyLabel(t("restore.description"), self)
        desc.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(desc)

        # Action bar
        action_bar = QHBoxLayout()
        refresh_btn = PushButton(FIF.SYNC, t("common.refresh"), self)
        refresh_btn.clicked.connect(self._refresh_backups)
        action_bar.addWidget(refresh_btn)

        self._restore_btn = PrimaryPushButton(FIF.HISTORY, t("restore.restore_selected"), self)
        self._restore_btn.setFixedWidth(160)
        self._restore_btn.clicked.connect(self._on_restore)
        action_bar.addWidget(self._restore_btn)

        self._progress = ProgressRing(self)
        self._progress.setFixedSize(24, 24)
        self._progress.hide()
        action_bar.addWidget(self._progress)

        self._status_label = BodyLabel("", self)
        action_bar.addWidget(self._status_label)
        action_bar.addStretch()
        layout.addLayout(action_bar)

        # Tree widget for backups
        self._tree = TreeWidget(self)
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels([
            t("scan.game_name"),
            t("restore.version"),
            t("restore.backup_time"),
            t("scan.emulator"),
        ])
        self._tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self._tree, stretch=1)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh_backups(self) -> None:
        if self._backup_manager is None:
            return
        self._all_backups = self._backup_manager.list_all_backups()
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        self._tree.clear()
        for key, records in self._all_backups.items():
            if not records:
                continue
            latest = records[0]
            game_item = QTreeWidgetItem([
                latest.game_save.game_name,
                f"{len(records)} {t('backup.backup_count').lower()}",
                latest.display_time,
                latest.game_save.emulator,
            ])
            # Add version history as children
            for r in records:
                pin_marker = " [P]" if r.is_pinned else ""
                label_marker = f" ({r.label})" if r.label else ""
                child = QTreeWidgetItem([
                    f"  v{r.version}{pin_marker}{label_marker}",
                    str(r.version),
                    r.display_time,
                    r.game_save.emulator,
                ])
                child.setData(0, Qt.ItemDataRole.UserRole, r)
                game_item.addChild(child)
            self._tree.addTopLevelItem(game_item)

    # ------------------------------------------------------------------
    # Restore action
    # ------------------------------------------------------------------

    def _on_restore(self) -> None:
        if self._restore_manager is None:
            return

        items = self._tree.selectedItems()
        if not items:
            InfoBar.warning(
                title=t("common.warning"),
                content=t("restore.restore_selected"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )
            return

        item = items[0]
        record: BackupRecord | None = item.data(0, Qt.ItemDataRole.UserRole)
        if record is None:
            # User selected a parent node; select the latest child
            if item.childCount() > 0:
                record = item.child(0).data(0, Qt.ItemDataRole.UserRole)
        if record is None:
            return

        # Preview and confirm
        changes = self._restore_manager.preview_restore(record)
        has_newer = any(c.is_newer_locally for c in changes)

        if has_newer:
            box = MessageBox(
                t("restore.confirm_restore"),
                t("restore.overwrite_warning"),
                self,
            )
            if not box.exec():
                return

        self._progress.show()
        errors = self._restore_manager.restore_backup(record, force=True)
        self._progress.hide()

        if errors:
            InfoBar.warning(
                title=t("restore.restore_complete"),
                content="\n".join(errors),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
        else:
            InfoBar.success(
                title=t("restore.restore_complete"),
                content=t("restore.restore_success", count="1"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )
