"""Config persistence — atomic writes."""

from __future__ import annotations

import json


def test_config_roundtrips(cfg):
    cfg.set("language", "en_US")
    cfg.set("max_backups", 9)
    on_disk = json.loads(cfg._path.read_text(encoding="utf-8"))
    assert on_disk["language"] == "en_US"
    assert on_disk["max_backups"] == 9


def test_save_is_atomic_on_failure(cfg, monkeypatch):
    """If the write fails mid-way, the existing config is left untouched."""
    cfg.set("language", "en_US")  # known-good state on disk
    good = cfg._path.read_text(encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", boom)
    cfg.set("language", "ja_JP")  # triggers a failing _save

    # Original file is intact (not truncated/corrupted)...
    assert cfg._path.read_text(encoding="utf-8") == good
    # ...and no temp file was left behind.
    assert not cfg._path.with_name(cfg._path.name + ".tmp").exists()


def test_no_tmp_left_after_success(cfg):
    cfg.set("theme", "dark")
    assert not cfg._path.with_name(cfg._path.name + ".tmp").exists()
    assert cfg._path.exists()
