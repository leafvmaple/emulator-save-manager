# Roadmap

Notable work deliberately left out of the current release, tracked here so it
isn't lost.

## Post-1.0

### Polish
- **First-run onboarding** — guide the user on first launch (no config, nothing
  scanned yet). The Home dashboard currently shows `—` placeholders until the
  first scan completes; a short welcome / "scan to get started" flow would read
  better.
- **README screenshots** — refresh them. The UI was substantially redesigned
  across the `0.x → 1.0` work (Fluent theming, Home dashboard, flat icon,
  timelines, etc.) and the docs' images are stale.

### Performance
- **Large libraries** — the scan / backup / restore lists build every card
  eagerly with no scroll virtualization. This is fine for typical libraries but
  should be stress-tested at 50+ games and, if needed, virtualized.

### Features (longer term)
- **More emulators** beyond the current set (PCSX2, Mesen, Snes9x, Citra,
  Dolphin, melonDS, RetroArch).
- **Wider save-state thumbnail coverage** — more emulators' state formats.
- **Native cloud APIs** (Google Drive / Dropbox / OneDrive) in addition to the
  current WebDAV + local/shared-folder sync.

---

For shipped history see [CHANGELOG.md](CHANGELOG.md).
