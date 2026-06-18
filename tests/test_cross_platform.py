"""Cross-platform path resolution and emulator detection.

These run on any OS (incl. the Windows CI runner) by monkeypatching
``platform.system`` and the directory lookups, so the macOS/Linux branches
are actually exercised rather than skipped.
"""

from __future__ import annotations


from app.core import path_resolver as pr


# ----------------------------------------------------------------------
# platform_data_dir_candidates
# ----------------------------------------------------------------------

def test_candidates_macos(tmp_path, monkeypatch):
    appsup = tmp_path / "AppSupport"
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(pr, "_cache", {})
    monkeypatch.setattr(pr, "get_app_support_dir", lambda: appsup)
    dirs = pr.platform_data_dir_candidates(macos_names=["Foo"], linux_names=["foo"])
    assert dirs == [appsup / "Foo"]


def test_candidates_linux(tmp_path, monkeypatch):
    cfg, data = tmp_path / "c", tmp_path / "d"
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr(pr, "_cache", {})
    monkeypatch.setattr(pr, "get_xdg_config_dir", lambda: cfg)
    monkeypatch.setattr(pr, "get_xdg_data_dir", lambda: data)
    dirs = pr.platform_data_dir_candidates(macos_names=["Foo"], linux_names=["foo"])
    assert dirs == [cfg / "foo", data / "foo"]


def test_candidates_windows_is_empty(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Windows")
    assert pr.platform_data_dir_candidates(macos_names=["Foo"], linux_names=["foo"]) == []


# ----------------------------------------------------------------------
# Portable-path round-trips per OS
# ----------------------------------------------------------------------

def test_portable_roundtrip_macos(tmp_path, monkeypatch):
    appsup = tmp_path / "Library" / "Application Support"
    appsup.mkdir(parents=True)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(pr, "_cache", {})
    monkeypatch.setattr(pr, "get_app_support_dir", lambda: appsup)
    monkeypatch.setattr(pr, "get_documents_dir", lambda: tmp_path / "Documents")
    monkeypatch.setattr(pr, "get_home_dir", lambda: tmp_path)

    p = appsup / "PCSX2" / "memcards" / "card.ps2"
    portable = pr.to_portable_path(p)
    assert portable.startswith("${APP_SUPPORT}/")
    assert pr.resolve_path(portable) == p


def test_portable_roundtrip_linux(tmp_path, monkeypatch):
    cfg = tmp_path / ".config"
    data = tmp_path / ".local" / "share"
    cfg.mkdir(parents=True)
    data.mkdir(parents=True)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr(pr, "_cache", {})
    monkeypatch.setattr(pr, "get_xdg_config_dir", lambda: cfg)
    monkeypatch.setattr(pr, "get_xdg_data_dir", lambda: data)
    monkeypatch.setattr(pr, "get_documents_dir", lambda: tmp_path / "Documents")
    monkeypatch.setattr(pr, "get_home_dir", lambda: tmp_path)

    p = cfg / "PCSX2" / "memcards" / "card.ps2"
    portable = pr.to_portable_path(p)
    assert portable.startswith("${XDG_CONFIG}/")
    assert pr.resolve_path(portable) == p


# ----------------------------------------------------------------------
# Plugin detection on macOS / Linux layouts
# ----------------------------------------------------------------------

def test_pcsx2_detects_linux(tmp_path, monkeypatch):
    from app.plugins.pcsx2.plugin import PCSX2Plugin
    cfg = tmp_path / ".config"
    (cfg / "PCSX2" / "memcards").mkdir(parents=True)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr(pr, "_cache", {})
    monkeypatch.setattr(pr, "get_xdg_config_dir", lambda: cfg)
    monkeypatch.setattr(pr, "get_xdg_data_dir", lambda: tmp_path / ".local" / "share")

    infos = PCSX2Plugin().detect_installation(None)
    assert any((i.data_path / "memcards").exists() for i in infos)


def test_pcsx2_detects_macos(tmp_path, monkeypatch):
    from app.plugins.pcsx2.plugin import PCSX2Plugin
    appsup = tmp_path / "Library" / "Application Support"
    (appsup / "PCSX2" / "memcards").mkdir(parents=True)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(pr, "_cache", {})
    monkeypatch.setattr(pr, "get_app_support_dir", lambda: appsup)

    infos = PCSX2Plugin().detect_installation(None)
    assert any((i.data_path / "memcards").exists() for i in infos)


def test_dolphin_detects_linux(tmp_path, monkeypatch):
    from app.plugins.dolphin.plugin import DolphinPlugin
    data = tmp_path / ".local" / "share"
    (data / "dolphin-emu" / "GC").mkdir(parents=True)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr(pr, "_cache", {})
    monkeypatch.setattr(pr, "get_xdg_config_dir", lambda: tmp_path / ".config")
    monkeypatch.setattr(pr, "get_xdg_data_dir", lambda: data)

    infos = DolphinPlugin().detect_installation(None)
    assert any(i.data_path.name == "dolphin-emu" for i in infos)


def test_citra_detects_macos(tmp_path, monkeypatch):
    from app.plugins.citra.plugin import CitraPlugin
    appsup = tmp_path / "Library" / "Application Support"
    (appsup / "Citra" / "sdmc").mkdir(parents=True)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(pr, "_cache", {})
    monkeypatch.setattr(pr, "get_app_support_dir", lambda: appsup)

    infos = CitraPlugin().detect_installation(None)
    assert any(i.data_path.name == "Citra" and not i.is_portable for i in infos)


def test_extra_paths_work_on_non_windows(tmp_path, monkeypatch):
    """User-configured paths are honored on every platform."""
    from app.plugins.pcsx2.plugin import PCSX2Plugin
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr(pr, "_cache", {})
    monkeypatch.setattr(pr, "get_xdg_config_dir", lambda: tmp_path / ".config")
    monkeypatch.setattr(pr, "get_xdg_data_dir", lambda: tmp_path / ".local" / "share")

    custom = tmp_path / "my-pcsx2"
    (custom / "memcards").mkdir(parents=True)
    infos = PCSX2Plugin().detect_installation([custom])
    assert any(i.data_path == custom for i in infos)
