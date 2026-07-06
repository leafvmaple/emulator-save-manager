"""DAT installer — validate user-picked DAT files, place them in dat_dir.

The product flow mirrors an emulator's BIOS-install dialog: the user
picks ``.dat`` files (or the ``.zip`` exactly as downloaded from
Dat-o-Matic), every candidate is parsed and validated *before* anything
lands in the DAT directory, and an older export of the same DAT family
is replaced — Dat-o-Matic filenames carry an export timestamp, so the
directory keeps exactly one DAT per family.

Validation guards against a real-world mistake: Dat-o-Matic's
"Source Code" category exports parse as valid XML but hold one or two
entries — installing one would silently cripple verification for that
platform, so suspiciously small DATs are rejected with a pointed error.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from app.core.dat_index import parse_dat

#: A real platform DAT has thousands of entries; a mis-picked special
#: export ("Source Code", …) has a handful.
MIN_ENTRIES = 50

_DATE_SUFFIX = re.compile(r"\s*\(\d{8}-\d{6}\)\s*$")


def dat_family(filename: str) -> str:
    """Family key of a DAT filename — the name minus the export date.

    ``Nintendo - Game Boy Color (20260704-095041)`` and a newer export
    of the same system share a family; GB and GBC do not, even though
    both map to the "Game Boy" platform label.
    """
    return _DATE_SUFFIX.sub("", Path(filename).stem).strip()


@dataclass
class InstalledDat:
    name: str
    platform: str
    entries: int
    replaced: list[str] = field(default_factory=list)


@dataclass
class DatInstallReport:
    installed: list[InstalledDat] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    """(filename, kind) — kind: 'unreadable' | 'too_small' | 'no_dat_in_zip'."""

    @property
    def ok(self) -> bool:
        return bool(self.installed) and not self.errors


def _candidate_dats(source: Path, tmpdir: Path) -> list[Path] | None:
    """Resolve *source* to concrete .dat files (zip → extracted members).

    Returns ``None`` when a zip contains no .dat at all.
    """
    with open(source, "rb") as fh:
        magic = fh.read(2)
    if magic == b"PK":
        with zipfile.ZipFile(source, "r") as zf:
            names = [
                n for n in zf.namelist()
                if n.lower().endswith(".dat") and not n.endswith("/")
            ]
            if not names:
                return None
            out = []
            for n in names:
                dest = tmpdir / Path(n).name
                with zf.open(n) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                out.append(dest)
            return out
    return [source]


def install_dats(sources: list[Path], dat_dir: Path) -> DatInstallReport:
    """Validate and install every DAT in *sources* into *dat_dir*.

    Existing DATs of the same family are removed after the new one is
    in place, so a botched import never leaves the platform uncovered.
    """
    report = DatInstallReport()
    dat_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        for source in sources:
            try:
                candidates = _candidate_dats(source, tmpdir)
            except (OSError, zipfile.BadZipFile) as e:
                logger.warning("Unreadable DAT source {}: {}", source, e)
                report.errors.append((source.name, "unreadable"))
                continue
            if candidates is None:
                report.errors.append((source.name, "no_dat_in_zip"))
                continue

            for dat_path in candidates:
                try:
                    platform, entries, _headers = parse_dat(dat_path)
                except (ET.ParseError, OSError) as e:
                    logger.warning("Invalid DAT {}: {}", dat_path.name, e)
                    report.errors.append((dat_path.name, "unreadable"))
                    continue
                if len(entries) < MIN_ENTRIES:
                    logger.warning(
                        "Rejected tiny DAT {} ({} entries)",
                        dat_path.name, len(entries))
                    report.errors.append((dat_path.name, "too_small"))
                    continue

                dest = dat_dir / dat_path.name
                tmp = dest.with_suffix(dest.suffix + ".tmp")
                try:
                    shutil.copyfile(dat_path, tmp)
                    os.replace(tmp, dest)
                except OSError as e:
                    logger.error("Failed to install {}: {}", dat_path.name, e)
                    report.errors.append((dat_path.name, "unreadable"))
                    continue

                # Retire older exports of the same family (never the
                # file just installed).
                family = dat_family(dest.name)
                replaced = []
                for old in dat_dir.glob("*.dat"):
                    if old == dest or dat_family(old.name) != family:
                        continue
                    try:
                        old.unlink()
                        replaced.append(old.name)
                    except OSError as e:
                        logger.warning("Could not remove old DAT {}: {}", old, e)

                report.installed.append(InstalledDat(
                    name=dest.name,
                    platform=platform,
                    entries=len(entries),
                    replaced=replaced,
                ))
                logger.info(
                    "Installed DAT {} ({} entries, {} replaced)",
                    dest.name, len(entries), len(replaced))

    return report
