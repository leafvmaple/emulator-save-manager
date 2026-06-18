"""Restore page — card-based UI for browsing and restoring backups.

Each game is shown as a card; expanding it reveals individual backup
versions as sub-cards with timestamp, version number, pin/label info,
and a one-click restore button.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
)
from qfluentwidgets import (
    SubtitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    PrimaryPushButton, PushButton, TransparentToolButton,
    CardWidget, SimpleCardWidget, SmoothScrollArea, CheckBox,
    FluentIcon as FIF, InfoBar, InfoBarPosition, InfoBadge,
    MessageBox, MessageBoxBase, LineEdit, ProgressRing, IconWidget,
    setFont,
)
from loguru import logger

from app.i18n import t
from app.models.backup_record import BackupRecord
from app.models.game_save import SaveType
from app.core.game_icon import GameIconProvider, get_plugin_icon
from app.ui import theme
from app.ui.components.badge import TypeBadge
from app.ui.components.page_header import PageHeader
from app.ui.components.empty_state import EmptyState


class _RestoreWorker(QThread):
    """Background thread for restoring a backup without freezing the UI."""

    finished = Signal(list)  # list[str] errors
    error = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = None
        self._record: BackupRecord | None = None
        self._indices: set[int] | None = None

    def set_data(self, manager, record: BackupRecord,  # noqa: ANN001
                 indices: set[int] | None = None) -> None:
        self._manager = manager
        self._record = record
        self._indices = indices

    def run(self) -> None:
        try:
            errors = self._manager.restore_backup(
                self._record, force=True, indices=self._indices,
            )
            self.finished.emit(errors)
        except Exception as e:
            self.error.emit(str(e))


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _zip_size(record: BackupRecord) -> int:
    """Return the size of the backup zip file, or 0."""
    try:
        return record.backup_path.stat().st_size if record.backup_path.exists() else 0
    except Exception:
        return 0


def _backup_types(record: BackupRecord) -> list[str]:
    """Read raw save type keys from the sidecar json metadata."""
    meta = record.backup_path.with_suffix(".json")
    if not meta.exists():
        return []
    try:
        with open(meta, "r", encoding="utf-8") as f:
            data = json.load(f)
        return sorted({bp.get("type", "") for bp in data.get("backup_paths", [])} - {""})
    except Exception:
        return []




# -----------------------------------------------------------------------
# Label editor dialog
# -----------------------------------------------------------------------

class _LabelDialog(MessageBoxBase):
    """Small dialog to set/edit a backup's label."""

    def __init__(self, current: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(t("restore.label_title"), self)
        self.edit = LineEdit(self)
        self.edit.setText(current)
        self.edit.setPlaceholderText(t("restore.label_placeholder"))
        self.edit.setClearButtonEnabled(True)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.edit)
        self.yesButton.setText(t("common.confirm"))
        self.cancelButton.setText(t("common.cancel"))
        self.widget.setMinimumWidth(360)

    @property
    def label_text(self) -> str:
        return self.edit.text().strip()


