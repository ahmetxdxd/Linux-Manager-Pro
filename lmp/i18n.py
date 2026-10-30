"""Minimal gettext-style translation layer.

Source strings are English and double as message ids. Other languages live in
``lmp/locales/<code>.py`` as a ``MESSAGES`` dict mapping the English text to its translation;
a missing entry silently falls back to English.

Usage::

    from lmp.i18n import tr, tr_noop, trn

    label.setText(tr("Uninstall"))
    tr("{n} items").format(n=3)
    trn("{n} item", "{n} items", n).format(n=n)
    KINDS = {"cfg": tr_noop("Configuration")}   # module-level constants: mark now, tr() on display
"""

from __future__ import annotations

import importlib

LANGUAGES = {"en": "English", "tr": "Türkçe"}
DEFAULT = "en"

_lang = DEFAULT
_catalog: dict[str, str] = {}


def set_language(code: str) -> str:
    """Activates a language (unknown codes fall back to English). Returns the active code."""
    global _lang, _catalog
    if code not in LANGUAGES:
        code = DEFAULT
    _lang = code
    _catalog = {} if code == DEFAULT else dict(importlib.import_module(f"lmp.locales.{code}").MESSAGES)
    return _lang


def current() -> str:
    return _lang


def tr(message: str) -> str:
    """Translates ``message`` into the active language."""
    return _catalog.get(message, message)


def tr_noop(message: str) -> str:
    """Marks a string for translation without translating it (for module-level constants)."""
    return message


def trn(singular: str, plural: str, n: int) -> str:
    """Plural-aware translation. Turkish uses the same form for both, so only ``plural`` is
    looked up in the catalog."""
    if _lang == DEFAULT:
        return singular if n == 1 else plural
    return _catalog.get(plural, plural)
