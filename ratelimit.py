"""Request rate limits.

Fixed windows: each (rule, who) pair may make `limit` requests per `seconds`. "who" is the signed-in Discord ID when
there is one, otherwise the client IP. Sensitive rules are counted in the site's SQLite database, shared by all gunicorn
workers. Plain page views only stop floods: they are counted in memory, per worker, because a database write on every
page costs more than rendering the page.
"""

import random
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_limits (
    rule    TEXT NOT NULL,
    who     TEXT NOT NULL,
    window  INTEGER NOT NULL,
    count   INTEGER NOT NULL,
    PRIMARY KEY (rule, who, window)
);
"""

# rule -> (requests, seconds). Rules are picked by endpoint in app.py.
RULES = {
    "login": (20, 60),            # Discord login + callback, per IP
    "claim": (10, 600),           # "Link a key": stops key guessing
    "buy": (10, 600),             # starting PayPal purchases
    "shop_check": (40, 60),       # "Waiting for PayPal" polling (20 per minute when waiting)
    "setup": (20, 600),           # setup links: opening, answering, self-service
    "bot": (60, 60),              # bot API, per IP
    "download": (30, 3600),       # plugin / proxy jars
    "heads": (300, 60),           # player heads in the panel
    "post": (120, 60),            # any other form, per user/IP
    "page": (600, 60),            # everything else, per IP: only stops floods
}


MEMORY_RULES = {"heads", "page"}
_memory = {}  # (rule, who) -> [window, count]


def init(db):
    db.executescript(SCHEMA)


def _hit_memory(rule, who, limit, seconds, now, window):
    if len(_memory) > 20000:
        _memory.clear()
    entry = _memory.get((rule, who))
    if entry is None or entry[0] != window:
        entry = _memory[(rule, who)] = [window, 0]
    entry[1] += 1
    return 0 if entry[1] <= limit else window + seconds - now


def hit(db, rule, who):
    """Count one request. Returns 0 if allowed, else the seconds to wait."""
    limit, seconds = RULES[rule]
    now = int(time.time())
    window = now - now % seconds
    if rule in MEMORY_RULES:
        return _hit_memory(rule, who, limit, seconds, now, window)
    db.execute("INSERT INTO rate_limits (rule, who, window, count) VALUES (?, ?, ?, 1)"
               " ON CONFLICT (rule, who, window) DO UPDATE SET count = count + 1", (rule, who, window))
    count = db.execute("SELECT count FROM rate_limits WHERE rule = ? AND who = ? AND window = ?",
                       (rule, who, window)).fetchone()[0]
    if random.random() < 0.01:  # housekeeping: windows older than an hour
        db.execute("DELETE FROM rate_limits WHERE window < ?", (now - 3600 - max(s for _, s in RULES.values()),))
    db.commit()
    return 0 if count <= limit else window + seconds - now
