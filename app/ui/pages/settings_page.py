"""Settings page — application preferences with Fluent-style cards.

Wraps all groups in a SmoothScrollArea so content is scrollable when the
window is small.  Each setting card has a meaningful description line.
An *About* section is added at the bottom.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QSizePolicy, QLabel,
)
from qfluentwidgets import (
    SubtitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    SettingCardGroup, ComboBoxSettingCard, PushSettingCard,
    RangeSettingCard, SwitchSettingCard, FluentIcon as FIF,
    InfoBar, InfoBarPosition, setTheme, Theme,
    CardWidget, SmoothScrollArea, IconWidget, setFont,
    TransparentToolButton,
)
from qfluentwidgets import (
    ConfigItem, OptionsConfigItem, RangeConfigItem,
    BoolValidator, OptionsValidator, RangeValidator, QConfig,
)
from PySide6.QtGui import QFont
from loguru import logger

from app.i18n import t, set_language
from app.config import Config
from app.core.game_icon import get_plugin_icon


# -----------------------------------------------------------------------
# QConfig wrapper (unchanged)
# -----------------------------------------------------------------------

class _AppQConfig(QConfig):
    """Thin wrapper connecting app config to qfluentwidgets' QConfig system."""

    language = OptionsConfigItem(
        "General", "Language", "zh_CN",
        OptionsValidator(["zh_CN", "en_US", "ja_JP"]),
    )
    theme_mode = OptionsConfigItem(
        "General", "ThemeMode", "auto",
        OptionsValidator(["auto", "light", "dark"]),
    )
    max_backups = RangeConfigItem(
        "Backup", "MaxBackups", 5, RangeValidator(1, 50),
    )
    auto_scan = ConfigItem(
        "General", "AutoScan", False, BoolValidator(),
    )
    auto_sync = ConfigItem(
        "General", "AutoSync", False, BoolValidator(),
    )


_app_qconfig = _AppQConfig()


# -----------------------------------------------------------------------
# About card
# -----------------------------------------------------------------------

