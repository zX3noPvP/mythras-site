"""Official Mythras changelog, published by the license server (GET /v1/changelog, public, license server 1.14+).

The website never writes it: CHANGELOG.md is copied next to the license server by `mythras-update`.
Answers are cached for 5 minutes; the last good answer is also saved to a file so the page survives a
license server outage and a website restart. Lines are plain text: escaped, with `backticks` as <code>.
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
_mem = {"at": 0.0, "versions": None}

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


def _cache_file():
    db = Path(os.getenv("DATABASE_PATH", "") or Path(__file__).resolve().parent / "data" / "mythras.db")
    return db.parent / "changelog-cache.json"


def _fetch():
    base = os.getenv("MYTHRAS_API_URL", "").rstrip("/")
    if not base:
        return None
    req = urllib.request.Request(base + "/v1/changelog", headers={"Accept": "application/json",
                                                                   "User-Agent": "MythrasWebsite/1.0"})
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
            clean.append({"version": str(v["version"])[:32], "date": str(v.get("date") or "")[:10],
                          "changes": [str(c) for c in (v.get("changes") or []) if isinstance(c, str)][:200]})
    return clean


def versions():
    """Newest first, or None when nothing was ever received."""
    with _lock:
        if _mem["versions"] is not None and time.time() - _mem["at"] < TTL:
            return _mem["versions"]
        fresh = _fetch()
        if fresh is not None:
            _mem.update(at=time.time(), versions=fresh)
            try:
                tmp = _cache_file().with_suffix(".part")
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(fresh), encoding="utf-8")
                os.replace(tmp, _cache_file())
            except OSError:
                pass
            return fresh
        if _mem["versions"] is not None:  # license server down: keep the last good answer, retry in a minute
            _mem["at"] = time.time() - TTL + 60
            return _mem["versions"]
        try:
            saved = json.loads(_cache_file().read_text(encoding="utf-8"))
            if isinstance(saved, list):
                _mem.update(at=time.time() - TTL + 60, versions=saved)
                return saved
        except (OSError, ValueError):
            pass
        return None


def latest():
    items = versions()
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
        _mem.update(at=0.0, versions=None)
