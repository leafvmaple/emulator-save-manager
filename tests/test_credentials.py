"""WebDAV credential storage via keyring (with an in-memory fake)."""

from __future__ import annotations


def _fake_keyring(monkeypatch):
    import keyring
    store: dict = {}
    monkeypatch.setattr(keyring, "set_password",
                        lambda s, k, v: store.__setitem__((s, k), v))
    monkeypatch.setattr(keyring, "get_password",
                        lambda s, k: store.get((s, k)))
    monkeypatch.setattr(keyring, "delete_password",
                        lambda s, k: store.pop((s, k), None))
    return store


def test_password_roundtrip(monkeypatch):
    _fake_keyring(monkeypatch)
    from app.core import credentials as cr

    assert cr.get_webdav_password() == ""
    assert cr.set_webdav_password("s3cret") is True
    assert cr.get_webdav_password() == "s3cret"
    cr.delete_webdav_password()
    assert cr.get_webdav_password() == ""


def test_setting_empty_password_clears(monkeypatch):
    _fake_keyring(monkeypatch)
    from app.core import credentials as cr

    cr.set_webdav_password("x")
    cr.set_webdav_password("")          # empty → clear
    assert cr.get_webdav_password() == ""


def test_keyring_failure_is_graceful(monkeypatch):
    import keyring

    def boom(*a, **k):
        raise RuntimeError("no keyring backend")

    monkeypatch.setattr(keyring, "set_password", boom)
    monkeypatch.setattr(keyring, "get_password", boom)
    from app.core import credentials as cr

    assert cr.set_webdav_password("x") is False   # doesn't raise
    assert cr.get_webdav_password() == ""          # doesn't raise
