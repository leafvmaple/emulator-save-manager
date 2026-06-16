# v0.5.0

Released on 2026-06-16

## ✨ Features

- feat: auto-backup with change detection (Stage 3) (`971b911`)
- feat(plugins): add RetroArch plugin (Stage 3) (`86879cc`)
- feat(restore): selective restore — pick which saves to restore (Stage 3) (`b94c9ab`)

---

# v0.4.0

Released on 2026-06-16

## ✨ Features

- feat: cross-platform emulator detection (macOS + Linux) — Stage 2 (`48add27`)
- feat: finish Stage 1 — atomic config, cancellable ops, backup management UI (`af7197c`)
- feat(restore): transactional restore with snapshot + rollback (`8ce56ce`)

## 📦 Other Changes

- ci: fix test collection — put project root on sys.path (`8fce7f9`)
- ci: add pytest quality gate on push/PR (`3429130`)
- test: add local pytest suite (Stage 1 kickoff) (`a167de3`)

---

# v0.3.0

Released on 2026-06-16

## ✨ Features

- feat: add Dolphin and melonDS plugins, portable emulator-relative paths (`b2c4a5b`)
- feat: add README (`5284722`)
- feat: portable path resolver, expandable file details, and UI polish (`f01c1d5`)

## 🐛 Bug Fixes

- fix: make sync/restore/packaging actually work (stage 0) (`b2b84c5`)
- fix: fixed CHANGELOG.md (`38df16a`)

---

# v0.2.1 Release Notes

Released on 2026-02-25

## ✨ Features

- feat: add workflow (`84dac2c`)
- feat: add Snes9x plugin and user-configurable emulator install paths (`eb0f747`)
- feat: add Citra plugin and migrate name resolution to local tables (`2556c80`)
- feat: ZIP backup engine + card-based UI overhaul + game cover & emulator icon system (`0223466`)
- feat: started (`1fcfe1c`)

## 🐛 Bug Fixes

- fix: fixed workflow run error (`cb7afbb`)

## 📦 Other Changes

- chore(release): v0.2.0 (`9cf479e`)
