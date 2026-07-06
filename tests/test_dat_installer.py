"""DAT installer — validation, zip handling, family replacement."""

from __future__ import annotations

import zipfile

from app.core.dat_installer import DatInstallReport, dat_family, install_dats


def _dat_xml(system: str, n_entries: int) -> str:
    games = "".join(
        f'<game name="Game {i}"><rom name="g{i}.nes" crc="{i:08X}"/></game>'
        for i in range(1, n_entries + 1)
    )
    return (
        '<?xml version="1.0"?><datafile>'
        f"<header><name>{system}</name></header>{games}</datafile>"
    )


def test_dat_family_strips_export_date():
    assert dat_family("Nintendo - Game Boy (20260625-001404).dat") \
        == "Nintendo - Game Boy"
    # GB and GBC are distinct families even though both map to the
    # "Game Boy" platform label.
    assert dat_family("Nintendo - Game Boy Color (20260704-095041).dat") \
        == "Nintendo - Game Boy Color"
    assert dat_family("custom.dat") == "custom"


def test_install_plain_dat(tmp_path):
    src = tmp_path / "Nintendo - Nintendo Entertainment System (Headered) (20260704-141639).dat"
    src.write_text(_dat_xml("Nintendo - Nintendo Entertainment System (Headered)", 60),
                   encoding="utf-8")
    dat_dir = tmp_path / "dats"

    report = install_dats([src], dat_dir)
    assert report.ok
    assert (dat_dir / src.name).exists()
    assert report.installed[0].platform == "NES"
    assert report.installed[0].entries == 60


def test_install_from_downloaded_zip(tmp_path):
    inner = "Nintendo - Game Boy (20260625-001404).dat"
    z = tmp_path / "Nintendo - Game Boy (20260625-001404).zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(inner, _dat_xml("Nintendo - Game Boy", 55))
    dat_dir = tmp_path / "dats"

    report = install_dats([z], dat_dir)
    assert report.ok
    assert (dat_dir / inner).exists()


def test_newer_export_replaces_same_family_only(tmp_path):
    dat_dir = tmp_path / "dats"
    dat_dir.mkdir()
    old_gb = dat_dir / "Nintendo - Game Boy (20250101-000000).dat"
    old_gb.write_text(_dat_xml("Nintendo - Game Boy", 52), encoding="utf-8")
    gbc = dat_dir / "Nintendo - Game Boy Color (20260704-095041).dat"
    gbc.write_text(_dat_xml("Nintendo - Game Boy Color", 53), encoding="utf-8")

    src = tmp_path / "Nintendo - Game Boy (20260625-001404).dat"
    src.write_text(_dat_xml("Nintendo - Game Boy", 58), encoding="utf-8")
    report = install_dats([src], dat_dir)

    assert report.installed[0].replaced == [old_gb.name]
    assert not old_gb.exists()
    assert gbc.exists()  # different family — untouched despite same platform
    assert (dat_dir / src.name).exists()


def test_rejects_garbage_and_tiny_source_code_export(tmp_path):
    dat_dir = tmp_path / "dats"
    garbage = tmp_path / "junk.dat"
    garbage.write_bytes(b"NOT-XML-AT-ALL")
    # The real-world trap: a "Source Code" category export parses fine
    # but holds a couple of entries.
    tiny = tmp_path / "Source Code - Nintendo - Game Boy Color (20250313-191959).dat"
    tiny.write_text(_dat_xml("Source Code - Nintendo - Game Boy Color", 1),
                    encoding="utf-8")

    report = install_dats([garbage, tiny], dat_dir)
    assert not report.installed
    assert ("junk.dat", "unreadable") in report.errors
    assert (tiny.name, "too_small") in report.errors
    assert not list(dat_dir.glob("*.dat"))


def test_zip_without_dat_is_reported(tmp_path):
    z = tmp_path / "wrong.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "nothing here")
    report = install_dats([z], tmp_path / "dats")
    assert report.errors == [("wrong.zip", "no_dat_in_zip")]
    assert isinstance(report, DatInstallReport)
