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
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread, QPoint
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, StrongBodyLabel,
    PrimaryPushButton, PushButton, TransparentToolButton,
    CardWidget, SimpleCardWidget, SmoothScrollArea,
    SingleDirectionScrollArea,
    FluentIcon as FIF, InfoBar, InfoBarPosition,
    ProgressRing, setFont, RoundMenu, Action,
    IconWidget, InfoBadge, SearchLineEdit,
)
from loguru import logger

from app.i18n import t
from app.models.emulator import EmulatorInfo
from app.models.game_save import GameSave, SaveType
from app.core.game_icon import GameIconProvider, IconDownloadWorker, get_plugin_icon
from app.core import scan_cache
from app.ui import theme
from app.ui.components.badge import TypeBadge
from app.ui.components.page_header import PageHeader
from app.ui.components.empty_state import EmptyState
from app.ui.components.avatar import letter_avatar
from app.ui.components.skeleton import SkeletonCard
from app.ui.components.elevation import add_hover_elevation


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
            emulators, saves = self._scanner.full_scan(
                should_cancel=self.isInterruptionRequested,
            )
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


# -----------------------------------------------------------------------
# Small widgets
# -----------------------------------------------------------------------

class _EmulatorCard(CardWidget):
    """Summary card for a detected emulator."""

    def __init__(self, emu: EmulatorInfo, save_count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(300, 90)

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
            plat_label.setStyleSheet(f"color:{theme.text_secondary()};")
            col.addWidget(plat_label)

        saves_label = CaptionLabel(
            f"{t('scan.saves_count')}: {save_count}", self
        )
        col.addWidget(saves_label)

        root.addLayout(col, 1)


class _GameSaveCard(CardWidget):
    """Card showing one game with all its saves and metadata.

    Clicking the expand chevron reveals a table of individual save files
    with file name, type, size and last-modified time.
    """

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
        self._expanded = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        add_hover_elevation(self)

        ref = saves[0]
        display_name = ref.game_name
        for s in saves:
            if s.game_name != s.game_id:
                display_name = s.game_name
                break

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 12, 20, 12)
        outer.setSpacing(0)

        # -- Top summary row (always visible) --
        self._summary = QWidget(self)
        self._summary.setFixedHeight(78)
        root = QHBoxLayout(self._summary)
        root.setContentsMargins(0, 2, 0, 2)
        root.setSpacing(theme.GAP_MD)

        # Icon — cover art > plugin emulator icon > generic
        self._icon_label = QLabel(self)
        self._icon_label.setFixedWidth(self.ICON_WIDTH)
        self._icon_label.setMaximumHeight(self.ICON_MAX_HEIGHT)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pm = icon_provider.get_pixmap(ref.emulator, game_id, self.ICON_WIDTH, self.ICON_MAX_HEIGHT) if icon_provider else None
        if pm and not pm.isNull():
            self._icon_label.setPixmap(pm)
        else:
            self._set_fallback_icon(display_name)
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
        setFont(title, 14, QFont.Weight.DemiBold)
        row1.addWidget(title)

        emu_label = CaptionLabel(ref.emulator, self)
        emu_label.setStyleSheet(
            f"background:{theme.subtle_fill()}; color:{theme.subtle_fill_text()}; "
            f"border-radius:{theme.RADIUS_SM}px; padding:1px 6px;"
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
            row2.addWidget(TypeBadge.for_save_type(st.value, self))
        row2.addStretch()
        info.addLayout(row2)

        # Row 3: meta — muted so the title + CRC accent carry the emphasis
        row3 = QHBoxLayout()
        row3.setSpacing(theme.GAP_MD)
        muted = f"color:{theme.text_muted()};"

        def _meta(text: str) -> CaptionLabel:
            lbl = CaptionLabel(text, self)
            lbl.setStyleSheet(muted)
            return lbl

        row3.addWidget(_meta(f"ID: {game_id}"))

        crc = ref.crc32
        for s in saves:
            if s.crc32:
                crc = s.crc32
                break
        if crc:
            crc_label = CaptionLabel(f"CRC32: {crc}", self)
            crc_label.setStyleSheet(f"color:{theme.accent()};")
            row3.addWidget(crc_label)

        total_size = sum(s.total_size for s in saves)
        row3.addWidget(_meta(_format_size(total_size)))

        file_count = sum(len(s.save_files) for s in saves)
        row3.addWidget(_meta(f"{file_count} {t('scan.files')}"))

        last_mod = max(
            (s.last_modified for s in saves if s.last_modified),
            default=None,
        )
        if last_mod:
            row3.addWidget(_meta(last_mod.strftime("%Y/%m/%d %H:%M")))
        row3.addStretch()
        info.addLayout(row3)

        root.addLayout(info, 1)

        # Expand / collapse chevron — clicking anywhere on the card row also
        # toggles it (the action buttons consume their own clicks first).
        self._expand_btn = TransparentToolButton(FIF.CHEVRON_RIGHT, self)
        self._expand_btn.setFixedSize(28, 28)
        self._expand_btn.setToolTip(t("scan.show_files"))
        self._expand_btn.clicked.connect(self._toggle_expand)
        self.clicked.connect(self._toggle_expand)
        root.addWidget(self._expand_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # Open folder button
        open_btn = TransparentToolButton(FIF.FOLDER, self)
        open_btn.setFixedSize(32, 32)
        open_btn.setToolTip(t("common.open_folder"))
        open_btn.clicked.connect(lambda: self._open_folder())
        root.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        outer.addWidget(self._summary)

        # -- File detail area (hidden by default) --
        self._detail_widget = QWidget(self)
        self._detail_widget.setVisible(False)
        detail_layout = QVBoxLayout(self._detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(2)

        # Separator line
        sep = QLabel(self)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.divider()};")
        detail_layout.addWidget(sep)

        # Collect all save files
        all_files = []
        for s in saves:
            for sf in s.save_files:
                all_files.append(sf)

        # Header row
        hdr = QHBoxLayout()
        hdr.setSpacing(0)
        _hdr_css = f"color:{theme.text_muted()}; font-weight:600;"
        hdr_name = CaptionLabel(t("scan.file_name"), self)
        hdr_name.setStyleSheet(_hdr_css)
        hdr_name.setFixedWidth(260)
        hdr.addWidget(hdr_name)
        hdr_type = CaptionLabel(t("scan.save_type"), self)
        hdr_type.setStyleSheet(_hdr_css)
        hdr_type.setFixedWidth(90)
        hdr.addWidget(hdr_type)
        hdr_size = CaptionLabel(t("scan.size"), self)
        hdr_size.setStyleSheet(_hdr_css)
        hdr_size.setFixedWidth(80)
        hdr.addWidget(hdr_size)
        hdr_mod = CaptionLabel(t("scan.last_modified"), self)
        hdr_mod.setStyleSheet(_hdr_css)
        hdr.addWidget(hdr_mod)
        hdr.addStretch()
        detail_layout.addLayout(hdr)

        # File rows — each is a hoverable widget for scanability
        muted = f"color:{theme.text_muted()};"
        for sf in all_files:
            row_widget = QWidget(self)
            row_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            row_widget.setStyleSheet(
                f"QWidget:hover {{ background:{theme.subtle_fill()}; "
                f"border-radius:{theme.RADIUS_SM}px; }}"
            )
            frow = QHBoxLayout(row_widget)
            frow.setContentsMargins(4, 2, 4, 2)
            frow.setSpacing(0)

            # File / folder name (with path tooltip)
            file_label = CaptionLabel(sf.path.name, self)
            file_label.setFixedWidth(256)
            file_label.setToolTip(str(sf.path))
            file_label.setStyleSheet(f"color:{theme.text_primary()}; font-weight:600;")
            frow.addWidget(file_label)

            # Type
            type_label = CaptionLabel(t(f"save_type.{sf.save_type.value}"), self)
            type_label.setFixedWidth(90)
            type_label.setStyleSheet(f"color:{theme.status_text(sf.save_type.value)};")
            frow.addWidget(type_label)

            # Size
            size_label = CaptionLabel(_format_size(sf.size), self)
            size_label.setFixedWidth(80)
            size_label.setStyleSheet(muted)
            frow.addWidget(size_label)

            # Modified time
            mod_label = CaptionLabel(
                sf.modified.strftime("%Y/%m/%d %H:%M") if sf.modified else "-",
                self,
            )
            mod_label.setStyleSheet(muted)
            frow.addWidget(mod_label)
            frow.addStretch()

            # Open folder button for this file
            file_folder_btn = TransparentToolButton(FIF.FOLDER, self)
            file_folder_btn.setFixedSize(24, 24)
            file_folder_btn.setToolTip(t("common.open_folder"))
            _path = sf.path
            file_folder_btn.clicked.connect(lambda checked=False, p=_path: self._open_file_folder(p))
            frow.addWidget(file_folder_btn)

            detail_layout.addWidget(row_widget)

        outer.addWidget(self._detail_widget)

    # -- Expand / collapse --

    def _toggle_expand(self) -> None:
        self._expanded = not self._expanded
        self._detail_widget.setVisible(self._expanded)
        if self._expanded:
            self._expand_btn.setIcon(FIF.CHEVRON_DOWN_MED)
            self._expand_btn.setToolTip(t("scan.hide_files"))
        else:
            self._expand_btn.setIcon(FIF.CHEVRON_RIGHT)
            self._expand_btn.setToolTip(t("scan.show_files"))

    def _set_fallback_icon(self, label_text: str) -> None:
        """Show a deterministic letter-avatar when there's no cover art."""
        self._icon_label.setPixmap(letter_avatar(label_text, self.ICON_WIDTH))

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

    @staticmethod
    def _open_file_folder(path: Path) -> None:
        """Open the folder containing a specific save file."""
        folder = path.parent if path.is_file() else path
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
        self._skeletons: list[SkeletonCard] = []
        self._cache_file: Path | None = None
        self._init_ui()

    def set_scanner(self, scanner) -> None:  # noqa: ANN001
        self._scanner = scanner

    def set_icon_provider(self, provider: GameIconProvider) -> None:
        self._icon_provider = provider

    def set_cache_file(self, path: Path) -> None:
        """Where to persist / restore the last scan result."""
        self._cache_file = path

    def load_cache(self) -> bool:
        """Populate the page from the cached scan, if any.  Returns True if it had data."""
        if self._cache_file is None:
            return False
        emulators, saves = scan_cache.load_scan(self._cache_file)
        if not emulators and not saves:
            return False
        self._populate(emulators, saves)
        self._status_label.setText(
            t("scan.found_emulators", count=str(len(emulators)))
        )
        return True

    def _save_cache(self) -> None:
        if self._cache_file is not None:
            scan_cache.save_scan(self._cache_file, self._emulators, self._saves)

    def start_scan(self) -> None:
        """Public entry point for triggering a scan (e.g. on startup)."""
        self._on_scan()

    @property
    def is_scanning(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        page = QVBoxLayout(self)
        page.setContentsMargins(theme.PAGE_MARGIN_H, theme.PAGE_MARGIN_V,
                                theme.PAGE_MARGIN_H, theme.PAGE_MARGIN_V)
        page.setSpacing(theme.GAP_MD)

        # Title + description
        page.addWidget(PageHeader(t("scan.title"), t("scan.description"), self))

        # Action bar — search on the left, status + primary action on the right.
        # Everything is vertically centred so the button doesn't tower over the
        # caption beside it.
        av = Qt.AlignmentFlag.AlignVCenter
        action_bar = QHBoxLayout()
        action_bar.setSpacing(theme.GAP_MD)

        # Search (left)
        self._search = SearchLineEdit(self)
        self._search.setPlaceholderText(t("common.search"))
        self._search.setFixedWidth(280)
        self._search.textChanged.connect(self._on_search)
        action_bar.addWidget(self._search, 0, av)

        action_bar.addStretch()

        self._progress = ProgressRing(self)
        self._progress.setFixedSize(20, 20)
        self._progress.hide()
        action_bar.addWidget(self._progress, 0, av)

        self._status_label = CaptionLabel("", self)
        self._status_label.setStyleSheet(f"color:{theme.text_muted()};")
        action_bar.addWidget(self._status_label, 0, av)
        action_bar.addSpacing(theme.GAP_SM)

        self._scan_btn = PrimaryPushButton(FIF.SEARCH, t("scan.start_scan"), self)
        self._scan_btn.clicked.connect(self._on_scan)
        action_bar.addWidget(self._scan_btn, 0, av)

        self._cancel_btn = PushButton(FIF.CLOSE, t("common.cancel"), self)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._cancel_btn.hide()
        action_bar.addWidget(self._cancel_btn, 0, av)

        page.addLayout(action_bar)

        # Emulator cards row (horizontally scrollable)
        self._emu_scroll = SingleDirectionScrollArea(self, orient=Qt.Orientation.Horizontal)
        self._emu_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._emu_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._emu_scroll.enableTransparentBackground()
        # Height matches the inner card exactly — no dead space below it, so the
        # gap to the count row stays equal to every other gap (the scrollbar is
        # an overlay and doesn't reserve layout space).
        self._emu_scroll.setFixedHeight(90)
        self._emu_scroll.hide()
        self._emu_scroll_inner = QWidget()
        self._emu_scroll_inner.setStyleSheet("background: transparent;")
        self._emu_scroll_inner.setFixedHeight(90)
        self._emu_row = QHBoxLayout(self._emu_scroll_inner)
        self._emu_row.setSpacing(10)
        self._emu_row.setContentsMargins(0, 0, 0, 0)
        self._emu_scroll.setWidget(self._emu_scroll_inner)
        page.addWidget(self._emu_scroll)

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
        # No top margin: the gap above the first card is just the page spacing,
        # matching the card-to-card spacing for an even vertical rhythm.
        self._card_layout.setContentsMargins(theme.GAP_XS, 0, theme.GAP_XS, theme.GAP_XS)
        self._card_layout.setSpacing(theme.GAP_MD)
        self._card_layout.addStretch()
        self._scroll.setWidget(self._scroll_inner)
        page.addWidget(self._scroll, stretch=1)

        # Empty state — shown until the first scan produces cards
        self._empty = EmptyState(
            FIF.SEARCH, t("empty.scan_title"), t("empty.scan_desc"), self
        )
        page.addWidget(self._empty, stretch=1)
        self._scroll.setVisible(False)

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
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.show()
        self.setFocus()  # prevent focus jumping to search bar
        self._progress.show()
        self._status_label.setText(t("scan.scanning"))
        self._show_skeletons()

        self._worker = _ScanWorker(self)
        self._worker.set_scanner(self._scanner)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _show_skeletons(self, count: int = 3) -> None:
        """Replace the card list with loading skeletons while scanning."""
        self._clear_skeletons()
        for c in self._game_cards:
            self._card_layout.removeWidget(c)
            c.deleteLater()
        self._game_cards.clear()
        for _ in range(count):
            sk = SkeletonCard(self._scroll_inner)
            self._card_layout.insertWidget(self._card_layout.count() - 1, sk)
            self._skeletons.append(sk)
        self._empty.setVisible(False)
        self._scroll.setVisible(True)

    def _clear_skeletons(self) -> None:
        for sk in self._skeletons:
            self._card_layout.removeWidget(sk)
            sk.deleteLater()
        self._skeletons.clear()

    def _on_cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._cancel_btn.setEnabled(False)
            self._status_label.setText(t("common.canceling"))

    def _populate(self, emulators: list, saves: list) -> None:
        """Apply scan results (live or cached) to the UI and notify listeners."""
        self._emulators = emulators
        self._saves = saves

        # Register emulator data paths + per-plugin cover/thumbnail resolvers.
        if self._icon_provider:
            for emu in emulators:
                self._icon_provider.register_emulator(emu.name, emu.data_path)
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
                        if plugin is not None and hasattr(plugin, "get_state_thumbnail"):
                            self._icon_provider.register_thumbnail_extractor(
                                emu.name, plugin.get_state_thumbnail,
                            )

        self._refresh_emulator_cards()
        self._refresh_game_cards()
        self._start_icon_download()
        self.saves_updated.emit(saves)

    def _on_scan_finished(self, emulators: list, saves: list) -> None:
        cancelled = self._worker is not None and self._worker.isInterruptionRequested()
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText(t("scan.start_scan"))
        self._cancel_btn.hide()
        self._progress.hide()
        if cancelled:
            self._status_label.setText(t("common.cancelled"))
        else:
            self._status_label.setText(
                f"{t('scan.found_emulators', count=str(len(emulators)))}"
            )

        self._populate(emulators, saves)
        if not cancelled:  # don't persist a partial/cancelled scan
            self._save_cache()

        if cancelled:
            InfoBar.warning(
                title=t("common.cancelled"),
                content=t("scan.found_saves", count=str(len(saves))),
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )
        else:
            InfoBar.success(
                title=t("scan.scan_complete"),
                content=t("scan.found_saves", count=str(len(saves))),
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )

    def _on_scan_error(self, error: str) -> None:
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText(t("scan.start_scan"))
        self._cancel_btn.hide()
        self._progress.hide()
        self._status_label.setText("")
        self._clear_skeletons()
        self._scroll.setVisible(bool(self._game_cards))
        self._empty.setVisible(not self._game_cards)
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
            card = _EmulatorCard(emu, count, self._emu_scroll_inner)
            self._emu_row.addWidget(card)
            self._emu_cards.append(card)
        # Resize inner widget so it can overflow the viewport and scroll
        n = len(self._emu_cards)
        if n > 0:
            total_w = n * 300 + (n - 1) * self._emu_row.spacing()
            self._emu_scroll_inner.setMinimumWidth(total_w)
        self._emu_scroll.setVisible(n > 0)

    def _refresh_game_cards(self, filter_text: str = "") -> None:
        self._clear_skeletons()
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
        self._empty.setVisible(not has_cards)

    def _on_search(self, text: str) -> None:
        self._refresh_game_cards(text)

    # ------------------------------------------------------------------
    # Icon downloading
    # ------------------------------------------------------------------

    def _start_icon_download(self) -> None:
        """Kick off background fetch of cover art / save-state thumbnails."""
        if not self._icon_provider or not self._saves:
            return
        # Gather save-state paths per game (newest first) for thumbnail fallback.
        states_by_key: dict[str, list] = {}
        for s in self._saves:
            key = f"{s.emulator}:{s.game_id}"
            for sf in s.save_files:
                if sf.save_type == SaveType.SAVESTATE:
                    states_by_key.setdefault(key, []).append((sf.modified, sf.path))

        # One request per unique game that has no icon yet.
        requests: list[tuple] = []
        seen: set[str] = set()
        for s in self._saves:
            key = f"{s.emulator}:{s.game_id}"
            if key in seen:
                continue
            seen.add(key)
            if self._icon_provider.get_icon_path(s.emulator, s.game_id):
                continue
            paths = [
                p for _, p in sorted(
                    states_by_key.get(key, []), key=lambda x: x[0], reverse=True
                )
            ]
            requests.append((s.emulator, s.game_id, paths))
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

    def get_emulators(self) -> list[EmulatorInfo]:
        return list(self._emulators)
