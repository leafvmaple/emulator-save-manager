# Roadmap

Notable work deliberately left out of the current release, tracked here so it
isn't lost.

## Post-1.0

### P1 — Save identity hardening (prerequisite for all ROM work)

Cartridge-family plugins (Snes9x, Mesen, melonDS, RetroArch) use the **ROM
filename stem** as `game_id`, and the backup store is keyed by
`{emulator}/{game_id}` on disk. Renaming a ROM therefore orphans the game's
backup chain and breaks sync keys — in addition to the emulator itself losing
track of its `.srm`/`.dsv` saves.

- **Backup alias table** — `aliases.json` in the backup root mapping
  `emulator:old_id → emulator:new_id`; `BackupEngine.rename_game()` moves the
  backup directory and records the alias; all lookups follow aliases so old
  metadata and sync records keep resolving. *(Status: shipped — see CHANGELOG)*
- **Content-hash identity** — populate `GameSave.crc32` for cartridge systems
  once the ROM library (below) can link saves to ROM files, so identity
  survives renames without manual migration.

### ROM library (staged)

The differentiator vs. generic ROM managers (igir, RomVault, ClrMamePro):
this app knows every emulator's save-naming convention, so it can eventually
rename ROMs **and** migrate saves + backup keys in lockstep.

Write policy: scanning never moves, renames or deletes files, but
*convergent repairs* — edits whose result lands exactly on a known-good
DAT hash — are legitimate scan-time writes (original kept as a sibling
`.bak`, first write wins; replacement is atomic).

- **Stage A — library view** *(Status: shipped — see CHANGELOG)*:
  configure ROM directories, scan + CRC32-hash files (mtime/size cache),
  detect duplicates by content hash, link ROMs ↔ scanned saves by filename
  stem, and report ROMs without saves / saves without ROMs.
- **DAT verification + convergent NES header repair** *(Status: shipped)*:
  No-Intro DATs dropped into `<data_dir>/dat` (config `dat_dir`) verify
  CRCs against canonical names; a `.nes` miss — bare file or inside a
  `.zip` — is re-tried with every header seen in the headered DAT and
  fixed in place on a hit (ported from the proven `emulator-manager`
  matcher).
- **Derived-version detection** *(Status: shipped)*: GB/GBA/NDS cartridge
  headers are parsed (game code / title survive fan patching); a
  CRC-missing ROM whose embedded identity matches a DAT-verified library
  sibling is labelled "derived from X" — fan translations and hacks
  identified without touching the file. Follow-up: a bundled base-game
  DB so derived detection works without an official sibling present.
- **Stage B — save-aware normalization**: rename ROMs to a canonical
  convention (No-Intro style) with a dry-run preview, migrating emulator save
  files and backup keys (via the alias table) in the same transaction.
  Automatic safety backup before execution. The legacy `emulator-manager`
  `rename_engine.py` is prior art to port.
- **Stage C — curation**: 1G1R region preference, per-platform archiving,
  interop with igir reports; mis-named detection against DAT canonical
  names; custom DB for fan-translation identification (port
  `games_custom.json` from `emulator-manager`).

### Polish

- **README screenshots** — refresh them. The UI was substantially redesigned
  across the `0.x → 1.0` work (Fluent theming, Home dashboard, flat icon,
  timelines, etc.) and the docs' images are stale.

### Performance

- **Streaming sync transfers** *(Status: shipped — extracted from the
  `feat/rom-core-integration` branch)*: push/pull stream file content in
  chunks with per-transfer progress reporting instead of whole-file
  reads; remote content hashes come from sidecar metadata so unchanged
  games skip the download entirely.
- **Large libraries** — the scan / backup / restore lists build every card
  eagerly with no scroll virtualization. This is fine for typical libraries but
  should be stress-tested at 50+ games and, if needed, virtualized.
- **Parallel scan** — `Scanner` probes plugins sequentially; detection and
  save-scanning are filesystem-bound and could fan out on a thread pool.
- **Event-driven auto-backup** — replace the fixed-interval daemon rescan with
  a filesystem watcher on plugin save directories (debounced), keeping the
  interval mode as fallback.

### Architecture

- **Slim the heavy pages** — `restore_page` / `scan_page` / `settings_page`
  (~800–1000 lines each) mix orchestration into the UI. Extract per-page
  view-models incrementally whenever a page is next touched, so core logic
  becomes unit-testable.

### Features (longer term)

- **More emulators** beyond the current set (PCSX2, Mesen, Snes9x, Citra,
  Dolphin, melonDS, RetroArch).
- **Wider save-state thumbnail coverage** — more emulators' state formats.
- **Native cloud APIs** (Google Drive / Dropbox / OneDrive) in addition to the
  current WebDAV + local/shared-folder sync.
- **Backup dedup** — per-version full ZIPs grow fast with large memory cards;
  consider content-addressed storage if rotation limits prove insufficient.

---

For shipped history see [CHANGELOG.md](CHANGELOG.md).
