"""App version is read from the VERSION file, not hard-coded."""

from __future__ import annotations

from pathlib import Path

import app.version as av


def test_version_matches_version_file():
    repo_version = (
        Path(av.__file__).resolve().parent.parent / "VERSION"
    ).read_text(encoding="utf-8").strip()
    assert av.get_app_version() == repo_version


def test_version_looks_like_a_release():
    # Regression: the About box used to show a hard-coded "0.1.0".
    v = av.get_app_version()
    assert v != "dev"
    assert v.count(".") >= 1  # e.g. "0.8.0"
