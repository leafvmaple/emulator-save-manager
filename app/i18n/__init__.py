"""Internationalization module."""

import json
from pathlib import Path
from typing import Optional

_current_lang = "zh_CN"
_translations: dict[str, dict[str, str]] = {}
_i18n_dir = Path(__file__).parent


def load_language(lang: str) -> None:
    """Load a language file into the translation cache."""
    global _current_lang
    lang_file = _i18n_dir / f"{lang}.json"
    if lang_file.exists():
        with open(lang_file, "r", encoding="utf-8") as f:
            _translations[lang] = json.load(f)
        _current_lang = lang
    else:
        raise FileNotFoundError(f"Language file not found: {lang_file}")


def set_language(lang: str) -> None:
    """Switch the current language."""
    global _current_lang
    if lang not in _translations:
        load_language(lang)
    _current_lang = lang


def t(key: str, **kwargs: str) -> str:
    """Get a translated string by dot-separated key.

    Supports placeholder substitution via keyword arguments.
    Example: t("backup.count", count="5") -> "共 5 个备份"
    """
    keys = key.split(".")
    data: any = _translations.get(_current_lang, {})
    for k in keys:
        if isinstance(data, dict):
            data = data.get(k)
        else:
            data = None
            break
    if data is None:
        return key
    result = str(data)
    for k, v in kwargs.items():
        result = result.replace(f"{{{k}}}", str(v))
    return result


def get_current_language() -> str:
    """Return the current language code."""
    return _current_lang


def get_available_languages() -> list[str]:
    """Return list of available language codes."""
    return [f.stem for f in _i18n_dir.glob("*.json")]


def init(lang: Optional[str] = None) -> None:
    """Initialize i18n — load all available languages and set the active one."""
    for f in _i18n_dir.glob("*.json"):
        load_language(f.stem)
    if lang and lang in _translations:
        set_language(lang)
