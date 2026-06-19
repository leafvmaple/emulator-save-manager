"""Sync page — multi-device sync via shared folder."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from qfluentwidgets import (
    SubtitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    PrimaryPushButton, PushButton, CardWidget, IconWidget,
    FluentIcon as FIF, InfoBar, InfoBarPosition, ProgressBar, ProgressRing, setFont,
)

from app.i18n import t
from app.core.sync import SyncProgress, SyncResult
from app.core.conflict import ConflictInfo, ConflictResolution
from app.ui import theme
from app.ui.components.page_header import PageHeader
from app.ui.components.conflict_dialog import ConflictDialog, BatchConflictDialog


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _short_text(text: str, limit: int = 42) -> str:
    return text if len(text) <= limit else text[:limit - 3] + "..."


class _SyncWorker(QThread):
    """Background thread for sync operations."""

    finished = Signal(object)  # SyncResult
    progress = Signal(object)  # SyncProgress
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
            cancel = self.isInterruptionRequested
            if self._mode == "push":
                result = self._sync_manager.push_all(
                    should_cancel=cancel,
                    progress_callback=self.progress.emit,
                )
            elif self._mode == "pull":
                result = self._sync_manager.pull_all(
                    should_cancel=cancel,
                    progress_callback=self.progress.emit,
                )
            else:
                result = self._sync_manager.sync_all(
                    should_cancel=cancel,
                    progress_callback=self.progress.emit,
                )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class _ResolveWorker(QThread):
    """Background thread that applies conflict resolutions."""

    finished = Signal(list)  # list[str] errors
    error = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sync_manager = None
        self._items: list[tuple[ConflictInfo, ConflictResolution]] = []

    def set_data(self, sync_manager, items) -> None:  # noqa: ANN001
        self._sync_manager = sync_manager
        self._items = items

    def run(self) -> None:
        try:
            errors: list[str] = []
            for conflict, resolution in self._items:
                errors.extend(self._sync_manager.apply_resolution(conflict, resolution))
            self.finished.emit(errors)
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
        self._resolve_worker: _ResolveWorker | None = None
        self._init_ui()

    def set_sync_manager(self, sm) -> None:  # noqa: ANN001
        self._sync_manager = sm

    def start_sync(self) -> None:
        """Public entry point for triggering a full sync (e.g. on startup)."""
        self._run_sync("sync_all")

    def set_config(self, config) -> None:  # noqa: ANN001
        self._config = config
        self._refresh_status()

    def restyle(self) -> None:
        """Re-apply theme-dependent styles after a live theme switch."""
        self._status_msg.setStyleSheet(f"color:{theme.text_muted()};")
        self._refresh_status()  # re-colours the status pill

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.PAGE_MARGIN_H, theme.PAGE_MARGIN_V,
                                  theme.PAGE_MARGIN_H, theme.PAGE_MARGIN_V)
        layout.setSpacing(theme.GAP_LG)

        layout.addWidget(PageHeader(t("sync.title"), t("sync.description"), self))

        # Status card — connection method / target / machine, with a status pill
        self._status_card = CardWidget(self)
        card_lay = QVBoxLayout(self._status_card)
        card_lay.setContentsMargins(20, 16, 20, 16)
        card_lay.setSpacing(theme.GAP_SM)

        head = QHBoxLayout()
        head.setSpacing(theme.GAP_SM)
        ic = IconWidget(FIF.SYNC, self._status_card)
        ic.setFixedSize(18, 18)
        head.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(StrongBodyLabel(t("sync.status_title"), self._status_card),
                       0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch()
        self._status_badge = QLabel(self._status_card)
        self._status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_badge.setFixedHeight(20)
        setFont(self._status_badge, 11)
        head.addWidget(self._status_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        card_lay.addLayout(head)

        self._method_value = self._info_row(card_lay, t("sync.method"))
        self._target_value = self._info_row(card_lay, t("sync.target"))
        self._machine_value = self._info_row(card_lay, t("sync.machine_id"))
        self._set_status_badge(False)
        layout.addWidget(self._status_card)

        # Action buttons
        av = Qt.AlignmentFlag.AlignVCenter
        action_bar = QHBoxLayout()
        action_bar.setSpacing(theme.GAP_SM)

        self._sync_btn = PrimaryPushButton(FIF.SYNC, t("sync.sync_all"), self)
        self._sync_btn.clicked.connect(self._on_sync)
        action_bar.addWidget(self._sync_btn, 0, av)

        self._push_btn = PushButton(FIF.UP, t("sync.push"), self)
        self._push_btn.clicked.connect(self._on_push)
        action_bar.addWidget(self._push_btn, 0, av)

        self._pull_btn = PushButton(FIF.DOWN, t("sync.pull"), self)
        self._pull_btn.clicked.connect(self._on_pull)
        action_bar.addWidget(self._pull_btn, 0, av)

        self._cancel_btn = PushButton(FIF.CLOSE, t("common.cancel"), self)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._cancel_btn.hide()
        action_bar.addWidget(self._cancel_btn, 0, av)

        self._progress = ProgressRing(self)
        self._progress.setFixedSize(20, 20)
        self._progress.hide()
        action_bar.addWidget(self._progress, 0, av)
        action_bar.addSpacing(theme.GAP_XS)

        self._status_msg = BodyLabel("", self)
        self._status_msg.setStyleSheet(f"color:{theme.text_muted()};")
        action_bar.addWidget(self._status_msg, 0, av)
        action_bar.addStretch()
        layout.addLayout(action_bar)

        self._progress_panel = QWidget(self)
        progress_lay = QVBoxLayout(self._progress_panel)
        progress_lay.setContentsMargins(0, 0, 0, 0)
        progress_lay.setSpacing(theme.GAP_XS)
        self._progress_detail = CaptionLabel("", self._progress_panel)
        self._progress_detail.setStyleSheet(f"color:{theme.text_muted()};")
        progress_lay.addWidget(self._progress_detail)
        self._progress_bar = ProgressBar(self._progress_panel)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        progress_lay.addWidget(self._progress_bar)
        self._progress_panel.hide()
        layout.addWidget(self._progress_panel)

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

    def _info_row(self, parent_layout, label_text: str) -> BodyLabel:  # noqa: ANN001
        row = QHBoxLayout()
        row.setSpacing(theme.GAP_SM)
        lbl = CaptionLabel(label_text, self._status_card)
        lbl.setStyleSheet(f"color:{theme.text_muted()};")
        lbl.setFixedWidth(64)
        row.addWidget(lbl, 0, Qt.AlignmentFlag.AlignTop)
        value = BodyLabel("—", self._status_card)
        value.setWordWrap(True)
        row.addWidget(value, 1)
        parent_layout.addLayout(row)
        return value

    def _set_status_badge(self, ok: bool) -> None:
        text = t("sync.configured") if ok else t("sync.unconfigured")
        color = theme.status_fill("added") if ok else "#9a9a9a"
        self._status_badge.setText(text)
        w = max(48, self._status_badge.fontMetrics().horizontalAdvance(text) + 18)
        self._status_badge.setFixedWidth(w)
        self._status_badge.setStyleSheet(
            f"background:{color}; color:{theme.on_accent()}; "
            f"border-radius:{theme.RADIUS_PILL}px; padding:0 6px; font-weight:500;"
        )

    def _refresh_status(self) -> None:
        if self._config is None:
            return
        if self._config.sync_backend == "webdav":
            target = self._config.webdav_url
            configured = bool(target)
            method = t("settings.sync_method_webdav")
        else:
            sf = self._config.sync_folder
            configured = bool(sf) and str(sf) not in ("", ".") and sf.exists()
            method = t("settings.sync_method_folder")
            target = str(sf) if configured else ""

        self._method_value.setText(method)
        self._target_value.setText(target or "—")
        self._target_value.setToolTip(target or "")
        self._machine_value.setText(self._config.machine_id)
        self._set_status_badge(configured)

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
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.show()
        self._progress.show()
        self._progress_panel.show()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setValue(0)
        self._progress_detail.setText("")
        self._status_msg.setText(t("sync.syncing"))

        self._worker = _SyncWorker(self)
        self._worker.set_data(self._sync_manager, mode)
        self._worker.progress.connect(self._on_sync_progress)
        self._worker.finished.connect(self._on_sync_finished)
        self._worker.error.connect(self._on_sync_error)
        self._worker.start()

    def _on_cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._cancel_btn.setEnabled(False)
            self._status_msg.setText(t("common.canceling"))

    def _on_sync_finished(self, result: SyncResult) -> None:
        cancelled = self._worker is not None and self._worker.isInterruptionRequested()
        self._sync_btn.setEnabled(True)
        self._push_btn.setEnabled(True)
        self._pull_btn.setEnabled(True)
        self._cancel_btn.hide()
        self._progress.hide()
        self._progress_panel.hide()

        if cancelled:
            self._status_msg.setText(t("common.cancelled"))
            InfoBar.warning(
                title=t("common.cancelled"),
                content=t("sync.sync_success", push=str(result.pushed),
                          pull=str(result.pulled)),
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )
            return

        msg = t("sync.sync_success", push=str(result.pushed), pull=str(result.pulled))
        self._status_msg.setText(msg)

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

        if result.conflicts:
            msg += f"\n{t('sync.conflict_found', count=str(len(result.conflicts)))}"
            self._status_msg.setText(msg)
            self._handle_conflicts(result.conflicts)
        else:
            InfoBar.success(
                title=t("sync.sync_complete"),
                content=msg,
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )

    def _on_sync_progress(self, progress: SyncProgress) -> None:
        operation_label = {
            "push": t("sync.progress_push"),
            "pull": t("sync.progress_pull"),
            "check": t("sync.progress_check"),
        }.get(progress.operation, t("sync.syncing"))

        target = f"{operation_label} {progress.emulator}:{_short_text(progress.game_id)}"
        self._status_msg.setText(target)

        current = max(0, progress.current)
        total = max(0, progress.total)
        if total > 0:
            pct = min(100, int(current / total * 100))
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(pct)
            size_text = f"{_format_size(current)} / {_format_size(total)}"
        else:
            self._progress_bar.setRange(0, 0)
            size_text = _format_size(current)

        self._progress_detail.setText(
            f"{_short_text(progress.file_name, 64)} · {size_text}"
        )

    # ------------------------------------------------------------------
    # Conflict resolution
    # ------------------------------------------------------------------

    def _handle_conflicts(self, conflicts: list[ConflictInfo]) -> None:
        """Prompt the user to resolve sync conflicts, then apply the choices."""
        if len(conflicts) == 1:
            dlg = ConflictDialog(conflicts[0], self)
            if not dlg.exec():
                return
            pairs = [(conflicts[0], dlg.resolution)]
        else:
            dlg = BatchConflictDialog(conflicts, self)
            if not dlg.exec():
                return
            res_map = dlg.resolutions
            pairs = [
                (c, res_map.get(c.game_id, ConflictResolution.SKIP))
                for c in conflicts
            ]

        pairs = [(c, r) for c, r in pairs if r != ConflictResolution.SKIP]
        if not pairs:
            return

        self._sync_btn.setEnabled(False)
        self._push_btn.setEnabled(False)
        self._pull_btn.setEnabled(False)
        self._progress.show()
        self._progress_panel.hide()
        self._status_msg.setText(t("conflict.resolving"))

        self._resolve_worker = _ResolveWorker(self)
        self._resolve_worker.set_data(self._sync_manager, pairs)
        self._resolve_worker.finished.connect(self._on_resolve_finished)
        self._resolve_worker.error.connect(self._on_sync_error)
        self._resolve_worker.start()

    def _on_resolve_finished(self, errors: list) -> None:
        self._sync_btn.setEnabled(True)
        self._push_btn.setEnabled(True)
        self._pull_btn.setEnabled(True)
        self._progress.hide()
        self._progress_panel.hide()

        if errors:
            self._log_label.show()
            self._log_body.setText("\n".join(errors))
            InfoBar.warning(
                title=t("conflict.title"),
                content="\n".join(errors),
                parent=self, position=InfoBarPosition.TOP, duration=5000,
            )
        else:
            self._status_msg.setText(t("conflict.resolved"))
            InfoBar.success(
                title=t("conflict.title"),
                content=t("conflict.resolved"),
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )

    def _on_sync_error(self, error: str) -> None:
        self._sync_btn.setEnabled(True)
        self._push_btn.setEnabled(True)
        self._pull_btn.setEnabled(True)
        self._cancel_btn.hide()
        self._progress.hide()
        self._progress_panel.hide()
        self._status_msg.setText("")
        InfoBar.error(
            title=t("common.error"),
            content=error,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )
