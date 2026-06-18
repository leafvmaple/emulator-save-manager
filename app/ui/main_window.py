"""Main application window using PySide6-Fluent-Widgets FluentWindow."""

from __future__ import annotations

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QPalette, QIcon
from PySide6.QtWidgets import QApplication

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
        self._init_window()
        self._init_pages()
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
    # Public
    # ------------------------------------------------------------------

    def refresh_titles(self) -> None:
        """Refresh all navigation labels after a language change."""
        self.setWindowTitle(t("app.name"))
        # Page titles are refreshed internally by each page

    def get_config(self) -> Config:
        return self._cfg

    # ------------------------------------------------------------------
    # Auto-backup
    # ------------------------------------------------------------------

    def start_auto_backup_cycle(self) -> None:
        """Scan, then auto-backup changed saves once the scan finishes."""
        self._auto_backup_pending = True
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
