"""Generate the PyInstaller Windows version-info file from VERSION.

Fills the exe's Properties → Details panel (company / product / version
/ copyright). This is file metadata only — the "Publisher" shown by UAC
and SmartScreen comes from an Authenticode code-signing certificate,
which is a separate (paid / SignPath) concern.

Usage: python tools/gen_version_info.py [output_path]
"""

from __future__ import annotations

import sys
from pathlib import Path

COMPANY = "leafvmaple"
PRODUCT = "Emulator Save Manager"
DESCRIPTION = "Cross-platform emulator save manager with ROM library tools"

TEMPLATE = """\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable("040904B0", [
        StringStruct("CompanyName", "{company}"),
        StringStruct("FileDescription", "{description}"),
        StringStruct("FileVersion", "{version}.0"),
        StringStruct("InternalName", "EmulatorSaveManager"),
        StringStruct("LegalCopyright", "MIT License, (c) {company}"),
        StringStruct("OriginalFilename", "EmulatorSaveManager.exe"),
        StringStruct("ProductName", "{product}"),
        StringStruct("ProductVersion", "{version}.0"),
      ])
    ]),
    VarFileInfo([VarStruct("Translation", [1033, 1200])]),
  ],
)
"""


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    try:
        major, minor, patch = (int(x) for x in version.split("."))
    except ValueError:
        print(f"Unparseable VERSION: {version!r}", file=sys.stderr)
        return 1

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "version_info.txt"
    out.write_text(TEMPLATE.format(
        major=major, minor=minor, patch=patch, version=version,
        company=COMPANY, product=PRODUCT, description=DESCRIPTION,
    ), encoding="utf-8")
    print(f"version info for {version} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