class _RestoreSelectDialog(MessageBoxBase):
    """Pick which items of a multi-save backup to restore."""

    def __init__(self, items, parent: QWidget | None = None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._checks: list[tuple[int, CheckBox]] = []

        self.titleLabel = SubtitleLabel(t("restore.select_title"), self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(BodyLabel(t("restore.select_desc"), self))

        inner = QWidget(self)
        box = QVBoxLayout(inner)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        for it in items:
            cb = CheckBox(self._item_label(it), inner)
            cb.setChecked(True)
            self._checks.append((it.index, cb))
            box.addWidget(cb)
        box.addStretch()

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setMaximumHeight(280)
        self.viewLayout.addWidget(scroll)

        self.yesButton.setText(t("restore.restore_selected"))
        self.cancelButton.setText(t("common.cancel"))
        self.widget.setMinimumWidth(440)

    @staticmethod
    def _item_label(item) -> str:  # noqa: ANN001
        type_name = t(f"save_type.{item.save_type}") if item.save_type else ""
        label = f"{item.name}  [{type_name}]" if type_name else item.name
        if item.is_newer_locally:
            label += f"   ⚠ {t('restore.newer_local')}"
        return label

    @property
    def selected_indices(self) -> set[int]:
        return {idx for idx, cb in self._checks if cb.isChecked()}


class _DiffDialog(MessageBoxBase):
    """Read-only view of what changed between two backups."""

    def __init__(self, diff, parent: QWidget | None = None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(t("compare.title"), self)
        self.viewLayout.addWidget(self.titleLabel)

        sub = CaptionLabel(
            f"v{diff.old.version} {diff.old.display_time}  →  "
            f"v{diff.new.version} {diff.new.display_time}",
            self,
        )
        sub.setStyleSheet(f"color:{theme.text_muted()};")
        self.viewLayout.addWidget(sub)

        if not diff.has_changes:
            self.viewLayout.addWidget(BodyLabel(t("compare.no_changes"), self))
        else:
            self.viewLayout.addWidget(BodyLabel(
                t("compare.summary",
                  added=str(len(diff.added)),
                  modified=str(len(diff.modified)),
                  removed=str(len(diff.removed))),
                self,
            ))
            self.viewLayout.addWidget(self._build_file_list(diff))

        self.yesButton.setText(t("common.close"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(480)

    def _build_file_list(self, diff) -> QWidget:  # noqa: ANN001
        inner = QWidget(self)
        box = QVBoxLayout(inner)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        for f in diff.changed:
            row = QHBoxLayout()
            row.setSpacing(8)

            badge = QLabel(t(f"compare.{f.status}"))
            badge.setFixedHeight(18)
            setFont(badge, 10)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            color = theme.status_fill(f.status)
            badge.setStyleSheet(
                f"background:{color}; color:{theme.on_accent()}; "
                f"border-radius:{theme.RADIUS_PILL}px; padding:0 8px;"
            )
            row.addWidget(badge)

            name = CaptionLabel(f.name)
            name.setStyleSheet(f"color:{theme.text_primary()};")
            row.addWidget(name)
            row.addStretch()
            row.addWidget(CaptionLabel(self._size_text(f)))

            holder = QWidget()
            holder.setLayout(row)
            box.addWidget(holder)
        box.addStretch()

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setMaximumHeight(300)
        return scroll

    @staticmethod
    def _size_text(f) -> str:  # noqa: ANN001
        def fmt(n):
            return "-" if n is None else _format_size(n)
        if f.status == "modified":
            return f"{fmt(f.size_old)} → {fmt(f.size_new)}"
        if f.status == "added":
            return fmt(f.size_new)
        return fmt(f.size_old)


# -----------------------------------------------------------------------
# Version sub-card
# -----------------------------------------------------------------------

class _VersionCard(SimpleCardWidget):
    """A compact card representing a single backup version."""

    restore_clicked = Signal(object)  # emits BackupRecord
    pin_clicked = Signal(object)      # emits BackupRecord
    label_clicked = Signal(object)    # emits BackupRecord
    delete_clicked = Signal(object)   # emits BackupRecord
    compare_clicked = Signal(object, object)  # emits (new_record, old_record)

    def __init__(
        self,
        record: BackupRecord,
        prev_record: BackupRecord | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.record = record
        self._prev_record = prev_record
        self.setFixedHeight(56)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 8, 16, 8)
        root.setSpacing(12)

        # Version badge
        ver_label = QLabel(f"v{record.version}", self)
        ver_label.setFixedSize(36, 24)
        ver_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver_label.setStyleSheet(
            f"background:{theme.accent()}; color:{theme.on_accent()}; "
            f"border-radius:{theme.RADIUS_SM}px; font-weight:600; font-size:12px;"
        )
        root.addWidget(ver_label)

        # Info
        info = QVBoxLayout()
        info.setSpacing(0)
        info.setContentsMargins(0, 0, 0, 0)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        time_label = BodyLabel(record.display_time, self)
        setFont(time_label, 13)
        top_row.addWidget(time_label)

        if record.is_pinned:
            pin = CaptionLabel(f"[{t('backup.pin')}]", self)
            pin.setStyleSheet(f"color:{theme.status_text('pinned')}; font-weight:600;")
            top_row.addWidget(pin)

        if record.label:
            lbl = CaptionLabel(record.label, self)
            lbl.setStyleSheet(f"color:{theme.text_secondary()}; font-style:italic;")
            top_row.addWidget(lbl)

        top_row.addStretch()
        info.addLayout(top_row)

        # Size
        size = _zip_size(record)
        if size:
            size_label = CaptionLabel(_format_size(size), self)
            info.addWidget(size_label)

        root.addLayout(info, 1)

        # Restore button
        restore_btn = PushButton(FIF.HISTORY, t("restore.restore_selected"), self)
        restore_btn.setFixedWidth(100)
        setFont(restore_btn, 12)
        restore_btn.clicked.connect(lambda: self.restore_clicked.emit(self.record))
        root.addWidget(restore_btn)

        # Compare with the previous (older) version, if there is one.
        if prev_record is not None:
            cmp_btn = TransparentToolButton(FIF.SEARCH_MIRROR, self)
            cmp_btn.setFixedSize(28, 28)
            cmp_btn.setToolTip(t("compare.tooltip"))
            cmp_btn.clicked.connect(
                lambda: self.compare_clicked.emit(self.record, self._prev_record)
            )
            root.addWidget(cmp_btn)

        # Management actions: pin / label / delete
        pin_btn = TransparentToolButton(
            FIF.UNPIN if record.is_pinned else FIF.PIN, self
        )
        pin_btn.setFixedSize(28, 28)
        pin_btn.setToolTip(t("backup.unpin") if record.is_pinned else t("backup.pin"))
        pin_btn.clicked.connect(lambda: self.pin_clicked.emit(self.record))
        root.addWidget(pin_btn)

        label_btn = TransparentToolButton(FIF.LABEL, self)
        label_btn.setFixedSize(28, 28)
        label_btn.setToolTip(t("backup.label"))
        label_btn.clicked.connect(lambda: self.label_clicked.emit(self.record))
        root.addWidget(label_btn)

        delete_btn = TransparentToolButton(FIF.DELETE, self)
        delete_btn.setFixedSize(28, 28)
        delete_btn.setToolTip(t("common.delete"))
        delete_btn.clicked.connect(lambda: self.delete_clicked.emit(self.record))
        root.addWidget(delete_btn)



# -----------------------------------------------------------------------
# Game card
# -----------------------------------------------------------------------

class _GameBackupCard(CardWidget):
    """Expandable card for a game showing its backup history."""

    restore_requested = Signal(object)  # emits BackupRecord
    pin_requested = Signal(object)
    label_requested = Signal(object)
    delete_requested = Signal(object)
    compare_requested = Signal(object, object)  # emits (new_record, old_record)
    ICON_WIDTH = 42
    ICON_MAX_HEIGHT = 58

    def __init__(
        self,
        game_name: str,
        emulator: str,
        game_id: str,
        records: list[BackupRecord],
        icon_provider: GameIconProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._records = records
        self._expanded = False
        self._version_cards: list[_VersionCard] = []

        main = QVBoxLayout(self)
        main.setContentsMargins(20, 12, 20, 12)
        main.setSpacing(0)

        # --- Header ---
        header = QWidget(self)
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        # Icon — cover art or fallback
        self._icon_label = QLabel(header)
        self._icon_label.setFixedWidth(self.ICON_WIDTH)
        self._icon_label.setMaximumHeight(self.ICON_MAX_HEIGHT)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pm = icon_provider.get_pixmap(emulator, game_id, self.ICON_WIDTH, self.ICON_MAX_HEIGHT) if icon_provider else None
        if pm and not pm.isNull():
            self._icon_label.setPixmap(pm)
        else:
            emu_pm = get_plugin_icon(emulator, self.ICON_WIDTH)
            if emu_pm and not emu_pm.isNull():
                self._icon_label.setPixmap(emu_pm)
            else:
                fallback = IconWidget(FIF.GAME, header)
                fallback.setFixedSize(36, 36)
                fl = QVBoxLayout(self._icon_label)
                fl.setContentsMargins(0, 0, 0, 0)
                fl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                fl.addWidget(fallback)
        header_layout.addWidget(self._icon_label)

        # Title + meta
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.setContentsMargins(0, 0, 0, 0)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_label = StrongBodyLabel(game_name, header)
        setFont(title_label, 14, QFont.Weight.DemiBold)
        title_row.addWidget(title_label)

        emu_badge = CaptionLabel(emulator, header)
        emu_badge.setStyleSheet(
            f"background:{theme.subtle_fill()}; color:{theme.subtle_fill_text()}; "
            f"border-radius:{theme.RADIUS_SM}px; padding:1px 6px;"
        )
        title_row.addWidget(emu_badge)
        title_row.addStretch()
        title_col.addLayout(title_row)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(theme.GAP_MD)
        muted = f"color:{theme.text_muted()};"

        def _meta(text: str) -> CaptionLabel:
            lbl = CaptionLabel(text, header)
            lbl.setStyleSheet(muted)
            return lbl

        meta_row.addWidget(_meta(f"ID: {game_id}"))
        meta_row.addWidget(_meta(f"{len(records)} {t('backup.backup_count').lower()}"))

        # Type badges from latest backup
        types = _backup_types(records[0]) if records else []
        for tp_key in types:
            meta_row.addWidget(TypeBadge.for_save_type(tp_key, header))

        meta_row.addStretch()
        meta_row.addWidget(_meta(
            f"{t('backup.last_backup')}: {records[0].display_time}" if records else ""
        ))
        title_col.addLayout(meta_row)

        header_layout.addLayout(title_col, 1)

        # Expand indicator
        self._chevron = TransparentToolButton(FIF.CHEVRON_RIGHT, header)
        self._chevron.setFixedSize(28, 28)
        self._chevron.setCursor(Qt.CursorShape.PointingHandCursor)
        self._chevron.clicked.connect(self._toggle)
        header_layout.addWidget(self._chevron)

        # Open backup folder
        open_btn = TransparentToolButton(FIF.FOLDER, header)
        open_btn.setFixedSize(32, 32)
        open_btn.setToolTip(t("common.open_folder"))
        self._backup_folder = records[0].backup_path.parent if records else None
        open_btn.clicked.connect(self._open_folder)
        header_layout.addWidget(open_btn)

        main.addWidget(header)

        # --- Expandable body ---
        self._body = QWidget(self)
        self._body.hide()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 4, 0, 4)
        body_layout.setSpacing(6)

        for idx, r in enumerate(records):
            # records are newest-first, so the next entry is the older version.
            prev = records[idx + 1] if idx + 1 < len(records) else None
            vc = _VersionCard(r, prev_record=prev, parent=self._body)
            vc.restore_clicked.connect(self.restore_requested.emit)
            vc.pin_clicked.connect(self.pin_requested.emit)
            vc.label_clicked.connect(self.label_requested.emit)
            vc.delete_clicked.connect(self.delete_requested.emit)
            vc.compare_clicked.connect(self.compare_requested.emit)
            body_layout.addWidget(vc)
            self._version_cards.append(vc)

        main.addWidget(self._body)

        # Click the header to expand/collapse
        header.mousePressEvent = lambda e: self._toggle()

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._chevron.setIcon(
            FIF.CHEVRON_DOWN_MED if self._expanded else FIF.CHEVRON_RIGHT
        )

    def _open_folder(self) -> None:
        """Open the folder containing this game's backups."""
        if not self._backup_folder or not self._backup_folder.exists():
            return
        if sys.platform == "win32":
            os.startfile(str(self._backup_folder))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(self._backup_folder)])  # noqa: S603
        else:
            subprocess.Popen(["xdg-open", str(self._backup_folder)])  # noqa: S603


# -----------------------------------------------------------------------
# Page
# -----------------------------------------------------------------------

class RestorePage(QWidget):
    """Page for browsing and restoring backups with card-based UI."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("restore_page")
        self._backup_manager = None
        self._restore_manager = None
        self._all_backups: dict[str, list[BackupRecord]] = {}
        self._cards: list[_GameBackupCard] = []
        self._icon_provider: GameIconProvider | None = None
        self._restore_worker: _RestoreWorker | None = None
        self._init_ui()

    def set_managers(self, backup_manager, restore_manager) -> None:  # noqa: ANN001
        self._backup_manager = backup_manager
        self._restore_manager = restore_manager

    def set_icon_provider(self, provider: GameIconProvider) -> None:
        self._icon_provider = provider

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(theme.PAGE_MARGIN_H, theme.PAGE_MARGIN_V,
                                       theme.PAGE_MARGIN_H, theme.PAGE_MARGIN_V)
        page_layout.setSpacing(theme.GAP_MD)

        page_layout.addWidget(PageHeader(t("restore.title"), t("restore.description"), self))

        # Action bar
        av = Qt.AlignmentFlag.AlignVCenter
        action_bar = QHBoxLayout()
        action_bar.setSpacing(theme.GAP_MD)

        refresh_btn = PushButton(FIF.SYNC, t("common.refresh"), self)
        refresh_btn.clicked.connect(self._refresh_backups)
        action_bar.addWidget(refresh_btn, 0, av)

        self._count_badge = InfoBadge.attension("0", parent=self)
        self._count_badge.setFixedHeight(20)
        action_bar.addWidget(self._count_badge, 0, av)

        action_bar.addStretch()

        self._progress = ProgressRing(self)
        self._progress.setFixedSize(20, 20)
        self._progress.hide()
        action_bar.addWidget(self._progress, 0, av)

        self._status_label = CaptionLabel("", self)
        self._status_label.setStyleSheet(f"color:{theme.text_muted()};")
        action_bar.addWidget(self._status_label, 0, av)

        page_layout.addLayout(action_bar)

        # Scrollable card area
        self._scroll = SmoothScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._scroll_inner = QWidget()
        self._card_layout = QVBoxLayout(self._scroll_inner)
        self._card_layout.setContentsMargins(8, 8, 8, 8)
        self._card_layout.setSpacing(10)
        self._card_layout.addStretch()
        self._scroll.setWidget(self._scroll_inner)
        page_layout.addWidget(self._scroll, stretch=1)

        # Empty state — shown until backups exist
        self._empty = EmptyState(
            FIF.HISTORY, t("empty.restore_title"), t("empty.restore_desc"), self
        )
        page_layout.addWidget(self._empty, stretch=1)
        self._scroll.hide()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh_backups(self) -> None:
        if self._backup_manager is None:
            return
        self._all_backups = self._backup_manager.list_all_backups()
        self._refresh_cards()

    def _refresh_cards(self) -> None:
        for card in self._cards:
            self._card_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        if not self._all_backups:
            self._scroll.hide()
            self._empty.show()
            self._count_badge.setText("0")
            return

        self._empty.hide()
        self._scroll.show()

        total_backups = 0
        for key in sorted(self._all_backups):
            records = self._all_backups[key]
            if not records:
                continue
            total_backups += len(records)
            latest = records[0]
            card = _GameBackupCard(
                game_name=latest.game_save.game_name,
                emulator=latest.game_save.emulator,
                game_id=latest.game_save.game_id,
                records=records,
                icon_provider=self._icon_provider,
                parent=self._scroll_inner,
            )
            card.restore_requested.connect(self._on_restore)
            card.pin_requested.connect(self._on_pin)
            card.label_requested.connect(self._on_label)
            card.delete_requested.connect(self._on_delete)
            card.compare_requested.connect(self._on_compare)
            self._card_layout.insertWidget(self._card_layout.count() - 1, card)
            self._cards.append(card)

        self._count_badge.setText(str(total_backups))

    # ------------------------------------------------------------------
    # Restore action
    # ------------------------------------------------------------------

    def _on_restore(self, record: BackupRecord) -> None:
        if self._restore_manager is None:
            return
        if self._restore_worker is not None and self._restore_worker.isRunning():
            return  # a restore is already in progress

        # Selective restore: if the backup holds more than one save, let the
        # user pick which items to restore (single-item backups restore directly).
        indices: set[int] | None = None
        items = self._restore_manager.list_backup_items(record)
        if len(items) > 1:
            dlg = _RestoreSelectDialog(items, self)
            if not dlg.exec():
                return
            indices = dlg.selected_indices
            if not indices:
                return  # nothing selected

        # Preview and confirm (only the selected items)
        changes = self._restore_manager.preview_restore(record, indices=indices)
        has_newer = any(c.is_newer_locally for c in changes)

        if has_newer:
            box = MessageBox(
                t("restore.confirm_restore"),
                t("restore.overwrite_warning"),
                self,
            )
            if not box.exec():
                return

        self._set_cards_enabled(False)
        self._progress.show()
        self._status_label.setText(t("restore.restoring"))

        self._restore_worker = _RestoreWorker(self)
        self._restore_worker.set_data(self._restore_manager, record, indices)
        self._restore_worker.finished.connect(self._on_restore_finished)
        self._restore_worker.error.connect(self._on_restore_error)
        self._restore_worker.start()

    def _on_restore_finished(self, errors: list) -> None:
        self._progress.hide()
        self._status_label.setText("")
        self._set_cards_enabled(True)

        if errors:
            InfoBar.warning(
                title=t("restore.restore_complete"),
                content="\n".join(errors),
                parent=self, position=InfoBarPosition.TOP, duration=5000,
            )
        else:
            InfoBar.success(
                title=t("restore.restore_complete"),
                content=t("restore.restore_success", count="1"),
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )

    def _on_restore_error(self, error: str) -> None:
        self._progress.hide()
        self._status_label.setText("")
        self._set_cards_enabled(True)
        InfoBar.error(
            title=t("common.error"), content=error,
            parent=self, position=InfoBarPosition.TOP, duration=5000,
        )

    def _set_cards_enabled(self, enabled: bool) -> None:
        """Enable/disable all game cards while a restore runs."""
        for card in self._cards:
            card.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Backup management
    # ------------------------------------------------------------------

    def _on_pin(self, record: BackupRecord) -> None:
        if self._backup_manager is None:
            return
        if record.is_pinned:
            self._backup_manager.unpin_backup(record)
        else:
            self._backup_manager.pin_backup(record, label=record.label)
        self._refresh_backups()

    def _on_label(self, record: BackupRecord) -> None:
        if self._backup_manager is None:
            return
        dlg = _LabelDialog(record.label, self)
        if not dlg.exec():
            return
        self._backup_manager.set_label(record, dlg.label_text)
        self._refresh_backups()

    def _on_delete(self, record: BackupRecord) -> None:
        if self._backup_manager is None:
            return
        box = MessageBox(
            t("common.delete"),
            t("restore.delete_confirm"),
            self,
        )
        if not box.exec():
            return
        self._backup_manager.delete_backup(record)
        InfoBar.success(
            title=t("restore.restore_complete"),
            content=t("restore.deleted"),
            parent=self, position=InfoBarPosition.TOP, duration=2500,
        )
        self._refresh_backups()

    def _on_compare(self, new_record: BackupRecord, old_record: BackupRecord) -> None:
        from app.core.backup_diff import diff_backups
        diff = diff_backups(old_record, new_record)
        _DiffDialog(diff, self).exec()
