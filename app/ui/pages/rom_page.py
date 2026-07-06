"""ROM library page — read-only library view (ROM Stage A).

Scans the configured ROM directories in a background thread, then shows
the library with duplicate dumps and save links highlighted.  Strictly
read-only: the page never moves, renames or deletes ROM files.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QHeaderView, QTableWidgetItem,
    QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, IconWidget, InfoBar,
    InfoBarPosition, PrimaryPushButton, ProgressRing, PushButton,
    StrongBodyLabel, SubtitleLabel, TableWidget, TransparentToolButton,
    FluentIcon as FIF,
)

from app.config import Config
from app.core.rom_library import RomLibrary, RomLibraryReport, build_report
from app.i18n import t
from app.models.game_save import GameSave
from app.ui import theme
from app.ui.components.page_header import PageHeader


def _fmt_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


class _RomScanWorker(QThread):
    """Background thread that scans + hashes the ROM library."""

    finished = Signal(object)      # RomLibraryReport
    progressed = Signal(int, int)  # done, total
    error = Signal(str)

    def __init__(
        self,
        library: RomLibrary,
        saves: list[GameSave],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._saves = saves

    def run(self) -> None:
        try:
            roms = self._library.scan(
                should_cancel=self.isInterruptionRequested,
                progress=self.progressed.emit,
            )
            self.finished.emit(
                build_report(roms, self._saves,
                             dat_games=self._library.dat_games))
        except Exception as e:
            self.error.emit(str(e))


class RomPage(QWidget):
    """Read-only ROM library view with duplicate / save-link analysis."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("rom_page")
        self._config: Config | None = None
        self._saves: list[GameSave] = []
        self._worker: _RomScanWorker | None = None
        self._report: RomLibraryReport | None = None
        self._init_ui()

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def set_config(self, config: Config) -> None:
        self._config = config
        self._refresh_dirs()

    def update_saves(self, saves: list) -> None:
        """Receive the latest save scan (connected to scan_page.saves_updated)."""
        self._saves = list(saves)

    def restyle(self) -> None:
        """Re-apply theme-dependent styles after a live theme switch."""
        self._status_msg.setStyleSheet(f"color:{theme.text_muted()};")
        self._orphan_hint.setStyleSheet(f"color:{theme.text_muted()};")
        self._refresh_dirs()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.PAGE_MARGIN_H, theme.PAGE_MARGIN_V,
                                  theme.PAGE_MARGIN_H, theme.PAGE_MARGIN_V)
        layout.setSpacing(theme.GAP_LG)

        layout.addWidget(PageHeader(t("rom.title"), t("rom.description"), self))

        # Directory card — configured ROM dirs with add / remove
        self._dirs_card = CardWidget(self)
        card_lay = QVBoxLayout(self._dirs_card)
        card_lay.setContentsMargins(20, 16, 20, 16)
        card_lay.setSpacing(theme.GAP_SM)

        head = QHBoxLayout()
        head.setSpacing(theme.GAP_SM)
        ic = IconWidget(FIF.FOLDER, self._dirs_card)
        ic.setFixedSize(18, 18)
        head.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(StrongBodyLabel(t("rom.dirs_title"), self._dirs_card),
                       0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch()
        self._add_dir_btn = PushButton(FIF.FOLDER_ADD, t("rom.add_dir"),
                                       self._dirs_card)
        self._add_dir_btn.clicked.connect(self._on_add_dir)
        head.addWidget(self._add_dir_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        card_lay.addLayout(head)

        self._dirs_lay = QVBoxLayout()
        self._dirs_lay.setSpacing(theme.GAP_XS)
        card_lay.addLayout(self._dirs_lay)
        layout.addWidget(self._dirs_card)

        # Action bar
        av = Qt.AlignmentFlag.AlignVCenter
        action_bar = QHBoxLayout()
        action_bar.setSpacing(theme.GAP_SM)

        self._scan_btn = PrimaryPushButton(FIF.SEARCH, t("rom.scan"), self)
        self._scan_btn.clicked.connect(self._on_scan)
        action_bar.addWidget(self._scan_btn, 0, av)

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

        # Results table
        self._table = TableWidget(self)
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            t("rom.col_name"), t("rom.col_platform"), t("rom.col_size"),
            t("rom.col_crc"), t("rom.col_status"),
        ])
        self._table.verticalHeader().hide()
        self._table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._table.hide()
        layout.addWidget(self._table, 1)

        # Saves whose ROM is missing (renamed / moved) — the actionable bit
        self._orphan_title = SubtitleLabel(t("rom.orphan_saves_title"), self)
        self._orphan_title.hide()
        layout.addWidget(self._orphan_title)

        self._orphan_hint = CaptionLabel(t("rom.orphan_saves_hint"), self)
        self._orphan_hint.setWordWrap(True)
        self._orphan_hint.setStyleSheet(f"color:{theme.text_muted()};")
        self._orphan_hint.hide()
        layout.addWidget(self._orphan_hint)

        self._orphan_body = BodyLabel("", self)
        self._orphan_body.setWordWrap(True)
        self._orphan_body.hide()
        layout.addWidget(self._orphan_body)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Directory management
    # ------------------------------------------------------------------

    def _refresh_dirs(self) -> None:
        while self._dirs_lay.count():
            item = self._dirs_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                sub = item.layout()
                while sub.count():
                    inner = sub.takeAt(0)
                    if inner.widget() is not None:
                        inner.widget().deleteLater()
                sub.deleteLater()

        dirs = self._config.rom_dirs if self._config else []
        if not dirs:
            empty = CaptionLabel(t("rom.no_dirs"), self._dirs_card)
            empty.setStyleSheet(f"color:{theme.text_muted()};")
            self._dirs_lay.addWidget(empty)
            return

        for d in dirs:
            row = QHBoxLayout()
            row.setSpacing(theme.GAP_SM)
            lbl = BodyLabel(d, self._dirs_card)
            lbl.setToolTip(d)
            row.addWidget(lbl, 1)
            remove = TransparentToolButton(FIF.DELETE, self._dirs_card)
            remove.setFixedSize(28, 28)
            remove.clicked.connect(lambda _=False, p=d: self._on_remove_dir(p))
            row.addWidget(remove, 0)
            self._dirs_lay.addLayout(row)

    def _on_add_dir(self) -> None:
        if self._config is None:
            return
        path = QFileDialog.getExistingDirectory(self, t("rom.add_dir"))
        if not path:
            return
        dirs = self._config.rom_dirs
        if path not in dirs:
            self._config.set("rom_dirs", dirs + [path])
        self._refresh_dirs()

    def _on_remove_dir(self, path: str) -> None:
        if self._config is None:
            return
        dirs = [d for d in self._config.rom_dirs if d != path]
        self._config.set("rom_dirs", dirs)
        self._refresh_dirs()

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _on_scan(self) -> None:
        if self._config is None:
            return
        if not self._config.rom_dirs:
            InfoBar.warning(
                title=t("common.warning"),
                content=t("rom.no_dirs"),
                parent=self, position=InfoBarPosition.TOP, duration=3000,
            )
            return

        self._scan_btn.setEnabled(False)
        self._add_dir_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.show()
        self._progress.show()
        self._status_msg.setText(t("rom.scanning"))

        self._worker = _RomScanWorker(RomLibrary(self._config), self._saves, self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _on_cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._cancel_btn.setEnabled(False)
            self._status_msg.setText(t("common.canceling"))

    def _on_progress(self, done: int, total: int) -> None:
        self._status_msg.setText(
            t("rom.hashing", done=str(done), total=str(total)))

    def _on_scan_finished(self, report: RomLibraryReport) -> None:
        self._report = report
        self._scan_btn.setEnabled(True)
        self._add_dir_btn.setEnabled(True)
        self._cancel_btn.hide()
        self._progress.hide()

        platforms = {r.platform for r in report.roms}
        summary = t(
            "rom.summary",
            count=str(len(report.roms)),
            platforms=str(len(platforms)),
            dups=str(report.duplicate_count),
            orphans=str(len(report.saves_without_roms)),
        )
        if report.dat_games:
            summary += t("rom.summary_dat", verified=str(report.verified_count))
        if report.repaired_count:
            summary += t("rom.summary_repaired",
                         repaired=str(report.repaired_count))
        self._status_msg.setText(summary)
        self._populate_table(report)
        self._populate_orphans(report)

    def _on_scan_error(self, error: str) -> None:
        self._scan_btn.setEnabled(True)
        self._add_dir_btn.setEnabled(True)
        self._cancel_btn.hide()
        self._progress.hide()
        self._status_msg.setText("")
        InfoBar.error(
            title=t("common.error"),
            content=error,
            parent=self, position=InfoBarPosition.TOP, duration=5000,
        )

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    def _populate_table(self, report: RomLibraryReport) -> None:
        dup_paths: set[Path] = {
            r.path for group in report.duplicate_groups for r in group
        }
        roms = sorted(report.roms, key=lambda r: (r.platform, r.path.name.lower()))

        self._table.setRowCount(len(roms))
        for row, rom in enumerate(roms):
            status_parts = []
            if rom.repaired:
                status_parts.append(t("rom.status_repaired"))
            elif rom.dat_name:
                status_parts.append(t("rom.status_verified"))
            if rom.path in dup_paths:
                status_parts.append(t("rom.status_dup"))
            if rom.path in report.matched:
                status_parts.append(t("rom.status_linked"))

            name_item = QTableWidgetItem(rom.path.name)
            name_item.setToolTip(str(rom.path))
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, QTableWidgetItem(rom.platform))
            size_item = QTableWidgetItem(_fmt_size(rom.size))
            size_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 2, size_item)
            self._table.setItem(row, 3, QTableWidgetItem(rom.crc32 or "—"))
            status_item = QTableWidgetItem(" · ".join(status_parts))
            if rom.dat_name:
                status_item.setToolTip(rom.dat_name)
            self._table.setItem(row, 4, status_item)
        self._table.show()

    def _populate_orphans(self, report: RomLibraryReport) -> None:
        orphans = report.saves_without_roms
        if not orphans:
            self._orphan_title.hide()
            self._orphan_hint.hide()
            self._orphan_body.hide()
            return
        lines = [
            f"• {s.game_name} ({s.emulator} / {s.platform})" if s.platform
            else f"• {s.game_name} ({s.emulator})"
            for s in orphans
        ]
        self._orphan_body.setText("\n".join(lines))
        self._orphan_title.show()
        self._orphan_hint.show()
        self._orphan_body.show()
