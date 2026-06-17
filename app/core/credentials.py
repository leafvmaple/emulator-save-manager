"""Secret storage via the OS keyring (Windows Credential Manager, macOS
Keychain, Linux Secret Service).

Passwords are never written to ``config.json`` (which is plaintext) — only
the WebDAV URL / username live there; the password is kept in the keyring.
All calls degrade gracefully if no keyring backend is available.
"""

from __future__ import annotations

from loguru import logger

_SERVICE = "EmulatorSaveManager"
_WEBDAV_KEY = "webdav-password"


def set_webdav_password(password: str) -> bool:
    """Store (or clear) the WebDAV password in the OS keyring."""
    try:
        import keyring
        if password:
            keyring.set_password(_SERVICE, _WEBDAV_KEY, password)
        else:
            delete_webdav_password()
        return True
    except Exception as e:  # noqa: BLE001 - keyring backends vary widely
        logger.warning("Could not store WebDAV password in keyring: {}", e)
        return False


def get_webdav_password() -> str:
    """Return the stored WebDAV password, or empty string."""
    try:
        import keyring
        return keyring.get_password(_SERVICE, _WEBDAV_KEY) or ""
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not read WebDAV password from keyring: {}", e)
        return ""


def delete_webdav_password() -> None:
    try:
        import keyring
        keyring.delete_password(_SERVICE, _WEBDAV_KEY)
    except Exception:  # noqa: BLE001 - missing entry / no backend
        pass


def has_keyring() -> bool:
    """True if a usable (non-fail) keyring backend is available."""
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
        return not isinstance(keyring.get_keyring(), FailKeyring)
    except Exception:  # noqa: BLE001
        return False
