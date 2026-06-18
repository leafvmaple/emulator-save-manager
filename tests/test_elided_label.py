from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QLabel

from app.ui.components.elided_label import ElidedCaptionLabel, ElidedStrongBodyLabel


def test_elided_label_shrinks_without_losing_full_text(qtbot):
    text = "Super Robot Taisen Original Generations Translation Copy Very Long Name"
    label = ElidedStrongBodyLabel(text)
    qtbot.addWidget(label)

    label.resize(90, 24)
    label.show()
    qtbot.wait(10)

    assert label.full_text == text
    assert label.toolTip() == text
    assert label.minimumSizeHint().width() == 0
    assert label.text() != text


def test_elided_caption_label_stays_inside_fixed_column(qtbot):
    text = "口袋妖怪(精灵宝可梦) 灵魂之银 官译修正版v1.4.0(简)(JP)(ACG汉化组+Xzonn)(1024Mb)"
    label = ElidedCaptionLabel(text)
    qtbot.addWidget(label)
    label.setFixedWidth(160)
    label.setStyleSheet("font-weight:600;")
    label.show()
    qtbot.wait(10)

    assert label.fontMetrics().horizontalAdvance(label.text()) <= label.contentsRect().width()
    assert label.text().endswith("…")


def test_scan_detail_columns_do_not_overlap(qtbot):
    from app.models.game_save import GameSave, SaveFile, SaveType
    from app.ui.pages.scan_page import _GameSaveCard

    long_name = "口袋妖怪(精灵宝可梦) 灵魂之银 官译修正版v1.4.0(简)(JP)(ACG汉化组+Xzonn)(1024Mb)"
    save = GameSave(
        emulator="melonDS",
        game_name=long_name,
        game_id=long_name,
        platform="NDS",
        save_files=[
            SaveFile(
                path=Path(f"D:/saves/{long_name}.dsv"),
                save_type=SaveType.BATTERY,
                size=512 * 1024,
                modified=datetime(2023, 11, 12, 1, 23),
            )
        ],
    )
    card = _GameSaveCard(long_name, [save], icon_provider=None)
    qtbot.addWidget(card)
    card.resize(820, 220)
    card.show()
    card._detail_widget.setVisible(True)
    card._detail_widget.setMaximumHeight(1000)
    card.layout().activate()
    card._detail_widget.layout().activate()
    qtbot.wait(20)

    file_label = card.findChild(ElidedCaptionLabel, "detailFileName")
    type_label = card.findChild(QLabel, "detailSaveType")
    header_type_label = card.findChild(QLabel, "detailHeaderSaveType")

    assert file_label is not None
    assert type_label is not None
    assert header_type_label is not None
    assert file_label.parentWidget() is type_label.parentWidget()
    assert file_label.geometry().right() < type_label.geometry().left()
    header_type_x = header_type_label.mapTo(
        card, header_type_label.rect().topLeft()
    ).x()
    row_type_x = type_label.mapTo(card, type_label.rect().topLeft()).x()
    assert header_type_x == row_type_x