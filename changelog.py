"""Official Mythras changelog, published by the license server (GET /v1/changelog, public, license server 1.14+).

The website never writes it: CHANGELOG.md (and CHANGELOG.<lang>.md for the translations) is copied next to the
license server by `mythras-update`. The site asks for the visitor's language with ?lang=; versions not translated yet
come back in English. Answers are cached for 5 minutes per language; the last good answer is also saved to a file so
the page survives a license server outage and a website restart. Lines are plain text: escaped, `backticks` as <code>.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from markupsafe import Markup, escape

TTL = 300
TIMEOUT = 5
KINDS = ("New", "Improved", "Fixed", "Licenses")
_PREFIX = re.compile(r"^(New|Improved|Fixed|Licenses):\s*")
_lock = threading.Lock()
LANGS = ("en", "it", "de", "fr", "es")
_mem = {}  # lang -> {"at": time, "versions": [...]}

MONTHS = {
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"),
    "it": ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
           "novembre", "dicembre"),
    "de": ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November",
           "Dezember"),
    "fr": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre",
           "décembre"),
    "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre"),
}


def _cache_file(lang="en"):
    db = Path(os.getenv("DATABASE_PATH", "") or Path(__file__).resolve().parent / "data" / "mythras.db")
    return db.parent / ("changelog-cache.json" if lang == "en" else f"changelog-cache.{lang}.json")


def _fetch(lang="en"):
    base = os.getenv("MYTHRAS_API_URL", "").rstrip("/")
    if not base:
        return None
    url = base + "/v1/changelog" + ("" if lang == "en" else f"?lang={lang}")
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "MythrasWebsite/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    versions = data.get("versions") if isinstance(data, dict) and data.get("ok") else None
    if not isinstance(versions, list):
        return None
    clean = []
    for v in versions:
        if isinstance(v, dict) and v.get("version"):
            vlang = str(v.get("lang") or "en")  # older license servers send English without "lang"
            clean.append({"version": str(v["version"])[:32], "date": str(v.get("date") or "")[:10],
                          "lang": vlang if vlang in LANGS else "en",
                          "changes": [str(c) for c in (v.get("changes") or []) if isinstance(c, str)][:200]})
    return clean


def versions(lang="en"):
    """Newest first in this language (untranslated versions in English), or None when nothing was ever received."""
    lang = lang if lang in LANGS else "en"
    with _lock:
        mem = _mem.setdefault(lang, {"at": 0.0, "versions": None})
        if mem["versions"] is not None and time.time() - mem["at"] < TTL:
            return mem["versions"]
        fresh = _fetch(lang)
        if fresh is not None:
            mem.update(at=time.time(), versions=fresh)
            try:
                tmp = _cache_file(lang).with_suffix(".part")
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(fresh), encoding="utf-8")
                os.replace(tmp, _cache_file(lang))
            except OSError:
                pass
            return fresh
        if mem["versions"] is not None:  # license server down: keep the last good answer, retry in a minute
            mem["at"] = time.time() - TTL + 60
            return mem["versions"]
        try:
            saved = json.loads(_cache_file(lang).read_text(encoding="utf-8"))
            if isinstance(saved, list):
                mem.update(at=time.time() - TTL + 60, versions=saved)
                return saved
        except (OSError, ValueError):
            pass
    return versions("en") if lang != "en" else None  # nothing in this language yet: English


def latest(lang="en"):
    items = versions(lang)
    return items[0] if items else None


def split(change):
    """'Fixed: text' -> ('Fixed', 'text'); no known prefix -> (None, change)."""
    m = _PREFIX.match(change)
    return (m.group(1), change[m.end():]) if m else (None, change)


def render(text):
    """Plain text with `code` spans: escaped, backtick parts wrapped in <code>."""
    parts = text.split("`")
    if len(parts) % 2 == 0:  # unmatched backtick: show it as written
        return escape(text)
    return Markup("").join(Markup("<code>{}</code>").format(p) if i % 2 else escape(p) for i, p in enumerate(parts))


def plain(text):
    return text.replace("`", "")


def parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def format_date(value, lang="en"):
    d = parse_date(value)
    if not d:
        return value or ""
    month = MONTHS.get(lang, MONTHS["en"])[d.month - 1]
    if lang == "it" or lang == "fr":
        return f"{d.day} {month} {d.year}"
    if lang == "de":
        return f"{d.day}. {month} {d.year}"
    if lang == "es":
        return f"{d.day} de {month} de {d.year}"
    return f"{month} {d.day}, {d.year}"


def reset_cache():
    """Tests only."""
    with _lock:
        _mem.clear()
