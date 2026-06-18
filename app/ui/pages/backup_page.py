"""Backup page — card-based UI for backing up game saves.

Each game is shown as a styled card with checkbox, game info, save-type
badges, file details and action area.  Cards live inside a scrollable
layout so the page handles many games gracefully.
"""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import Qt, Signal, QThread, QSize
from PySide6.QtGui import QFont, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
    QGraphicsDropShadowEffect,
)
from qfluentwidgets import (
    CaptionLabel, StrongBodyLabel,
    PrimaryPushButton, PushButton, TransparentToolButton,
    CardWidget, SmoothScrollArea, FluentIcon as FIF,
    InfoBar, InfoBarPosition, InfoBadge,
    CheckBox, ProgressRing, ToolTipFilter, ToolTipPosition,
    setFont, FlowLayout, IconWidget,
)
from loguru import logger

from app.i18n import t
from app.models.game_save import GameSave, SaveType
from app.core.game_icon import GameIconProvider, get_plugin_icon
from app.ui import theme
from app.ui.components.badge import TypeBadge
from app.ui.components.page_header import PageHeader
from app.ui.components.empty_state import EmptyState
from app.ui.components.avatar import letter_avatar
from app.ui.components.elevation import add_hover_elevation


# -----------------------------------------------------------------------
# Worker
# -----------------------------------------------------------------------

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
        groups: dict[str, list[GameSave]] = {}
        for save in self._saves:
            key = f"{save.emulator}:{save.game_id}"
            groups.setdefault(key, []).append(save)
        total = len(groups)
        for i, (key, saves) in enumerate(groups.items(), start=1):
            if self.isInterruptionRequested():
                break
            try:
                self._backup_manager.create_backup(saves)
                success += 1
            except Exception as e:
                errors.append(f"{saves[0].game_name}: {e}")
            self.progress.emit(i, total)
        self.finished.emit(success, errors)


class _AutoBackupWorker(QThread):
    """Background thread that backs up only the games whose saves changed."""

    finished = Signal(object)  # AutoBackupResult
    error = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backup_manager = None
        self._saves: list[GameSave] = []

    def set_data(self, backup_manager, saves: list[GameSave]) -> None:  # noqa: ANN001
        self._backup_manager = backup_manager
        self._saves = saves

    def run(self) -> None:
        try:
            self.finished.emit(self._backup_manager.auto_backup_all(self._saves))
        except Exception as e:
            self.error.emit(str(e))


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _game_group_key(save: GameSave) -> str:
    return f"{save.emulator}:{save.game_id}"


# -----------------------------------------------------------------------
# Game Card
# -----------------------------------------------------------------------

