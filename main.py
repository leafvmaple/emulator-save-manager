"""Emulator Save Manager — entry point."""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from loguru import logger

from app.config import Config
from app.logger import setup_logger
from app.i18n import init as i18n_init
from app.plugins.plugin_manager import PluginManager
from app.core.scanner import Scanner
from app.core.backup import BackupManager
from app.core.restore import RestoreManager
from app.core.sync import SyncManager
from app.core.game_icon import GameIconProvider
from app.ui.main_window import MainWindow


def main() -> None:
    # ---- 1. Config ----
    config = Config()

    # ---- 2. Logger ----
    setup_logger()
    logger.info("Emulator Save Manager starting…")

    # ---- 3. i18n ----
    i18n_init(config.language)
    logger.info("Language: {}", config.language)

    # ---- 4. Plugin discovery ----
    pm = PluginManager()
    pm.discover()
    logger.info("Plugins loaded: {}", [p.name for p in pm.get_all_plugins()])

    # ---- 5. Core services ----
    scanner = Scanner(pm, config)
    backup_mgr = BackupManager(config)
    restore_mgr = RestoreManager()
    sync_mgr = SyncManager(config, backup_mgr)
    icon_provider = GameIconProvider(config.data_dir / "icons")

    # ---- 6. Qt Application ----
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough,
    )
    app = QApplication(sys.argv)

    # ---- 7. Main Window ----
    window = MainWindow(config)

    # Wire services into UI pages
    window.scan_page.set_scanner(scanner)
    window.scan_page.set_icon_provider(icon_provider)
    window.backup_page.set_backup_manager(backup_mgr)
    window.backup_page.set_icon_provider(icon_provider)
    window.restore_page.set_managers(backup_mgr, restore_mgr)
    window.restore_page.set_icon_provider(icon_provider)
    window.sync_page.set_sync_manager(sync_mgr)
    window.sync_page.set_config(config)
    window.settings_page.set_config(config)

    # Connect scan → backup page
    window.scan_page.saves_updated.connect(window.backup_page.update_saves)

    window.show()
    logger.info("Window shown, entering event loop")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
