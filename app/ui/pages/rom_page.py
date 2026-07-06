"""ROM library page — read-only library view (ROM Stage A).

Scans the configured ROM directories in a background thread, then shows
the library with duplicate dumps and save links highlighted.  Strictly
read-only: the page never moves, renames or deletes ROM files.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QHeaderView, QTableWidgetItem,
    QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, CheckBox, IconWidget, InfoBar,
    InfoBarPosition, MessageBoxBase, PrimaryPushButton, ProgressRing,
    PushButton, SmoothScrollArea, StrongBodyLabel, SubtitleLabel,
    TableWidget, TransparentToolButton,
    FluentIcon as FIF,
)

from app.config import Config
from app.core.backup import BackupManager
from app.core.rename_engine import GameRename, RenameEngine, RenamePlan, RenameResult
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


class _RenameWorker(QThread):
    """Background thread that applies a rename plan."""

    finished = Signal(object)  # RenameResult
    error = Signal(str)

    def __init__(self, engine: RenameEngine, plan: RenamePlan,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._plan = plan

    def run(self) -> None:
        try:
            self.finished.emit(self._engine.execute_plan(self._plan))
        except Exception as e:
            self.error.emit(str(e))


class _RenameDialog(MessageBoxBase):
    """Dry-run preview — pick which normalizations / moves to apply."""

    def __init__(
        self,
        plan: RenamePlan,
        parent: QWidget | None = None,
        title: str = "",
        desc: str = "",
    ) -> None:
        super().__init__(parent)
        self._checks: list[tuple[GameRename, CheckBox]] = []

        self.titleLabel = SubtitleLabel(title or t("rom.rename_title"), self)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(
            BodyLabel(desc or t("rom.rename_desc"), self))

        inner = QWidget(self)
        box = QVBoxLayout(inner)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        for item in plan.items:
            cb = CheckBox(self._item_label(item), inner)
            cb.setChecked(True)
            self._checks.append((item, cb))
            box.addWidget(cb)
            details = []
            if item.save_renames:
                details.append(t("rom.rename_saves_n",
                                 count=str(len(item.save_renames))))
            if item.backup_emulators:
                details.append(t("rom.rename_chain",
                                 emus=", ".join(item.backup_emulators)))
            if details:
                cap = CaptionLabel("      " + " · ".join(details), inner)
                cap.setStyleSheet(f"color:{theme.text_muted()};")
                box.addWidget(cap)
        for item in plan.skipped:
            cap = CaptionLabel(
                f"⊘ {item.rom.path.name} — {item.skip_reason}", inner)
            cap.setStyleSheet(f"color:{theme.text_muted()};")
            box.addWidget(cap)
        box.addStretch()

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }")
        scroll.setMaximumHeight(320)
        self.viewLayout.addWidget(scroll)

        self.yesButton.setText(t("rom.rename_execute"))
        self.cancelButton.setText(t("common.cancel"))
        self.widget.setMinimumWidth(560)

    @staticmethod
    def _item_label(item: GameRename) -> str:
        if item.target_path is not None:
            dest = f"{item.new_rom_path.parent.name}/{item.new_name}"
            return f"{item.rom.path.name}  →  {dest}"
        return f"{item.rom.path.name}  →  {item.new_name}"

    @property
    def selected_items(self) -> list[GameRename]:
        return [item for item, cb in self._checks if cb.isChecked()]


class RomPage(QWidget):
    """ROM library view — analysis, DAT verification, save-aware renames."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("rom_page")
        self._config: Config | None = None
        self._backup_mgr: BackupManager | None = None
        self._saves: list[GameSave] = []
        self._worker: _RomScanWorker | None = None
        self._rename_worker: _RenameWorker | None = None
        self._report: RomLibraryReport | None = None
        self._init_ui()

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def set_config(self, config: Config) -> None:
        self._config = config
        self._refresh_dirs()
        # Parse-and-summarize off the startup path; local parses are
        # cached process-wide after the first pass.
        QTimer.singleShot(50, self._refresh_dat_summary)

    def set_backup_manager(self, backup_mgr: BackupManager) -> None:
        self._backup_mgr = backup_mgr

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

        # DAT database card — install status + BIOS-style import flow
        self._dat_card = CardWidget(self)
        dat_lay = QVBoxLayout(self._dat_card)
        dat_lay.setContentsMargins(20, 16, 20, 16)
        dat_lay.setSpacing(theme.GAP_SM)

        dat_head = QHBoxLayout()
        dat_head.setSpacing(theme.GAP_SM)
        dat_ic = IconWidget(FIF.DICTIONARY, self._dat_card)
        dat_ic.setFixedSize(18, 18)
        dat_head.addWidget(dat_ic, 0, Qt.AlignmentFlag.AlignVCenter)
        dat_head.addWidget(StrongBodyLabel(t("rom.dat_title"), self._dat_card),
                           0, Qt.AlignmentFlag.AlignVCenter)
        dat_head.addStretch()
        self._dat_import_btn = PushButton(
            FIF.DOWNLOAD, t("rom.dat_import"), self._dat_card)
        self._dat_import_btn.clicked.connect(self._on_import_dat)
        dat_head.addWidget(self._dat_import_btn, 0,
                           Qt.AlignmentFlag.AlignVCenter)
        dat_lay.addLayout(dat_head)

        self._dat_summary = CaptionLabel(t("rom.dat_none"), self._dat_card)
        self._dat_summary.setStyleSheet(f"color:{theme.text_muted()};")
        self._dat_summary.setWordWrap(True)
        dat_lay.addWidget(self._dat_summary)
        layout.addWidget(self._dat_card)

        # Action bar
        av = Qt.AlignmentFlag.AlignVCenter
        action_bar = QHBoxLayout()
        action_bar.setSpacing(theme.GAP_SM)

        self._scan_btn = PrimaryPushButton(FIF.SEARCH, t("rom.scan"), self)
        self._scan_btn.clicked.connect(self._on_scan)
        action_bar.addWidget(self._scan_btn, 0, av)

        self._skeleton_btn = PushButton(FIF.SAVE_AS, t("rom.export_skeleton"), self)
        self._skeleton_btn.clicked.connect(self._on_export_skeleton)
        self._skeleton_btn.setEnabled(False)  # needs a completed scan
        action_bar.addWidget(self._skeleton_btn, 0, av)

        self._rename_btn = PushButton(FIF.EDIT, t("rom.rename_btn"), self)
        self._rename_btn.clicked.connect(self._on_normalize)
        self._rename_btn.setEnabled(False)  # needs a completed scan
        action_bar.addWidget(self._rename_btn, 0, av)

        self._sort_btn = PushButton(FIF.LIBRARY, t("rom.sort_btn"), self)
        self._sort_btn.clicked.connect(self._on_sort)
        self._sort_btn.setEnabled(False)  # needs a completed scan
        action_bar.addWidget(self._sort_btn, 0, av)

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

    # ------------------------------------------------------------------
    # DAT database
    # ------------------------------------------------------------------

    def _refresh_dat_summary(self) -> None:
        if self._config is None:
            return
        from app.core.dat_index import load_dat_index
        index = load_dat_index(self._config.dat_dir)
        if not index.sources:
            self._dat_summary.setText(t("rom.dat_none"))
            return
        platforms = sorted(
            {g.platform for g in index.by_crc.values() if g.platform})
        self._dat_summary.setText(t(
            "rom.dat_summary",
            count=str(len(index.sources)),
            games=f"{index.game_count:,}",
            platforms=" / ".join(platforms) or "?",
        ))

    def _on_import_dat(self) -> None:
        """BIOS-install style flow: pick DATs (or their zips), validate,
        place into the DAT directory, retire older exports."""
        if self._config is None:
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, t("rom.dat_pick"), "",
            "No-Intro DAT (*.dat *.zip);;All files (*)")
        if not files:
            return
        from app.core.dat_installer import install_dats
        report = install_dats([Path(f) for f in files],
                              self._config.dat_dir)

        lines = []
        for d in report.installed:
            line = t("rom.dat_installed_line",
                     platform=d.platform or "?",
                     entries=f"{d.entries:,}", name=d.name)
            if d.replaced:
                line += t("rom.dat_replaced", old=", ".join(d.replaced))
            lines.append(line)
        for name, kind in report.errors:
            lines.append(t(f"rom.dat_err_{kind}", name=name))

        content = "\n".join(lines)
        if report.installed and not report.errors:
            InfoBar.success(title=t("rom.dat_import"), content=content,
                            parent=self, position=InfoBarPosition.TOP,
                            duration=8000)
        elif report.installed:
            InfoBar.warning(title=t("rom.dat_import"), content=content,
                            parent=self, position=InfoBarPosition.TOP,
                            duration=10000)
        else:
            InfoBar.error(title=t("rom.dat_import"), content=content,
                          parent=self, position=InfoBarPosition.TOP,
                          duration=10000)
        self._refresh_dat_summary()

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

    def _on_export_skeleton(self) -> None:
        """Draft custom-DB entries for every unidentified scanned ROM."""
        if self._report is None or self._config is None:
            return
        from app.core.custom_db import build_skeleton, merge_skeleton
        from app.core.rom_library import RomLibrary

        db_path = RomLibrary(self._config).custom_db_path
        added = merge_skeleton(db_path, build_skeleton(self._report.roms))
        if added:
            InfoBar.success(
                title=t("rom.export_skeleton"),
                content=t("rom.skeleton_done",
                          count=str(added), file=str(db_path)),
                parent=self, position=InfoBarPosition.TOP, duration=8000,
            )
        else:
            InfoBar.info(
                title=t("rom.export_skeleton"),
                content=t("rom.skeleton_none"),
                parent=self, position=InfoBarPosition.TOP, duration=4000,
            )

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
        if report.custom_count:
            summary += t("rom.summary_custom", custom=str(report.custom_count))
        if report.derived_count:
            summary += t("rom.summary_derived",
                         derived=str(report.derived_count))
        self._status_msg.setText(summary)
        self._skeleton_btn.setEnabled(True)
        self._rename_btn.setEnabled(self._backup_mgr is not None)
        self._sort_btn.setEnabled(self._backup_mgr is not None)
        if self._config is not None and self._config.library_dir:
            self._sort_btn.setToolTip(self._config.library_dir)
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
    # Save-aware normalization (Stage B)
    # ------------------------------------------------------------------

    def _on_normalize(self) -> None:
        """Preview and apply canonical renames with save/backup migration."""
        self._run_rename_flow(library_dir=None)

    def _on_sort(self) -> None:
        """Move every identified ROM into the library dir, renamed."""
        if self._config is None:
            return
        lib_dir = self._config.library_dir
        if not lib_dir:
            lib_dir = QFileDialog.getExistingDirectory(
                self, t("rom.sort_pick_dir"))
            if not lib_dir:
                return
            self._config.set("library_dir", lib_dir)
            self._sort_btn.setToolTip(lib_dir)
        self._run_rename_flow(library_dir=Path(lib_dir))

    def _run_rename_flow(self, library_dir: Path | None) -> None:
        if self._report is None or self._config is None \
                or self._backup_mgr is None:
            return
        sort_mode = library_dir is not None
        button_label = t("rom.sort_btn") if sort_mode else t("rom.rename_btn")
        engine = RenameEngine(self._config, self._backup_mgr)
        plan = engine.plan_renames(self._report.roms, self._saves,
                                   library_dir=library_dir)
        if not plan.items and not plan.skipped:
            InfoBar.info(
                title=button_label,
                content=t("rom.rename_none"),
                parent=self, position=InfoBarPosition.TOP, duration=4000,
            )
            return

        dlg = _RenameDialog(
            plan, self.window(),
            title=t("rom.sort_title") if sort_mode else t("rom.rename_title"),
            desc=t("rom.sort_desc") if sort_mode else t("rom.rename_desc"),
        )
        if not dlg.exec():
            return
        selected = dlg.selected_items
        if not selected:
            return

        self._rename_btn.setEnabled(False)
        self._sort_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._progress.show()
        self._status_msg.setText(t("rom.rename_running"))

        run_plan = RenamePlan(items=selected, skipped=[])
        self._rename_worker = _RenameWorker(engine, run_plan, self)
        self._rename_worker.finished.connect(self._on_rename_finished)
        self._rename_worker.error.connect(self._on_scan_error)
        self._rename_worker.start()

    def _on_rename_finished(self, result: RenameResult) -> None:
        self._progress.hide()
        self._scan_btn.setEnabled(True)
        summary = t(
            "rom.rename_done",
            roms=str(result.renamed_roms),
            saves=str(result.renamed_saves),
            chains=str(result.migrated_backups),
        )
        if result.errors:
            InfoBar.warning(
                title=t("rom.rename_errors_title"),
                content=summary + "\n" + "\n".join(result.errors[:5]),
                parent=self, position=InfoBarPosition.TOP, duration=10000,
            )
        else:
            InfoBar.success(
                title=t("rom.rename_btn"),
                content=summary,
                parent=self, position=InfoBarPosition.TOP, duration=5000,
            )
        # Paths changed on disk — refresh the library view.
        self._on_scan()

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
            elif rom.custom_name:
                status_parts.append(t("rom.status_custom"))
            elif rom.derived_from:
                status_parts.append(t("rom.status_derived"))
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
            elif rom.custom_name and rom.derived_from:
                status_item.setToolTip(
                    rom.custom_name + "\n"
                    + t("rom.derived_tooltip", base=rom.derived_from))
            elif rom.custom_name:
                status_item.setToolTip(rom.custom_name)
            elif rom.derived_from:
                status_item.setToolTip(
                    t("rom.derived_tooltip", base=rom.derived_from))
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
