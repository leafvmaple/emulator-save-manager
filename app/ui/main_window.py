"""Main application window using PySide6-Fluent-Widgets FluentWindow."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from qfluentwidgets import (
    FluentWindow,
    NavigationItemPosition,
    FluentIcon as FIF,
    qconfig,
    setTheme,
    setThemeColor,
    Theme,
)

from app.config import Config
from app.i18n import t
from app.assets import app_icon_path
from app.ui.pages.home_page import HomePage
from app.ui.pages.scan_page import ScanPage
from app.ui.pages.backup_page import BackupPage
from app.ui.pages.restore_page import RestorePage
from app.ui.pages.sync_page import SyncPage
from app.ui.pages.settings_page import SettingsPage


class MainWindow(FluentWindow):
    """Main Fluent-style window with sidebar navigation."""

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._cfg = config
        self._auto_backup_pending = False
        self._auto_timer: QTimer | None = None
        self._allow_close = False
        self._tray_notice_shown = False
        self._tray_icon: QSystemTrayIcon | None = None
        self._tray_menu: QMenu | None = None
        self._tray_show_action: QAction | None = None
        self._tray_backup_action: QAction | None = None
        self._tray_quit_action: QAction | None = None
        self._init_window()
        self._init_pages()
        self._setup_tray()
        self._apply_theme()
        self.scan_page.saves_updated.connect(self._on_saves_updated)
        # Custom-painted colours are resolved at build time, so re-apply them
        # (and the accent) whenever the theme changes at runtime.
        qconfig.themeChanged.connect(self._on_theme_refresh)
        self._setup_auto_backup_timer()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _init_window(self) -> None:
        self.setWindowTitle(t("app.name"))
        self.setWindowIcon(QIcon(str(app_icon_path())))
        self.setMinimumSize(QSize(960, 640))
        self.resize(1100, 720)

        # Win11 Mica backdrop — a no-op on platforms that don't support it.
        try:
            self.setMicaEffectEnabled(True)
        except Exception:  # pragma: no cover - platform/DWM dependent
            pass

        # The default expanded navigation rail (322px) is far too wide for our
        # short labels — tighten it so it doesn't eat the content area.
        self.navigationInterface.setExpandWidth(190)

        # Center on screen
        desktop = QApplication.primaryScreen().availableGeometry()
        x = (desktop.width() - self.width()) // 2
        y = (desktop.height() - self.height()) // 2
        self.move(x, y)

    def _init_pages(self) -> None:
        # Create pages
        self.home_page = HomePage(self._cfg, self)
        self.scan_page = ScanPage(self)
        self.backup_page = BackupPage(self)
        self.restore_page = RestorePage(self)
        self.sync_page = SyncPage(self)
        self.settings_page = SettingsPage(self)

        # Add to navigation — Home first, and the default landing page.
        self.addSubInterface(self.home_page, FIF.HOME, t("nav.home"))
        self.addSubInterface(self.scan_page, FIF.SEARCH, t("nav.scan"))
        self.addSubInterface(self.backup_page, FIF.SAVE, t("nav.backup"))
        self.addSubInterface(self.restore_page, FIF.HISTORY, t("nav.restore"))
        self.addSubInterface(self.sync_page, FIF.SYNC, t("nav.sync"))
        self.addSubInterface(
            self.settings_page,
            FIF.SETTING,
            t("nav.settings"),
            position=NavigationItemPosition.BOTTOM,
        )

        # Quick-action wiring from the Home dashboard + empty-state buttons.
        self.home_page.scan_requested.connect(self._on_home_scan)
        self.home_page.navigate_requested.connect(self._navigate_to)
        self.backup_page.scan_requested.connect(self._on_home_scan)
        self.switchTo(self.home_page)

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def _navigate_to(self, key: str) -> None:
        page = {
            "scan": self.scan_page,
            "backup": self.backup_page,
            "restore": self.restore_page,
            "sync": self.sync_page,
            "settings": self.settings_page,
        }.get(key)
        if page is not None:
            self.switchTo(page)

    def _on_home_scan(self) -> None:
        self.switchTo(self.scan_page)
        if not self.scan_page.is_scanning:
            self.scan_page.start_scan()

    def _apply_theme(self) -> None:
        theme_str = self._cfg.theme
        if theme_str == "dark":
            setTheme(Theme.DARK)
        elif theme_str == "light":
            setTheme(Theme.LIGHT)
        else:
            setTheme(Theme.AUTO)
        self._apply_system_accent()

    def _apply_system_accent(self) -> None:
        """Follow the OS accent colour (Qt 6.6+ exposes it via QPalette)."""
        try:
            accent = QApplication.palette().color(QPalette.ColorRole.Accent)
            if accent.isValid():
                setThemeColor(accent, save=False)
        except (AttributeError, Exception):  # pragma: no cover - Qt-version dependent
            pass

    def _on_theme_refresh(self, *_args) -> None:
        """Re-style custom-painted widgets after a runtime theme change."""
        self._apply_system_accent()
        for page in (self.home_page, self.scan_page, self.backup_page,
                     self.restore_page, self.sync_page):
            page.restyle()

    # ------------------------------------------------------------------
    # System tray
    # ------------------------------------------------------------------

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)

        self._tray_menu = QMenu(self)
        self._tray_show_action = self._tray_menu.addAction("")
        self._tray_show_action.triggered.connect(self.show_from_tray)
        self._tray_backup_action = self._tray_menu.addAction("")
        self._tray_backup_action.triggered.connect(self.start_auto_backup_cycle)
        self._tray_menu.addSeparator()
        self._tray_quit_action = self._tray_menu.addAction("")
        self._tray_quit_action.triggered.connect(self.quit_from_tray)

        self._tray_icon = QSystemTrayIcon(QIcon(str(app_icon_path())), self)
        self._tray_icon.setContextMenu(self._tray_menu)
        self._tray_icon.activated.connect(self._on_tray_activated)
        self._refresh_tray_text()
        self._tray_icon.show()

    def _refresh_tray_text(self) -> None:
        if self._tray_icon is not None:
            self._tray_icon.setToolTip(t("app.name"))
        if self._tray_show_action is not None:
            self._tray_show_action.setText(t("tray.show"))
        if self._tray_backup_action is not None:
            self._tray_backup_action.setText(t("tray.auto_backup_now"))
        if self._tray_quit_action is not None:
            self._tray_quit_action.setText(t("tray.quit"))

    def _is_tray_enabled(self) -> bool:
        return self._tray_icon is not None and self._tray_icon.isVisible()

    def _hide_to_tray(self) -> bool:
        if not self._is_tray_enabled():
            return False

        self.hide()
        if not self._tray_notice_shown and QSystemTrayIcon.supportsMessages():
            self._tray_icon.showMessage(
                t("tray.still_running_title"),
                t("tray.still_running_desc"),
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
            self._tray_notice_shown = True
        return True

    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_from_tray(self) -> None:
        self._allow_close = True
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _on_tray_activated(
        self,
        reason: QSystemTrayIcon.ActivationReason,
    ) -> None:
        show_reasons = (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        )
        if reason in show_reasons:
            self.show_from_tray()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0, self._hide_to_tray)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._allow_close or not self._is_tray_enabled():
            super().closeEvent(event)
            return

        event.ignore()
        self._hide_to_tray()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def refresh_titles(self) -> None:
        """Refresh all navigation labels after a language change."""
        self.setWindowTitle(t("app.name"))
        self._refresh_tray_text()
        # Page titles are refreshed internally by each page

    def get_config(self) -> Config:
        return self._cfg

    # ------------------------------------------------------------------
    # Auto-backup
    # ------------------------------------------------------------------

    def start_auto_backup_cycle(self) -> None:
        """Scan, then auto-backup changed saves once the scan finishes."""
        self._auto_backup_pending = True
        if self.scan_page.is_scanning:
            return
        self.scan_page.start_scan()

    def _setup_auto_backup_timer(self) -> None:
        minutes = self._cfg.auto_backup_interval_minutes
        if minutes > 0:
            self._auto_timer = QTimer(self)
            self._auto_timer.setInterval(minutes * 60 * 1000)
            self._auto_timer.timeout.connect(self._on_auto_timer)
            self._auto_timer.start()

    def _on_auto_timer(self) -> None:
        # Skip this tick if a scan is already running (e.g. a manual one).
        if self.scan_page.is_scanning:
            return
        self.start_auto_backup_cycle()

    def _on_saves_updated(self, saves: list) -> None:
        self.home_page.update_stats(self.scan_page.get_emulators(), saves)
        if self._auto_backup_pending:
            self._auto_backup_pending = False
            self.backup_page.auto_backup(saves)
