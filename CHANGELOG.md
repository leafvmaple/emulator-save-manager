# v0.9.2

Released on 2026-06-18

## ✨ Features

- feat(ui): flatter, lighter app icon (`13da23c`)
- feat(ui): empty-state action buttons + About card polish (`679907c`)
- feat(sync): redesign the status card (`7e445c4`)
- feat(scan): animate the card file-detail expand/collapse (`a6e54a0`)
- feat(ui): hover elevation on interactive cards (`8487781`)
- feat(restore): render backup versions as a vertical timeline (`190aaaa`)

## 🐛 Bug Fixes

- fix(settings): restore About card height (was collapsing to a strip) (`b215ee1`)
- fix(ui): even card-list whitespace via one shared helper (`0fba2ef`)
- fix(scan): remove emulator-row dead space that broke the vertical rhythm (`e931240`)

## 📦 Other Changes

- revert(ui): remove hover-elevation effect (caused card jitter) (`c7e7a9a`)

---

# v0.9.1

Released on 2026-06-18

## ✨ Features

- feat(scan): show loading skeletons while scanning (`a201704`)
- feat(ui): letter-avatar placeholders for games without cover art (`e76eaf7`)
- feat(home): add a recent-backups list to the dashboard (`1ceba7e`)
- feat(ui): add application icon (window, taskbar, exe) and Home logo (`03865d0`)

## 🐛 Bug Fixes

- fix(ui): even out the card-list vertical rhythm (`0902046`)
- fix(home): enlarge hero logo to match the title block (`4a8557a`)

---

# v0.9.0

Released on 2026-06-18

## ✨ Features

- feat(scan/restore): persist scan results, auto-load backups on open (`4806900`)
- feat(ui): add Home dashboard, empty states, and card interactions (`b49abd4`)

## 📦 Other Changes

- refactor(ui): narrow nav rail + selected-card highlight (`45b5ede`)
- refactor(ui): tighten and flatten the save cards (`f5fc487`)
- refactor(ui): extract shared TypeBadge + PageHeader components (`0302b92`)
- refactor(ui): theme-aware design tokens + consistent action bars (`cf5d667`)

---

# v0.8.3

Released on 2026-06-18

## 🐛 Bug Fixes

- fix(pcsx2): recognize file-based memory cards (Mcd001.ps2) (`3052d58`)

---

# v0.8.2

Released on 2026-06-18

## 🐛 Bug Fixes

- fix(sync): switching sync method at runtime had no effect (`3c2e0de`)

---

# v0.8.1

Released on 2026-06-17

## 🐛 Bug Fixes

- fix(sync): WebDAV test-connection 409 on Nutstore (+ dir creation) (`4a2fb0a`)
- fix(settings): WebDAV card was collapsed in the SettingCardGroup (`d0d031d`)

## 📦 Other Changes

- chore: stop tracking the generated PyInstaller .spec (`b1ff460`)

---

# v0.8.0

Released on 2026-06-17

## ✨ Features

- feat(settings): WebDAV sync configuration UI (`8f5d8f5`)
- feat(sync): pluggable sync backends — add WebDAV (core + tests) (`5d80d69`)

## 🐛 Bug Fixes

- fix: About shows the real version (read VERSION file, not hard-coded 0.1.0) (`d97710f`)

## 📦 Other Changes

- build: bundle webdav4 + keyring; selftest verifies them (`d6c7a8a`)

---

# v0.7.2

Released on 2026-06-17

## 🐛 Bug Fixes

- fix: release binaries were hollow — bundle i18n + plugins correctly (`1c72ad6`)

---

# v0.7.1

Released on 2026-06-16

## 🐛 Bug Fixes

- fix: don't crash on startup in windowed builds (sys.stderr is None) (`0c1e053`)

## 📦 Other Changes

- ci: release body shows only the current version's notes (`c9a7fba`)

---

# v0.7.0

Released on 2026-06-16

## ✨ Features

- feat: save-state thumbnails — Phase 1 (PCSX2 + RetroArch, scan page) (`e2a1aeb`)

---

# v0.6.0

Released on 2026-06-16

## ✨ Features

- feat(restore): compare backup versions (Stage 3) (`f0292b0`)

---

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