class _GameCard(CardWidget):
    """Visual card representing one game and all its saves."""

    checked_changed = Signal()
    ICON_WIDTH = 42
    ICON_MAX_HEIGHT = 58

    def __init__(
        self,
        saves: list[GameSave],
        icon_provider: GameIconProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.saves = saves
        self._selected = False
        self.setFixedHeight(92)
        add_hover_elevation(self)

        ref = saves[0]
        display_name = ref.game_name
        for s in saves:
            if s.game_name != s.game_id:
                display_name = s.game_name
                break

        root = QHBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 12)
        root.setSpacing(theme.GAP_LG)

        # --- Checkbox ---
        self.cb = CheckBox(self)
        self.cb.stateChanged.connect(self._on_check)
        root.addWidget(self.cb, 0, Qt.AlignmentFlag.AlignVCenter)

        # --- Icon (cover art or fallback) ---
        self._icon_label = QLabel(self)
        self._icon_label.setFixedWidth(self.ICON_WIDTH)
        self._icon_label.setMaximumHeight(self.ICON_MAX_HEIGHT)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pm = icon_provider.get_pixmap(ref.emulator, ref.game_id, self.ICON_WIDTH, self.ICON_MAX_HEIGHT) if icon_provider else None
        if pm and not pm.isNull():
            self._icon_label.setPixmap(pm)
        else:
            self._icon_label.setPixmap(letter_avatar(display_name, self.ICON_WIDTH))
        root.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # --- Info column ---
        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        info_col.setContentsMargins(0, 0, 0, 0)

        # Title row
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_label = StrongBodyLabel(display_name, self)
        setFont(title_label, 14, QFont.Weight.DemiBold)
        title_row.addWidget(title_label)

        emu_badge = CaptionLabel(ref.emulator, self)
        emu_badge.setStyleSheet(
            f"background:{theme.subtle_fill()}; color:{theme.subtle_fill_text()}; "
            f"border-radius:{theme.RADIUS_SM}px; padding:1px 6px;"
        )
        title_row.addWidget(emu_badge)
        title_row.addStretch()
        info_col.addLayout(title_row)

        # Badge row — one pill per save type
        badge_row = QHBoxLayout()
        badge_row.setSpacing(6)
        all_types = sorted(
            {sf.save_type for s in saves for sf in s.save_files},
            key=lambda x: x.value,
        )
        for st in all_types:
            badge_row.addWidget(TypeBadge.for_save_type(st.value, self))
        badge_row.addStretch()
        info_col.addLayout(badge_row)

        # Meta row — muted so the title carries the emphasis
        meta_row = QHBoxLayout()
        meta_row.setSpacing(theme.GAP_MD)
        muted = f"color:{theme.text_muted()};"

        def _meta(text: str) -> CaptionLabel:
            lbl = CaptionLabel(text, self)
            lbl.setStyleSheet(muted)
            return lbl

        total_size = sum(s.total_size for s in saves)
        file_count = sum(len(s.save_files) for s in saves)
        last_mod = max(
            (s.last_modified for s in saves if s.last_modified),
            default=None,
        )

        meta_row.addWidget(_meta(f"ID: {ref.game_id}"))
        meta_row.addWidget(_meta(f"{t('scan.size')}: {_format_size(total_size)}"))
        meta_row.addWidget(_meta(
            f"{file_count} {t('backup.file_group', type='', count=str(file_count)).strip()}"
            if file_count > 1 else "1 file"
        ))
        if last_mod:
            meta_row.addWidget(_meta(last_mod.strftime("%Y/%m/%d %H:%M")))
        meta_row.addStretch()
        info_col.addLayout(meta_row)

        root.addLayout(info_col, 1)

    def _on_check(self, _state: int) -> None:
        self._selected = self.cb.isChecked()
        self.update()
        self.checked_changed.emit()

    def paintEvent(self, e) -> None:  # noqa: ANN001
        super().paintEvent(e)
        if not self._selected:
            return
        # A 2px accent outline marks a selected card.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(theme.accent()))
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = self.getBorderRadius() if hasattr(self, "getBorderRadius") else theme.RADIUS_MD
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), r, r)

    @property
    def is_checked(self) -> bool:
        return self.cb.isChecked()

    def set_checked(self, val: bool) -> None:
        self.cb.setChecked(val)


# -----------------------------------------------------------------------
# Page
# -----------------------------------------------------------------------

