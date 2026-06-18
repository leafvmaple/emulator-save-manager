"""Update checking against GitHub Release metadata."""

from __future__ import annotations

import json

from app.core.update_check import check_latest_release, is_newer_version


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_version_compare_handles_v_prefix_and_padding():
    assert is_newer_version("v1.0.2", "1.0.1")
    assert is_newer_version("1.1", "1.0.9")
    assert not is_newer_version("1.0.1", "1.0.1")
    assert not is_newer_version("1.0", "1.0.1")


def test_check_latest_release_parses_github_payload():
    def fetch(_request, timeout):  # noqa: ANN001
        assert timeout == 8
        return _FakeResponse({
            "tag_name": "v1.2.0",
            "name": "v1.2.0",
            "html_url": "https://example.test/release",
            "published_at": "2026-01-01T00:00:00Z",
        })

    info = check_latest_release("1.0.1", fetcher=fetch)

    assert info.latest_version == "1.2.0"
    assert info.release_url == "https://example.test/release"
    assert info.is_update_available is True
