"""Sync page — multi-device sync via shared folder."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
)
from qfluentwidgets import (
    SubtitleLabel, BodyLabel, CaptionLabel, PrimaryPushButton, PushButton,
    CardWidget, FluentIcon as FIF, InfoBar, InfoBarPosition,
    ProgressRing, setFont,
)
from loguru import logger

from app.i18n import t
from app.core.sync import SyncResult


class _SyncWorker(QThread):
    """Background thread for sync operations."""

    finished = Signal(object)  # SyncResult
    error = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sync_manager = None
        self._mode = "sync_all"

    def set_data(self, sync_manager, mode: str = "sync_all") -> None:  # noqa: ANN001
        self._sync_manager = sync_manager
        self._mode = mode

    def run(self) -> None:
        try:
            result = self._sync_manager.sync_all()
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class SyncPage(QWidget):
    """Page for multi-device sync management."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sync_page")
        self._sync_manager = None
        self._config = None
        self._worker: _SyncWorker | None = None
        self._init_ui()

    def set_sync_manager(self, sm) -> None:  # noqa: ANN001
        self._sync_manager = sm

    def set_config(self, config) -> None:  # noqa: ANN001
        self._config = config
        self._refresh_status()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(16)

        title = SubtitleLabel(t("sync.title"), self)
        desc = BodyLabel(t("sync.description"), self)
        desc.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(desc)

        # Status card
        self._status_card = CardWidget(self)
        self._status_card.setFixedHeight(120)
        status_layout = QVBoxLayout(self._status_card)
        status_layout.setContentsMargins(20, 16, 20, 16)

        self._sync_folder_label = BodyLabel(t("sync.not_configured"), self._status_card)
        self._machine_id_label = CaptionLabel("", self._status_card)
        self._last_sync_label = CaptionLabel("", self._status_card)

        status_layout.addWidget(self._sync_folder_label)
        status_layout.addWidget(self._machine_id_label)
        status_layout.addWidget(self._last_sync_label)
        layout.addWidget(self._status_card)

        # Action buttons
        action_bar = QHBoxLayout()

        self._sync_btn = PrimaryPushButton(FIF.SYNC, t("sync.sync_all"), self)
        self._sync_btn.setFixedWidth(160)
        self._sync_btn.clicked.connect(self._on_sync)
        action_bar.addWidget(self._sync_btn)

        self._push_btn = PushButton(FIF.UP, t("sync.push"), self)
        self._push_btn.setFixedWidth(120)
        self._push_btn.clicked.connect(self._on_push)
        action_bar.addWidget(self._push_btn)

        self._pull_btn = PushButton(FIF.DOWN, t("sync.pull"), self)
        self._pull_btn.setFixedWidth(120)
        self._pull_btn.clicked.connect(self._on_pull)
        action_bar.addWidget(self._pull_btn)

        self._progress = ProgressRing(self)
        self._progress.setFixedSize(24, 24)
        self._progress.hide()
        action_bar.addWidget(self._progress)

        self._status_msg = BodyLabel("", self)
        action_bar.addWidget(self._status_msg)
        action_bar.addStretch()
        layout.addLayout(action_bar)

        # Sync log area
        self._log_label = SubtitleLabel(t("sync.sync_complete"), self)
        self._log_label.hide()
        layout.addWidget(self._log_label)

        self._log_body = BodyLabel("", self)
        self._log_body.setWordWrap(True)
        layout.addWidget(self._log_body)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        if self._config is None:
            return
        sf = self._config.sync_folder
        if sf and sf.exists():
            self._sync_folder_label.setText(f"{t('sync.sync_folder')}: {sf}")
        else:
            self._sync_folder_label.setText(t("sync.not_configured"))
        self._machine_id_label.setText(f"{t('sync.machine_id')}: {self._config.machine_id}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_sync(self) -> None:
        self._run_sync("sync_all")

    def _on_push(self) -> None:
        self._run_sync("push")

    def _on_pull(self) -> None:
        self._run_sync("pull")

    def _run_sync(self, mode: str) -> None:
        if self._sync_manager is None:
            InfoBar.warning(
                title=t("common.warning"),
                content="Sync manager not initialized",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )
            return

        if not self._sync_manager.is_configured:
            InfoBar.warning(
                title=t("common.warning"),
                content=t("sync.not_configured"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )
            return

        self._sync_btn.setEnabled(False)
        self._push_btn.setEnabled(False)
        self._pull_btn.setEnabled(False)
        self._progress.show()
        self._status_msg.setText(t("sync.syncing"))

        self._worker = _SyncWorker(self)
        self._worker.set_data(self._sync_manager, mode)
        self._worker.finished.connect(self._on_sync_finished)
        self._worker.error.connect(self._on_sync_error)
        self._worker.start()

    def _on_sync_finished(self, result: SyncResult) -> None:
        self._sync_btn.setEnabled(True)
        self._push_btn.setEnabled(True)
        self._pull_btn.setEnabled(True)
        self._progress.hide()

        msg = t("sync.sync_success", push=str(result.pushed), pull=str(result.pulled))
        self._status_msg.setText(msg)

        if result.conflicts:
            msg += f"\n{t('sync.conflict_found', count=str(len(result.conflicts)))}"
            InfoBar.warning(
                title=t("sync.sync_complete"),
                content=msg,
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
            # TODO: open conflict dialog
        else:
            InfoBar.success(
                title=t("sync.sync_complete"),
                content=msg,
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )

        if result.errors:
            self._log_label.show()
            self._log_body.setText("\n".join(result.errors))
        else:
            self._log_label.hide()
            self._log_body.setText("")

        # CRC32 mismatch warnings
        if result.crc32_warnings:
            for warn in result.crc32_warnings:
                InfoBar.warning(
                    title=t("sync.crc32_mismatch_title"),
                    content=t("sync.crc32_mismatch_desc") + "\n" + warn,
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=8000,
                )

    def _on_sync_error(self, error: str) -> None:
        self._sync_btn.setEnabled(True)
        self._push_btn.setEnabled(True)
        self._pull_btn.setEnabled(True)
        self._progress.hide()
        self._status_msg.setText("")
        InfoBar.error(
            title=t("common.error"),
            content=error,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )
