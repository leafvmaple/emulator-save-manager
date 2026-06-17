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

    # ---- Self-test: verify bundled resources actually loaded ----
    # A packaging mistake can produce a binary that launches but has no
    # translations or plugins (see app/i18n + app/plugins collection). The
    # release workflow runs `--selftest` against the built binary and fails
    # the build if this returns non-zero, so a hollow build never ships.
    if "--selftest" in sys.argv:
        _selftest(pm)

    # ---- 5. Core services ----
    scanner = Scanner(pm, config)
    backup_mgr = BackupManager(config)
    restore_mgr = RestoreManager()
    restore_mgr.set_scanner(scanner)
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
    window.settings_page.set_plugin_manager(pm)

    # Connect scan → backup page
    window.scan_page.saves_updated.connect(window.backup_page.update_saves)

    window.show()
    logger.info("Window shown, entering event loop")

    # ---- 8. Optional auto-start actions ----
    if config.auto_backup_on_start:
        logger.info("Auto-backup on start enabled")
        window.start_auto_backup_cycle()  # scans, then backs up changed saves
    elif config.auto_scan_on_start:
        logger.info("Auto-scan on start enabled")
        window.scan_page.start_scan()
    if config.auto_sync_on_start and sync_mgr.is_configured:
        logger.info("Auto-sync on start enabled")
        window.sync_page.start_sync()

    sys.exit(app.exec())


def _selftest(pm: "PluginManager") -> None:
    """Verify bundled resources loaded, then exit (0 = OK, 1 = broken).

    Writes the result to the path following ``--selftest`` (or
    ``selftest_result.txt``) so a windowed build with no stdout can still be
    checked by CI.
    """
    from app.i18n import t

    i18n_ok = t("scan.title") != "scan.title"   # real translation, not the key
    plugin_count = len(pm.get_all_plugins())
    ok = i18n_ok and plugin_count > 0
    result = "OK" if ok else f"FAIL i18n={i18n_ok} plugins={plugin_count}"

    out = "selftest_result.txt"
    idx = sys.argv.index("--selftest")
    if idx + 1 < len(sys.argv):
        out = sys.argv[idx + 1]
    try:
        Path(out).write_text(result, encoding="utf-8")
    except OSError:
        pass
    logger.info("Selftest: {}", result)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
