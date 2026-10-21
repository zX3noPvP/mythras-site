"""Tiny translation layer.

Templates and views use English source strings; translations/<lang>.json maps each
English string to its translation. Missing strings fall back to English.
"""

import json
from pathlib import Path

LANGUAGES = {
    "en": "English",
    "it": "Italiano",
    "de": "Deutsch",
    "fr": "Français",
    "es": "Español",
}
DEFAULT = "en"
COOKIE = "lang"

_DIR = Path(__file__).resolve().parent / "translations"
_catalogs = {}


def load():
    _catalogs.clear()
    for code in LANGUAGES:
        path = _DIR / f"{code}.json"
        if code != DEFAULT and path.exists():
            _catalogs[code] = json.loads(path.read_text(encoding="utf-8"))


def pick(request):
    """Language from the preference cookie, else the browser's Accept-Language, else English."""
    code = request.cookies.get(COOKIE, "")
    if code in LANGUAGES:
        return code
    return request.accept_languages.best_match(list(LANGUAGES)) or DEFAULT


def gettext(lang, text, **values):
    translated = _catalogs.get(lang, {}).get(text) or text
    return translated % values if values else translated


load()
