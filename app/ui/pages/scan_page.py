"""Scan page — emulator detection and save scanning with card-based UI.

Detected emulators are shown as summary cards at the top;  scanned game
saves are rendered as rich cards in a scrollable area grouped by game,
with coloured save-type badges, file counts and right-click context menu.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections import defaultdict

from PySide6.QtCore import Qt, Signal, QThread, QPoint
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
)
from qfluentwidgets import (
    SubtitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    PrimaryPushButton, PushButton, TransparentToolButton,
    CardWidget, SimpleCardWidget, SmoothScrollArea,
    FluentIcon as FIF, InfoBar, InfoBarPosition,
    ProgressRing, setFont, RoundMenu, Action,
    IconWidget, InfoBadge, SearchLineEdit,
)
from loguru import logger

from app.i18n import t
from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave, SaveType
from app.core.game_icon import GameIconProvider, IconDownloadWorker, get_plugin_icon


# -----------------------------------------------------------------------
# Worker
# -----------------------------------------------------------------------

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


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


_SAVE_TYPE_COLORS: dict[SaveType, str] = {
    SaveType.SAVESTATE: "#0078d4",
    SaveType.MEMCARD: "#107c10",
    SaveType.FOLDER: "#107c10",
    SaveType.BATTERY: "#ff8c00",
    SaveType.FILE: "#5c2d91",
}


# -----------------------------------------------------------------------
# Small widgets
# -----------------------------------------------------------------------

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


class _EmulatorCard(CardWidget):
    """Summary card for a detected emulator."""

    def __init__(self, emu: EmulatorInfo, save_count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(90)
        self.setMinimumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        # Emulator icon — from plugin folder icon.png or generic
        emu_pm = get_plugin_icon(emu.name, 36)
        if emu_pm and not emu_pm.isNull():
            icon_label = QLabel(self)
            icon_label.setFixedSize(36, 36)
            icon_label.setPixmap(emu_pm)
            root.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        else:
            icon = IconWidget(FIF.GAME, self)
            icon.setFixedSize(36, 36)
            root.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(4)
        col.setContentsMargins(0, 0, 0, 0)

        name = StrongBodyLabel(emu.name, self)
        setFont(name, 14, QFont.Weight.DemiBold)
        col.addWidget(name)

        platforms = ", ".join(emu.supported_platforms) if emu.supported_platforms else ""
        if platforms:
            plat_label = CaptionLabel(platforms, self)
            plat_label.setStyleSheet("color:#666;")
            col.addWidget(plat_label)

        meta = QHBoxLayout()
        meta.setSpacing(10)
        meta.addWidget(CaptionLabel(
            f"{t('scan.saves_count')}: {save_count}", self
        ))
        path_text = str(emu.data_path)
        if len(path_text) > 35:
            path_text = "…" + path_text[-32:]
        meta.addWidget(CaptionLabel(path_text, self))
        meta.addStretch()
        col.addLayout(meta)

        root.addLayout(col, 1)


class _GameSaveCard(CardWidget):
    """Card showing one game with all its saves and metadata."""

    ICON_WIDTH = 42
    ICON_MAX_HEIGHT = 58

    def __init__(
        self,
        game_id: str,
        saves: list[GameSave],
        icon_provider: GameIconProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.saves = saves
        self.game_id = game_id
        self.emulator = saves[0].emulator
        self.setFixedHeight(94)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        ref = saves[0]
        display_name = ref.game_name
        for s in saves:
            if s.game_name != s.game_id:
                display_name = s.game_name
                break

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 10)
        root.setSpacing(14)

        # Icon — cover art > plugin emulator icon > generic
        self._icon_label = QLabel(self)
        self._icon_label.setFixedWidth(self.ICON_WIDTH)
        self._icon_label.setMaximumHeight(self.ICON_MAX_HEIGHT)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pm = icon_provider.get_pixmap(ref.emulator, game_id, self.ICON_WIDTH, self.ICON_MAX_HEIGHT) if icon_provider else None
        if pm and not pm.isNull():
            self._icon_label.setPixmap(pm)
        else:
            self._set_fallback_icon(ref.emulator)
        root.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._icon_provider = icon_provider

        # Info column
        info = QVBoxLayout()
        info.setSpacing(4)
        info.setContentsMargins(0, 0, 0, 0)

        # Row 1: title + emulator badge
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        title = StrongBodyLabel(display_name, self)
        setFont(title, 13, QFont.Weight.DemiBold)
        row1.addWidget(title)

        emu_label = CaptionLabel(ref.emulator, self)
        emu_label.setStyleSheet(
            "background:#e0e0e0; color:#444; border-radius:3px; padding:1px 6px;"
        )
        row1.addWidget(emu_label)
        row1.addStretch()
        info.addLayout(row1)

        # Row 2: type badges
        row2 = QHBoxLayout()
        row2.setSpacing(5)
        all_types = sorted(
            {sf.save_type for s in saves for sf in s.save_files},
            key=lambda x: x.value,
        )
        for st in all_types:
            color = _SAVE_TYPE_COLORS.get(st, "#888")
            row2.addWidget(_TypeBadge(t(f"save_type.{st.value}"), color, self))
        row2.addStretch()
        info.addLayout(row2)

        # Row 3: meta
        row3 = QHBoxLayout()
        row3.setSpacing(14)
        row3.addWidget(CaptionLabel(f"ID: {game_id}", self))

        crc = ref.crc32
        for s in saves:
            if s.crc32:
                crc = s.crc32
                break
        if crc:
            crc_label = CaptionLabel(f"CRC32: {crc}", self)
            crc_label.setStyleSheet("color:#0078d4;")
            row3.addWidget(crc_label)

        total_size = sum(s.total_size for s in saves)
        row3.addWidget(CaptionLabel(_format_size(total_size), self))

        file_count = sum(len(s.save_files) for s in saves)
        row3.addWidget(CaptionLabel(f"{file_count} files", self))

        last_mod = max(
            (s.last_modified for s in saves if s.last_modified),
            default=None,
        )
        if last_mod:
            row3.addWidget(CaptionLabel(last_mod.strftime("%Y/%m/%d %H:%M"), self))
        row3.addStretch()
        info.addLayout(row3)

        root.addLayout(info, 1)

        # Open folder button
        open_btn = TransparentToolButton(FIF.FOLDER, self)
        open_btn.setFixedSize(32, 32)
        open_btn.setToolTip(t("common.open_folder"))
        open_btn.clicked.connect(lambda: self._open_folder())
        root.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def _set_fallback_icon(self, emulator_name: str) -> None:
        """Try plugin icon.png, otherwise show generic icon."""
        pm = get_plugin_icon(emulator_name, self.ICON_WIDTH)
        if pm and not pm.isNull():
            self._icon_label.setPixmap(pm)
        else:
            inner = QVBoxLayout()
            inner.setContentsMargins(0, 0, 0, 0)
            inner.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fw = IconWidget(FIF.GAME, self._icon_label)
            fw.setFixedSize(32, 32)
            inner.addWidget(fw)
            self._icon_label.setLayout(inner)

    def _open_folder(self) -> None:
        if not self.saves or not self.saves[0].save_files:
            return
        target = self.saves[0].save_files[0].path
        folder = target.parent if target.is_file() else target
        if not folder.exists():
            return
        if sys.platform == "win32":
            os.startfile(str(folder))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])  # noqa: S603
        else:
            subprocess.Popen(["xdg-open", str(folder)])  # noqa: S603

    def update_icon(self, pm: QPixmap) -> None:
        """Replace the fallback icon with a downloaded cover pixmap."""
        # Clear existing content (fallback IconWidget layout)
        layout = self._icon_label.layout()
        if layout:
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            QWidget().setLayout(layout)  # detach old layout
        self._icon_label.setPixmap(pm)

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001
        menu = RoundMenu(parent=self)
        open_action = Action(FIF.FOLDER, t("common.open_folder"), self)
        open_action.triggered.connect(self._open_folder)
        menu.addAction(open_action)
        menu.exec(event.globalPos())


# -----------------------------------------------------------------------
# Page
# -----------------------------------------------------------------------

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
        self._icon_worker: IconDownloadWorker | None = None
        self._icon_provider: GameIconProvider | None = None
        self._game_cards: list[_GameSaveCard] = []
        self._emu_cards: list[_EmulatorCard] = []
        self._init_ui()

    def set_scanner(self, scanner) -> None:  # noqa: ANN001
        self._scanner = scanner

    def set_icon_provider(self, provider: GameIconProvider) -> None:
        self._icon_provider = provider

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        page = QVBoxLayout(self)
        page.setContentsMargins(36, 20, 36, 20)
        page.setSpacing(12)

        # Title
        title = SubtitleLabel(t("scan.title"), self)
        desc = BodyLabel(t("scan.description"), self)
        desc.setWordWrap(True)
        page.addWidget(title)
        page.addWidget(desc)

        # Action bar
        action_bar = QHBoxLayout()
        action_bar.setSpacing(12)

        self._scan_btn = PrimaryPushButton(FIF.SEARCH, t("scan.start_scan"), self)
        self._scan_btn.setFixedWidth(160)
        self._scan_btn.clicked.connect(self._on_scan)
        action_bar.addWidget(self._scan_btn)

        self._progress = ProgressRing(self)
        self._progress.setFixedSize(24, 24)
        self._progress.hide()
        action_bar.addWidget(self._progress)

        self._status_label = CaptionLabel("", self)
        action_bar.addWidget(self._status_label)

        action_bar.addStretch()

        # Search
        self._search = SearchLineEdit(self)
        self._search.setPlaceholderText(t("common.search"))
        self._search.setFixedWidth(200)
        self._search.textChanged.connect(self._on_search)
        action_bar.addWidget(self._search)

        page.addLayout(action_bar)

        # Emulator cards row
        self._emu_row = QHBoxLayout()
        self._emu_row.setSpacing(10)
        page.addLayout(self._emu_row)

        # Save count badge
        count_row = QHBoxLayout()
        count_row.setSpacing(8)
        self._save_count_badge = InfoBadge.attension("0", parent=self)
        self._save_count_badge.setFixedHeight(20)
        count_row.addWidget(self._save_count_badge)
        self._save_count_label = BodyLabel(t("scan.saves_count"), self)
        count_row.addWidget(self._save_count_label)
        count_row.addStretch()
        page.addLayout(count_row)

        # Scrollable card area
        self._scroll = SmoothScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._scroll_inner = QWidget()
        self._card_layout = QVBoxLayout(self._scroll_inner)
        self._card_layout.setContentsMargins(0, 0, 8, 0)
        self._card_layout.setSpacing(6)
        self._card_layout.addStretch()
        self._scroll.setWidget(self._scroll_inner)
        page.addWidget(self._scroll, stretch=1)

        # Empty state
        self._empty_label = BodyLabel(t("common.no_data"), self)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #888;")
        page.addWidget(self._empty_label)

    # ------------------------------------------------------------------
    # Scan logic
    # ------------------------------------------------------------------

    def _on_scan(self) -> None:
        if self._scanner is None:
            InfoBar.warning(
                title=t("common.warning"),
                content="Scanner not initialized",
                parent=self, position=InfoBarPosition.TOP, duration=3000,
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
            f"{t('scan.found_emulators', count=str(len(emulators)))}"
        )
        # Register emulator data paths for icon look-up
        if self._icon_provider:
            for emu in emulators:
                self._icon_provider.register_emulator(emu.name, emu.data_path)

            # Register per-plugin cover URL resolvers
            if self._scanner is not None:
                pm = getattr(self._scanner, "_pm", None)
                if pm is not None:
                    seen_plugins: set[str] = set()
                    for emu in emulators:
                        if emu.name in seen_plugins:
                            continue
                        seen_plugins.add(emu.name)
                        plugin = pm.get_plugin(emu.name)
                        if plugin is not None and hasattr(plugin, "get_cover_urls"):
                            self._icon_provider.register_cover_resolver(
                                emu.name, plugin.get_cover_urls,
                            )

        self._refresh_emulator_cards()
        self._refresh_game_cards()
        self._start_icon_download()
        self.saves_updated.emit(saves)

        InfoBar.success(
            title=t("scan.scan_complete"),
            content=t("scan.found_saves", count=str(len(saves))),
            parent=self, position=InfoBarPosition.TOP, duration=3000,
        )

    def _on_scan_error(self, error: str) -> None:
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText(t("scan.start_scan"))
        self._progress.hide()
        self._status_label.setText("")
        InfoBar.error(
            title=t("common.error"), content=error,
            parent=self, position=InfoBarPosition.TOP, duration=5000,
        )

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _refresh_emulator_cards(self) -> None:
        for c in self._emu_cards:
            self._emu_row.removeWidget(c)
            c.deleteLater()
        self._emu_cards.clear()
        # Remove old stretch
        while self._emu_row.count():
            item = self._emu_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for emu in self._emulators:
            count = sum(1 for s in self._saves if s.emulator == emu.name)
            card = _EmulatorCard(emu, count, self)
            self._emu_row.addWidget(card)
            self._emu_cards.append(card)
        self._emu_row.addStretch()

    def _refresh_game_cards(self, filter_text: str = "") -> None:
        for c in self._game_cards:
            self._card_layout.removeWidget(c)
            c.deleteLater()
        self._game_cards.clear()

        # Group saves by game
        groups: dict[str, list[GameSave]] = defaultdict(list)
        for save in self._saves:
            groups[f"{save.emulator}:{save.game_id}"].append(save)

        shown = 0
        ft = filter_text.lower()
        for key in sorted(groups):
            saves = groups[key]
            # Apply filter
            if ft:
                match = any(
                    ft in s.game_name.lower() or ft in s.game_id.lower()
                    or ft in s.emulator.lower() or ft in (s.crc32 or "").lower()
                    for s in saves
                )
                if not match:
                    continue

            game_id = saves[0].game_id
            card = _GameSaveCard(game_id, saves, self._icon_provider, self._scroll_inner)
            self._card_layout.insertWidget(self._card_layout.count() - 1, card)
            self._game_cards.append(card)
            shown += 1

        self._save_count_badge.setText(str(shown))
        has_cards = shown > 0
        self._scroll.setVisible(has_cards)
        self._empty_label.setVisible(not has_cards)

    def _on_search(self, text: str) -> None:
        self._refresh_game_cards(text)

    # ------------------------------------------------------------------
    # Icon downloading
    # ------------------------------------------------------------------

    def _start_icon_download(self) -> None:
        """Kick off background downloads for missing cover art."""
        if not self._icon_provider or not self._saves:
            return
        # Collect unique (emulator, game_id) pairs that have no icon yet
        requests: list[tuple[str, str]] = []
        seen: set[str] = set()
        for s in self._saves:
            key = f"{s.emulator}:{s.game_id}"
            if key in seen:
                continue
            seen.add(key)
            if not self._icon_provider.get_icon_path(s.emulator, s.game_id):
                requests.append((s.emulator, s.game_id))
        if not requests:
            return
        self._icon_worker = IconDownloadWorker(self._icon_provider, requests, self)
        self._icon_worker.icon_ready.connect(self._on_icon_ready)
        self._icon_worker.start()

    def _on_icon_ready(self, emulator: str, game_id: str, path: str) -> None:
        """Update the card icon when a cover has been downloaded."""
        from app.core.game_icon import _rounded_cover_pixmap
        pm = QPixmap(path)
        if pm.isNull():
            return
        w = _GameSaveCard.ICON_WIDTH
        mh = _GameSaveCard.ICON_MAX_HEIGHT
        rpm = _rounded_cover_pixmap(pm, w, mh)
        if self._icon_provider:
            self._icon_provider.put_pixmap(emulator, game_id, w, rpm)
        for card in self._game_cards:
            if card.emulator == emulator and card.game_id == game_id:
                card.update_icon(rpm)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def get_saves(self) -> list[GameSave]:
        return list(self._saves)