class _AboutCard(CardWidget):
    """Simple card showing app name, version and description."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(100)

        root = QHBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(16)

        icon = IconWidget(FIF.INFO, self)
        icon.setFixedSize(36, 36)
        root.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(4)
        col.setContentsMargins(0, 0, 0, 0)

        name = StrongBodyLabel(t("app.name"), self)
        setFont(name, 15, QFont.Weight.DemiBold)
        col.addWidget(name)

        ver = CaptionLabel(f"v{t('app.version')}", self)
        ver.setStyleSheet("color:#888;")
        col.addWidget(ver)

        desc = CaptionLabel(t("settings.about_desc"), self)
        desc.setStyleSheet("color:#666;")
        col.addWidget(desc)

        root.addLayout(col, 1)


class _EmulatorPathCard(CardWidget):
    """Card for configuring install paths for a single emulator.

    Shows the emulator icon + name, any configured paths as rows, and
    an Add button.  Each path row has a remove button.
    """

    path_changed = Signal()  # emitted whenever a path is added/removed

    def __init__(
        self,
        emulator_name: str,
        display_name: str,
        paths: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._emulator_name = emulator_name
        self._paths: list[str] = list(paths)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 14)
        root.setSpacing(10)

        # Header row: icon + name + description + add button
        header = QHBoxLayout()
        header.setSpacing(12)

        pm = get_plugin_icon(emulator_name, 28)
        if pm and not pm.isNull():
            icon_label = QLabel(self)
            icon_label.setFixedSize(28, 28)
            icon_label.setPixmap(pm)
            header.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            icon = IconWidget(FIF.GAME, self)
            icon.setFixedSize(28, 28)
            header.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.setContentsMargins(0, 0, 0, 0)
        title_label = StrongBodyLabel(
            t("settings.emulator_install_path", name=display_name), self,
        )
        setFont(title_label, 13, QFont.Weight.DemiBold)
        title_col.addWidget(title_label)
        desc_label = CaptionLabel(
            t("settings.emulator_install_path_desc"), self,
        )
        desc_label.setStyleSheet("color:#888;")
        title_col.addWidget(desc_label)
        header.addLayout(title_col, 1)

        add_btn = TransparentToolButton(FIF.ADD, self)
        add_btn.setFixedSize(32, 32)
        add_btn.setToolTip(t("settings.add_path"))
        add_btn.clicked.connect(self._on_add)
        header.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        root.addLayout(header)

        # Container for path rows
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(4)
        self._rows_layout.setContentsMargins(40, 0, 0, 0)  # indent
        root.addLayout(self._rows_layout)

        # Build existing path rows
        for p in self._paths:
            self._add_path_row(p)

        self._update_height()

    @property
    def emulator_name(self) -> str:
        return self._emulator_name

    @property
    def paths(self) -> list[str]:
        return list(self._paths)

    def _update_height(self) -> None:
        """Recalculate card height to fit content."""
        # header ~56px + 28px per path row + margins
        header_h = 56
        row_h = 28
        margins = 28  # top + bottom padding
        h = header_h + margins + row_h * len(self._paths)
        self.setFixedHeight(max(72, h))
        # Notify parent SettingCardGroup to re-layout
        if self.parent() and hasattr(self.parent(), 'adjustSize'):
            self.parent().adjustSize()

    def _add_path_row(self, path: str) -> None:
        """Add a visual row for one path."""
        row = QHBoxLayout()
        row.setSpacing(8)

        icon = IconWidget(FIF.FOLDER, self)
        icon.setFixedSize(16, 16)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

        label = CaptionLabel(path, self)
        label.setStyleSheet("color:#555;")
        row.addWidget(label, 1)

        remove_btn = TransparentToolButton(FIF.CLOSE, self)
        remove_btn.setFixedSize(24, 24)
        remove_btn.setToolTip(t("settings.remove_path"))
        remove_btn.clicked.connect(lambda checked, p=path: self._on_remove(p))
        row.addWidget(remove_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # Wrap in a widget so we can remove it later
        wrapper = QWidget(self)
        wrapper.setLayout(row)
        wrapper.setProperty("path", path)
        self._rows_layout.addWidget(wrapper)
        self._update_height()

    def _on_add(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            t("settings.emulator_install_path", name=self._emulator_name),
        )
        if not folder:
            return
        if folder in self._paths:
            return
        self._paths.append(folder)
        self._add_path_row(folder)
        self.path_changed.emit()

    def _on_remove(self, path: str) -> None:
        if path in self._paths:
            self._paths.remove(path)
        # Remove the widget row
        for i in range(self._rows_layout.count()):
            item = self._rows_layout.itemAt(i)
            if item and item.widget() and item.widget().property("path") == path:
                w = item.widget()
                self._rows_layout.removeWidget(w)
                w.deleteLater()
                break
        self._update_height()
        self.path_changed.emit()


# -----------------------------------------------------------------------
# Page
# -----------------------------------------------------------------------

class SettingsPage(QWidget):
    """Settings page with Fluent-style setting cards inside a scroll area."""

    language_changed = Signal(str)
    theme_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settings_page")
        self._config: Config | None = None
        self._init_ui()

    def set_config(self, config: Config) -> None:
        self._config = config
        self._sync_from_config()

    def set_plugin_manager(self, pm) -> None:
        """Populate emulator path cards from registered plugins."""
        from app.plugins.plugin_manager import PluginManager

        self._pm: PluginManager = pm
        for plugin in pm.get_all_plugins():
            paths: list[str] = []
            if self._config:
                paths = self._config.get_emulator_install_paths(plugin.name)
            card = _EmulatorPathCard(
                emulator_name=plugin.name,
                display_name=plugin.name.capitalize(),
                paths=paths,
                parent=self._emu_path_group,
            )
            card.path_changed.connect(
                lambda c=card: self._on_emu_path_changed(c)
            )
            self._emu_path_group.addSettingCard(card)
            self._emu_path_cards.append(card)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(20)

        # Title
        title = SubtitleLabel(t("settings.title"), container)
        desc = BodyLabel(t("settings.description"), container)
        desc.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(desc)

        # --- General group ---
        general_group = SettingCardGroup(t("settings.general_group"), container)

        self._lang_card = ComboBoxSettingCard(
            _app_qconfig.language,
            FIF.LANGUAGE,
            t("settings.language"),
            t("settings.language_desc"),
            ["中文", "English", "日本語"],
            parent=general_group,
        )
        general_group.addSettingCard(self._lang_card)

        self._theme_card = ComboBoxSettingCard(
            _app_qconfig.theme_mode,
            FIF.BRUSH,
            t("settings.theme"),
            t("settings.theme_desc"),
            [t("settings.theme_auto"), t("settings.theme_light"), t("settings.theme_dark")],
            parent=general_group,
        )
        general_group.addSettingCard(self._theme_card)

        layout.addWidget(general_group)

        # --- Backup group ---
        backup_group = SettingCardGroup(t("settings.backup_group"), container)

        self._backup_dir_card = PushSettingCard(
            t("settings.choose_dir"),
            FIF.FOLDER,
            t("settings.backup_dir"),
            t("settings.backup_dir_desc"),
            parent=backup_group,
        )
        self._backup_dir_card.clicked.connect(self._choose_backup_dir)
        backup_group.addSettingCard(self._backup_dir_card)

        self._max_backup_card = RangeSettingCard(
            _app_qconfig.max_backups,
            FIF.HISTORY,
            t("settings.max_backups"),
            t("settings.max_backups_desc"),
            parent=backup_group,
        )
        backup_group.addSettingCard(self._max_backup_card)

        layout.addWidget(backup_group)

        # --- Sync group ---
        sync_group = SettingCardGroup(t("settings.sync_group"), container)

        self._sync_dir_card = PushSettingCard(
            t("settings.choose_dir"),
            FIF.SYNC,
            t("settings.sync_dir"),
            t("settings.sync_dir_desc"),
            parent=sync_group,
        )
        self._sync_dir_card.clicked.connect(self._choose_sync_dir)
        sync_group.addSettingCard(self._sync_dir_card)

        self._auto_scan_card = SwitchSettingCard(
            FIF.SEARCH,
            t("settings.auto_scan"),
            t("settings.auto_scan_desc"),
            _app_qconfig.auto_scan,
            parent=sync_group,
        )
        sync_group.addSettingCard(self._auto_scan_card)

        self._auto_sync_card = SwitchSettingCard(
            FIF.SYNC,
            t("settings.auto_sync"),
            t("settings.auto_sync_desc"),
            _app_qconfig.auto_sync,
            parent=sync_group,
        )
        sync_group.addSettingCard(self._auto_sync_card)

        layout.addWidget(sync_group)

        # --- Emulator paths group ---
        self._emu_path_group = SettingCardGroup(
            t("settings.emulator_paths_group"), container,
        )
        self._emu_path_group.vBoxLayout.setSpacing(6)
        layout.addWidget(self._emu_path_group)
        self._emu_path_cards: list[_EmulatorPathCard] = []

        # --- About ---
        about_group = SettingCardGroup(t("settings.about"), container)
        self._about_card = _AboutCard(about_group)
        about_group.addSettingCard(self._about_card)
        layout.addWidget(about_group)

        layout.addStretch()

        scroll.setWidget(container)
        outer.addWidget(scroll)

        # Connect signals
        _app_qconfig.language.valueChanged.connect(self._on_language_changed)
        _app_qconfig.theme_mode.valueChanged.connect(self._on_theme_changed)
        _app_qconfig.max_backups.valueChanged.connect(self._on_max_backups_changed)
        _app_qconfig.auto_scan.valueChanged.connect(
            lambda v: self._save("auto_scan_on_start", v)
        )
        _app_qconfig.auto_sync.valueChanged.connect(
            lambda v: self._save("auto_sync_on_start", v)
        )

    # ------------------------------------------------------------------
    # Sync config → UI
    # ------------------------------------------------------------------

    def _sync_from_config(self) -> None:
        if self._config is None:
            return
        bp = self._config.backup_path
        self._backup_dir_card.setContent(str(bp))
        sf = self._config.sync_folder
        if sf and sf.exists():
            self._sync_dir_card.setContent(str(sf))

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_language_changed(self, value: str) -> None:
        lang_map = {"zh_CN": "zh_CN", "en_US": "en_US", "ja_JP": "ja_JP"}
        lang = lang_map.get(value, "zh_CN")
        self._save("language", lang)
        set_language(lang)
        self.language_changed.emit(lang)
        InfoBar.success(
            title=t("settings.save_success"),
            content=t("settings.language"),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2000,
        )

    def _on_theme_changed(self, value: str) -> None:
        self._save("theme", value)
        if value == "dark":
            setTheme(Theme.DARK)
        elif value == "light":
            setTheme(Theme.LIGHT)
        else:
            setTheme(Theme.AUTO)
        self.theme_changed.emit(value)

    def _on_max_backups_changed(self, value: int) -> None:
        self._save("max_backups", value)

    def _choose_backup_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("settings.backup_dir"))
        if folder:
            self._save("backup_path", folder)
            self._backup_dir_card.setContent(folder)
            InfoBar.success(
                title=t("settings.save_success"),
                content=folder,
                parent=self,
                position=InfoBarPosition.TOP,
                duration=2000,
            )

    def _choose_sync_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("settings.sync_dir"))
        if folder:
            self._save("sync_folder", folder)
            self._sync_dir_card.setContent(folder)
            InfoBar.success(
                title=t("settings.save_success"),
                content=folder,
                parent=self,
                position=InfoBarPosition.TOP,
                duration=2000,
            )

    def _save(self, key: str, value: object) -> None:
        if self._config:
            self._config.set(key, value)

    def _on_emu_path_changed(self, card: _EmulatorPathCard) -> None:
        if self._config:
            self._config.set_emulator_install_paths(card.emulator_name, card.paths)
            InfoBar.success(
                title=t("settings.save_success"),
                content=t("settings.emulator_install_path", name=card.emulator_name),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=2000,
            )
