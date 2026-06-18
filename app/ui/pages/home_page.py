"""Home page — a dashboard landing view.

Gives the app a proper front door (hero + at-a-glance stats + quick actions)
instead of dropping straight into the scan list, so it reads as an
application rather than a single-purpose tool.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel
from qfluentwidgets import (
    TitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    CardWidget, SimpleCardWidget, IconWidget, SmoothScrollArea,
    FluentIcon as FIF, setFont,
)

from app.i18n import t
from app.version import get_app_version
from app.assets import app_icon_path
from app.ui import theme


class _StatCard(SimpleCardWidget):
    """A small overview tile: an icon + label and one big value."""

    def __init__(self, icon, label: str, parent: QWidget | None = None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setMinimumWidth(150)
        self.setFixedHeight(92)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.GAP_LG, theme.GAP_MD, theme.GAP_LG, theme.GAP_MD)
        lay.setSpacing(theme.GAP_XS)

        top = QHBoxLayout()
        top.setSpacing(theme.GAP_SM)
        ic = IconWidget(icon, self)
        ic.setFixedSize(16, 16)
        top.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
        lbl = CaptionLabel(label, self)
        lbl.setStyleSheet(f"color:{theme.text_muted()};")
        top.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch()
        lay.addLayout(top)

        self._value = StrongBodyLabel("—", self)
        setFont(self._value, 24, QFont.Weight.DemiBold)
        lay.addWidget(self._value)
        lay.addStretch()

    def set_value(self, value: object) -> None:
        self._value.setText(str(value))


class _ActionCard(CardWidget):
    """A clickable quick-action tile: icon + title + description."""

    def __init__(self, icon, title: str, desc: str, parent: QWidget | None = None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setFixedHeight(76)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.GAP_LG, theme.GAP_MD, theme.GAP_LG, theme.GAP_MD)
        lay.setSpacing(theme.GAP_MD)

        ic = IconWidget(icon, self)
        ic.setFixedSize(26, 26)
        lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(theme.GAP_XS)
        col.setContentsMargins(0, 0, 0, 0)
        ttl = StrongBodyLabel(title, self)
        setFont(ttl, 14, QFont.Weight.DemiBold)
        col.addWidget(ttl)
        dsc = CaptionLabel(desc, self)
        dsc.setStyleSheet(f"color:{theme.text_muted()};")
        dsc.setWordWrap(True)
        col.addWidget(dsc)
        lay.addLayout(col, 1)

        chevron = IconWidget(FIF.CHEVRON_RIGHT, self)
        chevron.setFixedSize(14, 14)
        lay.addWidget(chevron, 0, Qt.AlignmentFlag.AlignVCenter)


class HomePage(QWidget):
    """Dashboard landing page with overview stats and quick actions."""

    scan_requested = Signal()
    navigate_requested = Signal(str)  # "backup" | "restore" | "sync" | "settings"

    def __init__(self, config, parent: QWidget | None = None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setObjectName("home_page")
        self._config = config
        self._backup_manager = None
        self._init_ui()

    def set_backup_manager(self, bm) -> None:  # noqa: ANN001
        self._backup_manager = bm
        self._update_dynamic()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _init_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        container = QWidget()
        container.setStyleSheet("background: transparent;")

        page = QVBoxLayout(container)
        page.setContentsMargins(theme.PAGE_MARGIN_H, theme.PAGE_MARGIN_V,
                                theme.PAGE_MARGIN_H, theme.PAGE_MARGIN_V)
        page.setSpacing(theme.GAP_LG)

        # --- Hero: logo + title / subtitle / version ---
        hero = QHBoxLayout()
        hero.setSpacing(theme.GAP_LG)
        logo = QLabel(container)
        logo.setFixedSize(56, 56)
        logo.setScaledContents(True)
        logo.setPixmap(QPixmap(str(app_icon_path())))
        hero.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)

        hero_text = QVBoxLayout()
        hero_text.setSpacing(theme.GAP_XS)
        hero_text.addWidget(TitleLabel(t("app.name"), container))
        sub = BodyLabel(t("home.subtitle"), container)
        sub.setStyleSheet(f"color:{theme.text_secondary()};")
        sub.setWordWrap(True)
        hero_text.addWidget(sub)
        ver = CaptionLabel(f"v{get_app_version()}", container)
        ver.setStyleSheet(f"color:{theme.text_muted()};")
        hero_text.addWidget(ver)
        hero.addLayout(hero_text, 1)
        page.addLayout(hero)

        # --- Overview stats ---
        page.addWidget(self._section_title(t("home.overview"), container))
        stats = QHBoxLayout()
        stats.setSpacing(theme.GAP_MD)
        self._stat_emu = _StatCard(FIF.GAME, t("home.stat_emulators"), container)
        self._stat_saves = _StatCard(FIF.SAVE, t("home.stat_saves"), container)
        self._stat_backups = _StatCard(FIF.HISTORY, t("home.stat_backups"), container)
        self._stat_sync = _StatCard(FIF.SYNC, t("home.stat_sync"), container)
        for c in (self._stat_emu, self._stat_saves, self._stat_backups, self._stat_sync):
            stats.addWidget(c, 1)
        page.addLayout(stats)

        # --- Quick actions ---
        page.addWidget(self._section_title(t("home.quick_actions"), container))
        grid = QGridLayout()
        grid.setSpacing(theme.GAP_MD)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        actions = [
            (FIF.SEARCH, "action_scan", lambda: self.scan_requested.emit()),
            (FIF.SAVE, "action_backup", lambda: self.navigate_requested.emit("backup")),
            (FIF.SYNC, "action_sync", lambda: self.navigate_requested.emit("sync")),
            (FIF.SETTING, "action_settings", lambda: self.navigate_requested.emit("settings")),
        ]
        for i, (icon, key, cb) in enumerate(actions):
            card = _ActionCard(icon, t(f"home.{key}"), t(f"home.{key}_desc"), container)
            card.clicked.connect(cb)
            grid.addWidget(card, i // 2, i % 2)
        page.addLayout(grid)
        page.addStretch()

        scroll.setWidget(container)
        outer.addWidget(scroll)

        self._update_dynamic()

    @staticmethod
    def _section_title(text: str, parent: QWidget) -> StrongBodyLabel:
        lbl = StrongBodyLabel(text, parent)
        setFont(lbl, 16, QFont.Weight.DemiBold)
        return lbl

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def update_stats(self, emulators: list, saves: list) -> None:
        """Refresh emulator/save counts after a scan completes."""
        self._stat_emu.set_value(len(emulators))
        games = {f"{s.emulator}:{s.game_id}" for s in saves}
        self._stat_saves.set_value(len(games))

    def _update_dynamic(self) -> None:
        # Backups
        count = 0
        if self._backup_manager is not None:
            try:
                count = len(self._backup_manager.list_all_backups())
            except Exception:  # pragma: no cover - defensive
                count = 0
        self._stat_backups.set_value(count)

        # Sync status
        if self._config.sync_backend == "webdav":
            label = (t("settings.sync_method_webdav")
                     if self._config.webdav_url else t("home.sync_off"))
        else:
            folder = str(self._config.sync_folder)
            label = (t("settings.sync_method_folder")
                     if folder not in ("", ".") else t("home.sync_off"))
        self._stat_sync.set_value(label)

    def refresh(self) -> None:
        self._update_dynamic()

    def showEvent(self, e) -> None:  # noqa: ANN001
        super().showEvent(e)
        self._update_dynamic()
