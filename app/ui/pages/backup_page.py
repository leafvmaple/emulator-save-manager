"""Backup page — card-based UI for backing up game saves.

Each game is shown as a styled card with checkbox, game info, save-type
badges, file details and action area.  Cards live inside a scrollable
layout so the page handles many games gracefully.
"""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import Qt, Signal, QThread, QSize
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
    QGraphicsDropShadowEffect,
)
from qfluentwidgets import (
    SubtitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
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
            try:
                self._backup_manager.create_backup(saves)
                success += 1
            except Exception as e:
                errors.append(f"{saves[0].game_name}: {e}")
            self.progress.emit(i, total)
        self.finished.emit(success, errors)


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


_SAVE_TYPE_COLORS: dict[SaveType, str] = {
    SaveType.SAVESTATE: "#0078d4",
    SaveType.MEMCARD: "#107c10",
    SaveType.FOLDER: "#107c10",
    SaveType.BATTERY: "#ff8c00",
    SaveType.FILE: "#5c2d91",
}


def _game_group_key(save: GameSave) -> str:
    return f"{save.emulator}:{save.game_id}"


# -----------------------------------------------------------------------
# Badge Widget
# -----------------------------------------------------------------------

class _TypeBadge(QLabel):
    """A small coloured pill that indicates a save type."""

    def __init__(self, text: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFixedHeight(20)
        setFont(self, 11)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pad = max(24, self.fontMetrics().horizontalAdvance(text) + 16)
        self.setFixedWidth(pad)
        self.setStyleSheet(
            f"background:{color}; color:white; border-radius:10px; "
            f"padding: 0 6px; font-weight:500;"
        )


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
        self.setFixedHeight(110)

        ref = saves[0]
        display_name = ref.game_name
        for s in saves:
            if s.game_name != s.game_id:
                display_name = s.game_name
                break

        root = QHBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 12)
        root.setSpacing(16)

        # --- Checkbox ---
        self.cb = CheckBox(self)
        self.cb.stateChanged.connect(lambda _: self.checked_changed.emit())
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
            emu_pm = get_plugin_icon(ref.emulator, self.ICON_WIDTH)
            if emu_pm and not emu_pm.isNull():
                self._icon_label.setPixmap(emu_pm)
            else:
                fallback = IconWidget(FIF.GAME, self)
                fallback.setFixedSize(40, 40)
                fl = QVBoxLayout(self._icon_label)
                fl.setContentsMargins(0, 0, 0, 0)
                fl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                fl.addWidget(fallback)
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
            "background:#e0e0e0; color:#444; border-radius:3px; padding:1px 6px;"
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
            color = _SAVE_TYPE_COLORS.get(st, "#888")
            badge_row.addWidget(_TypeBadge(t(f"save_type.{st.value}"), color, self))
        badge_row.addStretch()
        info_col.addLayout(badge_row)

        # Meta row
        meta_row = QHBoxLayout()
        meta_row.setSpacing(16)

        total_size = sum(s.total_size for s in saves)
        file_count = sum(len(s.save_files) for s in saves)
        last_mod = max(
            (s.last_modified for s in saves if s.last_modified),
            default=None,
        )

        meta_row.addWidget(CaptionLabel(
            f"ID: {ref.game_id}", self
        ))
        meta_row.addWidget(CaptionLabel(
            f"{t('scan.size')}: {_format_size(total_size)}", self
        ))
        meta_row.addWidget(CaptionLabel(
            f"{file_count} {t('backup.file_group', type='', count=str(file_count)).strip()}"
            if file_count > 1 else f"1 file", self
        ))
        if last_mod:
            meta_row.addWidget(CaptionLabel(
                last_mod.strftime("%Y/%m/%d %H:%M"), self
            ))
        meta_row.addStretch()
        info_col.addLayout(meta_row)

        root.addLayout(info_col, 1)

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

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(36, 20, 36, 20)
        page_layout.setSpacing(12)

        title = SubtitleLabel(t("backup.title"), self)
        desc = BodyLabel(t("backup.description"), self)
        desc.setWordWrap(True)
        page_layout.addWidget(title)
        page_layout.addWidget(desc)

        # Action bar
        action_bar = QHBoxLayout()
        action_bar.setSpacing(12)

        self._select_all_cb = CheckBox(t("common.select_all"), self)
        self._select_all_cb.stateChanged.connect(self._on_select_all)
        action_bar.addWidget(self._select_all_cb)

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

        self._backup_btn = PrimaryPushButton(FIF.SAVE, t("backup.backup_selected"), self)
        self._backup_btn.setFixedWidth(160)
        self._backup_btn.clicked.connect(self._on_backup)
        action_bar.addWidget(self._backup_btn)

        page_layout.addLayout(action_bar)

        # Scrollable card area
        self._scroll = SmoothScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._scroll_inner = QWidget()
        self._card_layout = QVBoxLayout(self._scroll_inner)
        self._card_layout.setContentsMargins(8, 8, 8, 8)
        self._card_layout.setSpacing(8)
        self._card_layout.addStretch()
        self._scroll.setWidget(self._scroll_inner)
        page_layout.addWidget(self._scroll, stretch=1)

        # Empty state
        self._empty_label = BodyLabel(t("common.no_data"), self)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #888;")
        page_layout.addWidget(self._empty_label)
        self._empty_label.hide()

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
            self._empty_label.show()
            self._update_count()
            return

        self._empty_label.hide()
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
