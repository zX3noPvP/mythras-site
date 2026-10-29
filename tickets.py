"""Discord tickets: storage shared by the ticket bot (ticketbot/bot.py) and the website (transcript pages).

Both use the website's SQLite database. A closed ticket keeps its transcript (messages as JSON) and a random
token: the page is /transcripts/<token>, shown only to the person who opened the ticket and to staff.
Attachments are copied next to the database (data/transcripts/<token>/), because Discord's links expire.
"""

import json
import os
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,       -- general | billing
    status       TEXT NOT NULL,       -- opening | open | closing | closed
    channel_id   TEXT,
    opener_id    TEXT NOT NULL,
    opener_name  TEXT NOT NULL,
    answers      TEXT,                -- JSON [[question, answer], ...] from the form
    claimed_by   TEXT,
    claimed_name TEXT,
    closed_by    TEXT,
    closed_name  TEXT,
    close_reason TEXT,
    opened_at    TEXT NOT NULL,
    closed_at    TEXT,
    warned_at    TEXT,                -- "closes in 24 hours" reminder sent
    token        TEXT UNIQUE,         -- transcript page
    messages     TEXT                 -- JSON transcript
);
CREATE INDEX IF NOT EXISTS tickets_opener ON tickets (opener_id, status);
CREATE INDEX IF NOT EXISTS tickets_channel ON tickets (channel_id);
"""

KINDS = {
    "general": {"label": "General Questions", "emoji": "❓", "prefix": "question"},
    "billing": {"label": "Billing & Sales", "emoji": "💳", "prefix": "billing"},
}
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
# shown inline on the page; everything else is downloaded (an uploaded .html or .svg must never run on the site)
INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def db_path():
    return Path(os.getenv("DATABASE_PATH", BASE_DIR / "data" / "mythras.db"))


def files_dir():
    return db_path().parent / "transcripts"


def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def safe_filename(name):
    name = SAFE_NAME.sub("_", name or "file").strip("._") or "file"
    return name[-80:]


def attachment_path(token, stored):
    """Path of a saved attachment, or None if the name could leave the ticket's folder."""
    if not TOKEN_RE.match(token or "") or not stored or stored != safe_filename(stored):
        return None
    return files_dir() / token / stored


def load(row):
    """Ticket row as a dict with answers and messages decoded."""
    if row is None:
        return None
    t = dict(row)
    t["answers"] = json.loads(t["answers"] or "[]")
    t["messages"] = json.loads(t["messages"] or "[]")
    t["kind_label"] = KINDS.get(t["kind"], {}).get("label", t["kind"])
    return t


def env_list(name, default=""):
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


# who handles which tickets: Discord role IDs or role names, comma separated. Members with Administrator
# always count as staff. Billing tickets go to TICKETS_BILLING_ROLES when it is set, otherwise to staff.
DEFAULT_STAFF = "🎫 Support,🔧 Developer,🛡️ Admin,👑 Founder"


def staff_roles(kind):
    if kind == "billing" and env_list("TICKETS_BILLING_ROLES"):
        return env_list("TICKETS_BILLING_ROLES")
    return env_list("TICKETS_STAFF_ROLES", DEFAULT_STAFF)


def matches(role, wanted):
    """role: {"id", "name"}; wanted: IDs or names from the settings."""
    return str(role["id"]) in wanted or role["name"] in wanted
