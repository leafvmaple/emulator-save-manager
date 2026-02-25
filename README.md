# Emulator Save Manager

跨平台模拟器存档管理与多端同步工具。自动检测已安装的模拟器，扫描游戏存档，提供版本化备份、一键还原与多设备同步功能。

A cross-platform emulator save manager with multi-device sync. Auto-detects installed emulators, scans game saves, and provides versioned backup, one-click restore and multi-device synchronization.

![PySide6](https://img.shields.io/badge/PySide6-6.6+-blue)
![QFluentWidgets](https://img.shields.io/badge/QFluentWidgets-1.6+-green)
![Python](https://img.shields.io/badge/Python-3.10+-yellow)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

## Features

- **Auto Scan** — Automatically detect installed emulators and enumerate all game saves (memory cards, save states, battery saves, folder saves)
- **Versioned Backup** — Create ZIP-based versioned backups with sidecar JSON metadata; auto-rotate old backups per configurable limit
- **One-click Restore** — Preview changes before restoring; warns when local saves are newer than the backup
- **Multi-device Sync** — Push/pull backups through a shared folder (OneDrive, Google Drive, Nutstore, etc.) with conflict detection and resolution
- **Portable Paths** — Backup metadata uses placeholders (`${DOCUMENTS}`, `${APPDATA}`, …) so archives work across machines even when the Documents folder is relocated
- **Game Cover Art** — Downloads cover art from GitHub repositories; displays in card UI alongside game info
- **Plugin Architecture** — Each emulator is a self-contained plugin; easy to add new emulators
- **i18n** — Supports Simplified Chinese (zh_CN), English (en_US) and Japanese (ja_JP)
- **Fluent Design** — Modern card-based UI built with PySide6-Fluent-Widgets

## Supported Emulators

| Plugin | Emulator | Platforms | Save Types |
|--------|----------|-----------|------------|
| PCSX2 | PCSX2 | PS2 | Memory Card (image & folder), Save States |
| Mesen | Mesen2 | NES, SNES, Game Boy, GBA, PC Engine, SMS, WonderSwan | Battery Saves, Save States |
| Snes9x | Snes9x | SNES | Battery Saves (.srm), Save States |
| Citra | Citra | Nintendo 3DS | Application Saves, Save States |

## Screenshots

> *Coming soon*

## Installation

### From Release (Recommended)

Download the latest executable from the [Releases](../../releases) page — no Python installation required.

### From Source

```bash
# Clone
git clone https://github.com/<your-username>/emulator-save-manager.git
cd emulator-save-manager

# Create virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate
# Activate (macOS / Linux)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run
python main.py
```

**Requirements:** Python 3.10+

## Project Structure

```
emulator-save-manager/
├── main.py                  # Entry point
├── VERSION                  # Current version
├── requirements.txt
├── .github/workflows/       # CI/CD (release automation)
└── app/
    ├── config.py            # Singleton configuration
    ├── logger.py            # Loguru setup
    ├── i18n/                # Translations (zh_CN, en_US, ja_JP)
    ├── models/
    │   ├── emulator.py      # EmulatorInfo dataclass
    │   ├── game_save.py     # GameSave / SaveFile / SaveType
    │   └── backup_record.py # BackupRecord / BackupInfo
    ├── core/
    │   ├── scanner.py       # Orchestrates plugin detection & scanning
    │   ├── backup.py        # ZIP-based versioned backup engine
    │   ├── restore.py       # Restore engine with preview
    │   ├── sync.py          # Bidirectional sync via shared folder
    │   ├── conflict.py      # Conflict detection & resolution
    │   ├── game_icon.py     # Cover art provider & downloader
    │   └── path_resolver.py # Portable path placeholders & Windows Shell API
    ├── plugins/
    │   ├── base.py          # EmulatorPlugin abstract base class
    │   ├── plugin_manager.py
    │   ├── pcsx2/           # PCSX2 plugin (PS2 memory card parser)
    │   ├── mesen/           # Mesen2 plugin (MSS header parser)
    │   ├── snes9x/          # Snes9x plugin
    │   └── citra/           # Citra plugin (3DS title ID resolver)
    └── ui/
        ├── main_window.py   # FluentWindow with sidebar navigation
        ├── pages/           # Scan / Backup / Restore / Sync / Settings
        └── components/      # SaveTable, ConflictDialog, ProgressCard
```

## Configuration

Configuration is stored at:

| OS | Path |
|----|------|
| Windows | `Documents\EmulatorSaveManager\config.json` |
| macOS | `~/Library/Application Support/EmulatorSaveManager/config.json` |
| Linux | `~/.config/EmulatorSaveManager/config.json` |

Key settings:

| Key | Default | Description |
|-----|---------|-------------|
| `language` | `zh_CN` | UI language (`zh_CN` / `en_US` / `ja_JP`) |
| `theme` | `auto` | Theme (`light` / `dark` / `auto`) |
| `max_backups` | `5` | Max backup versions per game before auto-rotation |
| `backup_path` | `<data_dir>/backups` | Backup storage location |
| `sync_folder` | — | Shared folder path for multi-device sync |

## Multi-device Sync

1. Set a **Sync Directory** in Settings (e.g. a OneDrive / Google Drive / Nutstore synced folder)
2. On Machine A: **Scan** → **Backup** → **Push**
3. On Machine B: **Pull** → **Restore**
4. If both machines modified saves, a **Conflict Dialog** lets you choose per-file: use local / use remote / keep both / skip

Sync uses SHA-256 hashes and CRC32 disc checksums to detect conflicts and warn about incompatible game versions.

## Adding a New Emulator Plugin

1. Create a folder under `app/plugins/<name>/`
2. Implement `EmulatorPlugin` (see `app/plugins/base.py`):
   - `name` / `display_name` / `supported_platforms`
   - `detect_installation()` — return `list[EmulatorInfo]`
   - `scan_saves()` — return `list[GameSave]`
   - `get_save_directories()` — return `dict[str, Path]`
3. Optionally add `game_names.json` for display-name resolution and `icon.png` for the sidebar
4. The `PluginManager` auto-discovers all plugins at startup

## Building Executables

Releases are built automatically via GitHub Actions. To build locally:

```bash
pip install pyinstaller

# Windows
pyinstaller --noconfirm --onedir --windowed --name EmulatorSaveManager ^
  --add-data "app/i18n;app/i18n" ^
  --add-data "app/plugins;app/plugins" ^
  main.py

# macOS
pyinstaller --noconfirm --onedir --windowed --name EmulatorSaveManager \
  --add-data "app/i18n:app/i18n" \
  --add-data "app/plugins:app/plugins" \
  main.py
```

## License

MIT
