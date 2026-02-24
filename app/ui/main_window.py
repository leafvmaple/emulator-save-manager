"""Main application window using PySide6-Fluent-Widgets FluentWindow."""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from qfluentwidgets import (
    FluentWindow,
    NavigationItemPosition,
    FluentIcon as FIF,
    setTheme,
    Theme,
)

from app.config import Config
from app.i18n import t
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
        self._init_window()
        self._init_pages()
        self._apply_theme()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _init_window(self) -> None:
        self.setWindowTitle(t("app.name"))
        self.setMinimumSize(QSize(960, 640))
        self.resize(1100, 720)

        # Center on screen
        desktop = QApplication.primaryScreen().availableGeometry()
        x = (desktop.width() - self.width()) // 2
        y = (desktop.height() - self.height()) // 2
        self.move(x, y)

    def _init_pages(self) -> None:
        # Create pages
        self.scan_page = ScanPage(self)
        self.backup_page = BackupPage(self)
        self.restore_page = RestorePage(self)
        self.sync_page = SyncPage(self)
        self.settings_page = SettingsPage(self)

        # Add to navigation
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

    def _apply_theme(self) -> None:
        theme_str = self._cfg.theme
        if theme_str == "dark":
            setTheme(Theme.DARK)
        elif theme_str == "light":
            setTheme(Theme.LIGHT)
        else:
            setTheme(Theme.AUTO)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def refresh_titles(self) -> None:
        """Refresh all navigation labels after a language change."""
        self.setWindowTitle(t("app.name"))
        # Page titles are refreshed internally by each page

    def get_config(self) -> Config:
        return self._cfg
