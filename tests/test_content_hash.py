"""Content-hash behaviour — the fix that kills false-positive sync conflicts."""

from __future__ import annotations

import zipfile

from app.core.conflict import file_sha256, zip_content_hash


def _make_zip(path, date_time, arcname="save/file.bin", content=b"DATA"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo(arcname, date_time=date_time), content)


def test_content_hash_ignores_timestamps(tmp_path):
    """Identical content, different embedded mtimes → same content hash."""
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    _make_zip(a, (2020, 1, 1, 0, 0, 0))
    _make_zip(b, (2026, 6, 16, 12, 30, 0))

    # Raw bytes differ (this is exactly why the old file-hash gave false conflicts)...
    assert file_sha256(a) != file_sha256(b)
    # ...but the content hash sees them as equal.
    assert zip_content_hash(a) == zip_content_hash(b)


def test_content_hash_detects_real_change(tmp_path):
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    _make_zip(a, (2020, 1, 1, 0, 0, 0), content=b"AAA")
    _make_zip(b, (2020, 1, 1, 0, 0, 0), content=b"BBB")
    assert zip_content_hash(a) != zip_content_hash(b)


def test_content_hash_detects_renamed_entry(tmp_path):
    """A file moving to a different arcname is a real content difference."""
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    _make_zip(a, (2020, 1, 1, 0, 0, 0), arcname="save/x.bin", content=b"DATA")
    _make_zip(b, (2020, 1, 1, 0, 0, 0), arcname="save/y.bin", content=b"DATA")
    assert zip_content_hash(a) != zip_content_hash(b)


def test_content_hash_ignores_backup_thumbnails(tmp_path):
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    _make_zip(a, (2020, 1, 1, 0, 0, 0), content=b"DATA")
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("save/file.bin", b"DATA")
        zf.writestr("thumbnails/000_slot0.png", b"DISPLAY-ONLY")

    assert zip_content_hash(a) == zip_content_hash(b)


def test_content_hash_missing_file_returns_empty(tmp_path):
    assert zip_content_hash(tmp_path / "does-not-exist.zip") == ""
