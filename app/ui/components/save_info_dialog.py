"""Dialog showing plugin-extracted save-file info (key/value groups)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QGridLayout
from qfluentwidgets import (
    BodyLabel, CaptionLabel, MessageBoxBase, StrongBodyLabel, SubtitleLabel,
    setFont,
)

from app.i18n import t
from app.saveinfo import SaveInfoResult, get_save_info_manager
from app.ui import theme


class SaveInfoDialog(MessageBoxBase):
    """Shows what a save-info plugin extracted from one save file.

    Extraction runs in-process (plugins are pure Python parsers over small
    files), so the dialog fills synchronously in the constructor.
    """

    def __init__(self, path: Path, platform: str = "", parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        result = get_save_info_manager().extract(path, platform)
        self._init_content(path, result)

    def _init_content(self, path: Path, result: SaveInfoResult | None) -> None:
        title = (result.title if result and result.matched and result.title
                 else t("save_info.title"))
        self.viewLayout.addWidget(SubtitleLabel(title, self))

        file_label = CaptionLabel(str(path), self)
        file_label.setStyleSheet(f"color:{theme.text_muted()};")
        file_label.setWordWrap(True)
        self.viewLayout.addWidget(file_label)

        if result is None or (not result.matched and not result.reason):
            reason = (result.error if result and result.error
                      else t("save_info.not_recognized"))
            self.viewLayout.addWidget(BodyLabel(reason, self))
        elif not result.matched:
            self.viewLayout.addWidget(BodyLabel(result.reason, self))
        else:
            self._add_matched(result)

        if result is not None and result.plugin_name:
            src = CaptionLabel(t("save_info.source", name=result.plugin_name), self)
            src.setStyleSheet(f"color:{theme.text_muted()};")
            self.viewLayout.addWidget(src)

        self.yesButton.setText(t("common.confirm"))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(440)

    def _add_matched(self, result: SaveInfoResult) -> None:
        if result.summary:
            summary = BodyLabel(result.summary, self)
            summary.setStyleSheet(f"color:{theme.text_secondary()};")
            self.viewLayout.addWidget(summary)

        for warning in result.warnings:
            warn = CaptionLabel(f"⚠ {warning}", self)
            warn.setStyleSheet(f"color:{theme.error()};")
            warn.setWordWrap(True)
            self.viewLayout.addWidget(warn)

        for group in result.groups:
            header = StrongBodyLabel(group.label, self)
            setFont(header, 14, QFont.Weight.DemiBold)
            self.viewLayout.addWidget(header)

            grid = QGridLayout()
            grid.setContentsMargins(theme.GAP_SM, 0, 0, 0)
            grid.setHorizontalSpacing(theme.GAP_LG)
            grid.setVerticalSpacing(theme.GAP_XS)
            grid.setColumnStretch(1, 1)
            for row, item in enumerate(group.items):
                key = CaptionLabel(item.key, self)
                key.setStyleSheet(f"color:{theme.text_muted()};")
                key.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                grid.addWidget(key, row, 0)

                value = BodyLabel(item.value, self)
                value.setWordWrap(True)
                value.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                grid.addWidget(value, row, 1)
            self.viewLayout.addLayout(grid)
