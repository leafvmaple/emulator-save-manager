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
    CardWidget, SimpleCardWidget, SmoothScrollArea,
    FluentIcon as FIF, InfoBar, InfoBarPosition, InfoBadge,
    MessageBox, ProgressRing, IconWidget,
    setFont,
)
from loguru import logger

from app.i18n import t
from app.models.backup_record import BackupRecord
from app.models.game_save import SaveType
from app.core.game_icon import GameIconProvider, get_plugin_icon


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


_SAVE_TYPE_COLORS: dict[str, str] = {
    "savestate": "#0078d4",
    "memcard": "#107c10",
    "folder": "#107c10",
    "battery": "#ff8c00",
    "file": "#5c2d91",
}


class _TypeBadge(QLabel):
    """Coloured pill badge for a save type."""

    def __init__(self, text: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFixedHeight(18)
        setFont(self, 10)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pad = max(24, self.fontMetrics().horizontalAdvance(text) + 14)
        self.setFixedWidth(pad)
        self.setStyleSheet(
            f"background:{color}; color:white; border-radius:9px; "
            f"padding:0 5px; font-weight:500;"
        )


# -----------------------------------------------------------------------
# Version sub-card
# -----------------------------------------------------------------------

class _VersionCard(SimpleCardWidget):
    """A compact card representing a single backup version."""

    restore_clicked = Signal(object)  # emits BackupRecord

    def __init__(self, record: BackupRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.record = record
        self.setFixedHeight(56)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 8, 16, 8)
        root.setSpacing(12)

        # Version badge
        ver_label = QLabel(f"v{record.version}", self)
        ver_label.setFixedSize(36, 24)
        ver_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver_label.setStyleSheet(
            "background:#0078d4; color:white; border-radius:4px; font-weight:600; font-size:12px;"
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
            pin.setStyleSheet("color:#d83b01; font-weight:600;")
            top_row.addWidget(pin)

        if record.label:
            lbl = CaptionLabel(record.label, self)
            lbl.setStyleSheet("color:#666; font-style:italic;")
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



# -----------------------------------------------------------------------
# Game card
# -----------------------------------------------------------------------

class _GameBackupCard(CardWidget):
    """Expandable card for a game showing its backup history."""

    restore_requested = Signal(object)  # emits BackupRecord
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
            "background:#e0e0e0; color:#444; border-radius:3px; padding:1px 6px;"
        )
        title_row.addWidget(emu_badge)
        title_row.addStretch()
        title_col.addLayout(title_row)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(12)
        meta_row.addWidget(CaptionLabel(f"ID: {game_id}", header))
        meta_row.addWidget(CaptionLabel(
            f"{len(records)} {t('backup.backup_count').lower()}", header
        ))

        # Type badges from latest backup
        types = _backup_types(records[0]) if records else []
        for tp_key in types:
            color = _SAVE_TYPE_COLORS.get(tp_key, "#888")
            meta_row.addWidget(_TypeBadge(t(f"save_type.{tp_key}"), color, header))

        meta_row.addStretch()
        meta_row.addWidget(CaptionLabel(
            f"{t('backup.last_backup')}: {records[0].display_time}" if records else "", header
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

        for r in records:
            vc = _VersionCard(r, self._body)
            vc.restore_clicked.connect(self.restore_requested.emit)
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
        page_layout.setContentsMargins(36, 20, 36, 20)
        page_layout.setSpacing(12)

        title = SubtitleLabel(t("restore.title"), self)
        desc = BodyLabel(t("restore.description"), self)
        desc.setWordWrap(True)
        page_layout.addWidget(title)
        page_layout.addWidget(desc)

        # Action bar
        action_bar = QHBoxLayout()
        action_bar.setSpacing(12)

        refresh_btn = PushButton(FIF.SYNC, t("common.refresh"), self)
        refresh_btn.clicked.connect(self._refresh_backups)
        action_bar.addWidget(refresh_btn)

        self._count_badge = InfoBadge.attension("0", parent=self)
        self._count_badge.setFixedHeight(20)
        action_bar.addWidget(self._count_badge)

        action_bar.addStretch()

        self._progress = ProgressRing(self)
        self._progress.setFixedSize(24, 24)
        self._progress.hide()
        action_bar.addWidget(self._progress)

        self._status_label = CaptionLabel("", self)
        action_bar.addWidget(self._status_label)

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

        # Empty state
        self._empty_label = BodyLabel(t("backup.no_backups"), self)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #888;")
        page_layout.addWidget(self._empty_label)
        self._empty_label.hide()

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
            self._empty_label.show()
            self._count_badge.setText("0")
            return

        self._empty_label.hide()
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
            self._card_layout.insertWidget(self._card_layout.count() - 1, card)
            self._cards.append(card)

        self._count_badge.setText(str(total_backups))

    # ------------------------------------------------------------------
    # Restore action
    # ------------------------------------------------------------------

    def _on_restore(self, record: BackupRecord) -> None:
        if self._restore_manager is None:
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
        self._status_label.setText(t("restore.restoring"))
        errors = self._restore_manager.restore_backup(record, force=True)
        self._progress.hide()
        self._status_label.setText("")

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