class BackupPage(QWidget):
    """Page for backing up scanned game saves with card-based UI."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("backup_page")
        self._saves: list[GameSave] = []
        self._backup_manager = None
        self._worker: _BackupWorker | None = None
        self._auto_worker: _AutoBackupWorker | None = None
        self._cards: list[_GameCard] = []
        self._icon_provider: GameIconProvider | None = None
        self._init_ui()

    def set_backup_manager(self, bm) -> None:  # noqa: ANN001
        self._backup_manager = bm

    def set_icon_provider(self, provider: GameIconProvider) -> None:
        self._icon_provider = provider

    def update_saves(self, saves: list[GameSave]) -> None:
        self._saves = saves
        self._refresh_cards()

    def auto_backup(self, saves: list[GameSave]) -> None:
        """Back up changed saves in the background (called by the auto cycle)."""
        if self._backup_manager is None or not saves:
            return
        if self._auto_worker is not None and self._auto_worker.isRunning():
            return
        self._auto_worker = _AutoBackupWorker(self)
        self._auto_worker.set_data(self._backup_manager, saves)
        self._auto_worker.finished.connect(self._on_auto_backup_finished)
        self._auto_worker.error.connect(
            lambda e: logger.error("Auto-backup error: {}", e)
        )
        self._auto_worker.start()

    def _on_auto_backup_finished(self, result) -> None:  # noqa: ANN001
        # Stay quiet when nothing changed, so periodic cycles aren't noisy.
        if result.backed_up > 0:
            InfoBar.success(
                title=t("backup.auto_backup_done"),
                content=t("backup.auto_backup_summary",
                          backed=str(result.backed_up), skipped=str(result.skipped)),
                parent=self, position=InfoBarPosition.TOP, duration=4000,
            )
        # Refresh card counts (a new backup may have been created).
        self._refresh_cards()

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(theme.PAGE_MARGIN_H, theme.PAGE_MARGIN_V,
                                       theme.PAGE_MARGIN_H, theme.PAGE_MARGIN_V)
        page_layout.setSpacing(theme.GAP_MD)

        page_layout.addWidget(PageHeader(t("backup.title"), t("backup.description"), self))

        # Action bar
        av = Qt.AlignmentFlag.AlignVCenter
        action_bar = QHBoxLayout()
        action_bar.setSpacing(theme.GAP_MD)

        self._select_all_cb = CheckBox(t("common.select_all"), self)
        self._select_all_cb.stateChanged.connect(self._on_select_all)
        action_bar.addWidget(self._select_all_cb, 0, av)

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
        action_bar.addSpacing(theme.GAP_SM)

        self._backup_btn = PrimaryPushButton(FIF.SAVE, t("backup.backup_selected"), self)
        self._backup_btn.clicked.connect(self._on_backup)
        action_bar.addWidget(self._backup_btn, 0, av)

        self._cancel_btn = PushButton(FIF.CLOSE, t("common.cancel"), self)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._cancel_btn.hide()
        action_bar.addWidget(self._cancel_btn, 0, av)

        page_layout.addLayout(action_bar)

        # Scrollable card area
        self._scroll = SmoothScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._scroll_inner = QWidget()
        self._card_layout = QVBoxLayout(self._scroll_inner)
        theme.apply_card_list_layout(self._card_layout)
        self._card_layout.addStretch()
        self._scroll.setWidget(self._scroll_inner)
        page_layout.addWidget(self._scroll, stretch=1)

        # Empty state
        self._empty = EmptyState(
            FIF.SAVE, t("empty.backup_title"), t("empty.backup_desc"), self
        )
        page_layout.addWidget(self._empty, stretch=1)
        self._scroll.hide()  # empty state shows until saves arrive

    # ------------------------------------------------------------------
    # Card population
    # ------------------------------------------------------------------

    def _refresh_cards(self) -> None:
        # Clear old cards
        for card in self._cards:
            self._card_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        # Group saves by game
        groups: dict[str, list[GameSave]] = defaultdict(list)
        for save in self._saves:
            groups[_game_group_key(save)].append(save)

        if not groups:
            self._scroll.hide()
            self._empty.show()
            self._update_count()
            return

        self._empty.hide()
        self._scroll.show()

        for key in sorted(groups):
            card = _GameCard(groups[key], self._icon_provider, self._scroll_inner)
            card.checked_changed.connect(self._update_count)
            self._card_layout.insertWidget(self._card_layout.count() - 1, card)
            self._cards.append(card)

        self._update_count()

    def _update_count(self) -> None:
        n = sum(1 for c in self._cards if c.is_checked)
        self._count_badge.setText(str(n))

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _on_select_all(self, state: int) -> None:
        checked = state == Qt.CheckState.Checked.value
        for card in self._cards:
            card.set_checked(checked)

    def _get_selected_saves(self) -> list[GameSave]:
        selected: list[GameSave] = []
        for card in self._cards:
            if card.is_checked:
                selected.extend(card.saves)
        return selected

    # ------------------------------------------------------------------
    # Backup action
    # ------------------------------------------------------------------

    def _on_backup(self) -> None:
        if self._backup_manager is None:
            InfoBar.warning(
                title=t("common.warning"),
                content="Backup manager not initialized",
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )
            return

        selected = self._get_selected_saves()
        if not selected:
            InfoBar.warning(
                title=t("common.warning"),
                content=t("backup.no_selection"),
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )
            return

        self._backup_btn.setEnabled(False)
        self._backup_btn.setText(t("backup.backing_up"))
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.show()
        self._progress.show()

        self._worker = _BackupWorker(self)
        self._worker.set_data(self._backup_manager, selected)
        self._worker.progress.connect(self._on_backup_progress)
        self._worker.finished.connect(self._on_backup_finished)
        self._worker.start()

    def _on_cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._cancel_btn.setEnabled(False)
            self._status_label.setText(t("common.canceling"))

    def _on_backup_progress(self, current: int, total: int) -> None:
        self._status_label.setText(f"{current}/{total}")

    def _on_backup_finished(self, success: int, errors: list) -> None:
        cancelled = self._worker is not None and self._worker.isInterruptionRequested()
        self._backup_btn.setEnabled(True)
        self._backup_btn.setText(t("backup.backup_selected"))
        self._cancel_btn.hide()
        self._progress.hide()
        self._status_label.setText("")

        if cancelled:
            InfoBar.warning(
                title=t("common.cancelled"),
                content=t("backup.backup_success", count=str(success)),
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )
        elif errors:
            InfoBar.warning(
                title=t("backup.backup_complete"),
                content=t("backup.backup_success", count=str(success))
                + f"\n{len(errors)} errors",
                parent=self, position=InfoBarPosition.TOP, duration=5000,
            )
        else:
            InfoBar.success(
                title=t("backup.backup_complete"),
                content=t("backup.backup_success", count=str(success)),
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )
