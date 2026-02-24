"""Conflict resolution dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel,
)
from qfluentwidgets import (
    MessageBoxBase, SubtitleLabel, BodyLabel, RadioButton,
    PrimaryPushButton, PushButton,
)

from app.i18n import t
from app.core.conflict import ConflictInfo, ConflictResolution


class ConflictDialog(MessageBoxBase):
    """Dialog for resolving a single sync conflict."""

    def __init__(self, conflict: ConflictInfo, parent=None) -> None:
        super().__init__(parent)
        self._conflict = conflict
        self._resolution = ConflictResolution.SKIP
        self._init_content()

    @property
    def resolution(self) -> ConflictResolution:
        return self._resolution

    def _init_content(self) -> None:
        self.titleLabel = SubtitleLabel(t("conflict.title"), self)
        self.viewLayout.addWidget(self.titleLabel)

        self.viewLayout.addWidget(BodyLabel(
            t("conflict.file_conflict", file=self._conflict.game_id),
            self,
        ))

        # Local info
        local_info = BodyLabel(
            f"{t('conflict.local_version')}: {self._conflict.local_mtime:%Y/%m/%d %H:%M}",
            self,
        )
        self.viewLayout.addWidget(local_info)

        # Remote info
        remote_info = BodyLabel(
            f"{t('conflict.remote_version')}: {self._conflict.remote_mtime:%Y/%m/%d %H:%M}",
            self,
        )
        self.viewLayout.addWidget(remote_info)

        # Options
        self._rb_local = RadioButton(t("conflict.use_local"), self)
        self._rb_remote = RadioButton(t("conflict.use_remote"), self)
        self._rb_both = RadioButton(t("conflict.keep_both"), self)
        self._rb_skip = RadioButton(t("conflict.skip"), self)
        self._rb_skip.setChecked(True)

        self.viewLayout.addWidget(self._rb_local)
        self.viewLayout.addWidget(self._rb_remote)
        self.viewLayout.addWidget(self._rb_both)
        self.viewLayout.addWidget(self._rb_skip)

        self.yesButton.setText(t("common.confirm"))
        self.cancelButton.setText(t("common.cancel"))

    def _validate(self) -> bool:
        if self._rb_local.isChecked():
            self._resolution = ConflictResolution.USE_LOCAL
        elif self._rb_remote.isChecked():
            self._resolution = ConflictResolution.USE_REMOTE
        elif self._rb_both.isChecked():
            self._resolution = ConflictResolution.KEEP_BOTH
        else:
            self._resolution = ConflictResolution.SKIP
        return True


class BatchConflictDialog(MessageBoxBase):
    """Dialog for resolving multiple conflicts at once."""

    def __init__(self, conflicts: list[ConflictInfo], parent=None) -> None:
        super().__init__(parent)
        self._conflicts = conflicts
        self._resolutions: dict[str, ConflictResolution] = {}
        self._global_resolution: ConflictResolution | None = None
        self._init_content()

    @property
    def resolutions(self) -> dict[str, ConflictResolution]:
        return self._resolutions

    def _init_content(self) -> None:
        self.titleLabel = SubtitleLabel(
            t("conflict.title") + f" ({len(self._conflicts)})", self,
        )
        self.viewLayout.addWidget(self.titleLabel)

        for c in self._conflicts[:10]:  # Show first 10
            self.viewLayout.addWidget(BodyLabel(
                f"• {c.game_id}  ({c.local_mtime:%m/%d %H:%M} vs {c.remote_mtime:%m/%d %H:%M})",
                self,
            ))
        if len(self._conflicts) > 10:
            self.viewLayout.addWidget(BodyLabel(
                f"... +{len(self._conflicts) - 10} more", self,
            ))

        self.viewLayout.addWidget(BodyLabel(t("conflict.apply_all"), self))

        self._rb_local = RadioButton(t("conflict.use_local"), self)
        self._rb_remote = RadioButton(t("conflict.use_remote"), self)
        self._rb_skip = RadioButton(t("conflict.skip"), self)
        self._rb_skip.setChecked(True)

        self.viewLayout.addWidget(self._rb_local)
        self.viewLayout.addWidget(self._rb_remote)
        self.viewLayout.addWidget(self._rb_skip)

        self.yesButton.setText(t("common.confirm"))
        self.cancelButton.setText(t("common.cancel"))

    def _validate(self) -> bool:
        if self._rb_local.isChecked():
            res = ConflictResolution.USE_LOCAL
        elif self._rb_remote.isChecked():
            res = ConflictResolution.USE_REMOTE
        else:
            res = ConflictResolution.SKIP
        self._resolutions = {c.game_id: res for c in self._conflicts}
        return True
