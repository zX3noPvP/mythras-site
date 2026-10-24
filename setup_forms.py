"""Setup questionnaire: per-plan questions (edited by admins), single-use invite links and the answers.

An admin (or the Discord bot) creates an invite for a plan; the customer opens /setup/<token>, signs in with
Discord, answers the questions of their plan and the answers become plugin config for their key
(sent to the license server, which hands them to the plugin). Info-only questions (key "info.*") are just
shown to the admin; "info.server_ip" is the address the admin can lock the key to.

Invite tokens are random and stored only as a SHA-256 hash: a leaked database cannot be turned into links.
"""

import hashlib
import ipaddress
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

PLANS = ("free", "pro")
ADDONS = ("antixray", "antiesp")
# who sees a question: everyone (free), Pro keys (pro), or keys with that add-on
REQUIREMENTS = PLANS + ADDONS
KINDS = ("yesno", "choice", "text", "number", "ip", "list", "webhook")
MAX_TEXT = 200
LIST_ITEM_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
CONFIG_KEY_RE = re.compile(r"^[a-z0-9_.\-]{2,80}$")
WEBHOOK_RE = re.compile(r"^https://(?:discord|discordapp)\.com/api/webhooks/\d{5,30}/[A-Za-z0-9_\-]{20,120}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    position    INTEGER NOT NULL,
    plan        TEXT NOT NULL,              -- who sees it: free (everyone) | pro | antixray | antiesp (add-ons)
    kind        TEXT NOT NULL,              -- yesno | choice | text | number | ip | list | webhook
    config_key  TEXT NOT NULL UNIQUE,       -- plugin config path, or info.* (only shown to the admin)
    label       TEXT NOT NULL,
    help        TEXT NOT NULL DEFAULT '',
    warning     TEXT NOT NULL DEFAULT '',
    options     TEXT NOT NULL DEFAULT '[]', -- JSON [[value, label], ...] for choice
    default_value TEXT NOT NULL DEFAULT '',
    min_value   REAL,
    max_value   REAL,
    show_if     TEXT NOT NULL DEFAULT '',   -- e.g. "bedrock.enabled=yes": only when that answer has that value
    required    INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS setup_invites (
    token_hash  TEXT PRIMARY KEY,
    plan        TEXT NOT NULL,              -- free | pro
    addons      TEXT NOT NULL DEFAULT '[]', -- JSON list: antixray, antiesp
    license_key TEXT,                       -- key the answers configure (optional: can be chosen later)
    discord_id  TEXT,                       -- only this Discord account may use it (optional)
    note        TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT,
    used_by     TEXT,
    revoked_at  TEXT
);
CREATE TABLE IF NOT EXISTS setup_answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT NOT NULL,
    discord_id  TEXT NOT NULL,
    plan        TEXT NOT NULL,
    license_key TEXT,
    answers     TEXT NOT NULL,              -- JSON {config_key: value}
    server_ip   TEXT,
    created_at  TEXT NOT NULL,
    applied_at  TEXT,                       -- config accepted by the license server
    apply_error TEXT
);
"""

YES, NO = "yes", "no"
LANGS = [["en", "English"], ["it", "Italiano"], ["es", "Español"], ["pt", "Português"], ["de", "Deutsch"],
         ["fr", "Français"], ["pl", "Polski"], ["zh", "中文"]]

# (plan, kind, config_key, label, help, warning, options, default, min, max, show_if)
DEFAULT_QUESTIONS = [
    ("free", "choice", "language", "Language of the plugin messages", "", "", LANGS, "en", None, None, ""),
    ("free", "choice", "info.server_type", "What kind of server do you run?", "Only for us: it helps us suggest the right sensitivity.", "",
     [["survival", "Survival"], ["pvp", "PvP / Practice"], ["minigames", "Minigames"], ["network", "Network"], ["other", "Other"]], "survival", None, None, ""),
    ("free", "choice", "info.players", "How many players are usually online?", "", "",
     [["lt50", "Fewer than 50"], ["50-200", "50 to 200"], ["gt200", "More than 200"]], "lt50", None, None, ""),
    ("free", "ip", "info.server_ip", "Numeric IP address of your Minecraft server",
     "Numbers only, e.g. 123.45.67.89 (IPv6 also works), not a domain name. Your license will work only from this address.", "", [], "", None, None, ""),
    ("free", "choice", "database.type", "Where should Mythras save the violations?", "", "",
     [["file", "Files (default)"], ["sqlite", "SQLite database"]], "file", None, None, ""),
    ("free", "yesno", "alerts.console", "Show alerts in the server console too?", "", "", [], YES, None, None, ""),
    ("free", "choice", "alerts.cooldown-ms", "How often can the same alert repeat?", "Per player and per check.", "",
     [["1000", "At most once per second"], ["3000", "At most once every 3 seconds"], ["10000", "At most once every 10 seconds"]], "1000", None, None, ""),
    ("free", "yesno", "client-info.notify-staff", "Warn staff when a player joins with a suspicious client (e.g. Meteor, Wurst)?", "", "", [], YES, None, None, ""),
    ("pro", "webhook", "discord.webhook-url", "Discord webhook for alerts",
     "Optional. Channel settings, Integrations, Webhooks, Copy URL. Empty = alerts only in game.", "", [], "", None, None, ""),
    ("pro", "number", "discord.min-vl", "From which violation level should alerts go to Discord?", "", "", [], "5", 1, 100, ""),
    ("pro", "yesno", "discord.punishments", "Send punishments to Discord too?", "", "", [], YES, None, None, ""),
    ("pro", "yesno", "punishments.enabled", "Turn on automatic punishments?",
     "We suggest No for the first days: watch the alerts first, then turn punishments on.", "", [], NO, None, None, ""),
    ("pro", "text", "punishments.command", "Punishment command", "Run from the console. Placeholders: %player%, %check%, %vl%.", "", [], "ban %player% 7d Cheating", None, None, "punishments.enabled=yes"),
    ("pro", "text", "punishments.kick-message", "Message shown to the punished player", "Optional.", "", [], "", None, None, "punishments.enabled=yes"),
    ("pro", "yesno", "punishments.broadcast", "Announce punishments in the chat to everyone?", "", "", [], YES, None, None, "punishments.enabled=yes"),
    ("pro", "choice", "checks.sensitivity", "How strict should the checks be?", "", "",
     [["soft", "Soft (fewer false flags)"], ["normal", "Normal"], ["strict", "Strict"]], "normal", None, None, ""),
    ("pro", "yesno", "setback.enabled", "Pull cheaters back when they move illegally (setback)?", "", "", [], YES, None, None, ""),
    ("pro", "yesno", "checks.freecam.cancel", "Block clicks through walls (FreeCam, GhostHand)?", "", "", [], YES, None, None, ""),
    ("pro", "yesno", "checks.xray.enabled", "Detect X-Ray mining (alerts only)?", "", "", [], YES, None, None, ""),
    ("pro", "yesno", "bedrock.enabled", "Do Bedrock players join your server (Geyser/Floodgate)?", "", "", [], NO, None, None, ""),
    ("pro", "choice", "bedrock.reach-extra", "Extra reach tolerance for Bedrock players", "", "",
     [["0.3", "0.3 blocks"], ["0.5", "0.5 blocks"], ["1.0", "1.0 block"]], "0.5", None, None, "bedrock.enabled=yes"),

    ("antixray", "yesno", "mitigations.anti-xray.enabled", "Turn on Anti-XRay by default?", "Anti-XRay add-on: hides ores the player cannot see.", "It can cause a little lag.", [], NO, None, None, ""),
    ("antixray", "number", "mitigations.anti-xray.max-y", "Hide ores up to which height (Y)?", "", "", [], "64", -64, 320, "mitigations.anti-xray.enabled=yes"),
    ("antiesp", "yesno", "mitigations.anti-esp.enabled", "Anti-ESP: hide players behind walls?", "", "", [], YES, None, None, ""),
    ("antiesp", "yesno", "mitigations.anti-storage-esp.enabled", "Anti-StorageESP: hide chests and containers behind walls?", "", "", [], YES, None, None, ""),
    ("pro", "yesno", "mitigations.health-filter", "Hide other players' health (against health indicators)?", "", "", [], YES, None, None, ""),
    ("pro", "yesno", "mitigations.combat.cancel-impossible-hits", "Cancel impossible hits (beyond reach, through walls)?", "", "", [], YES, None, None, ""),
    ("pro", "yesno", "mitigations.combat.damage-penalty", "Halve the damage of a player right after a combat flag?", "", "", [], YES, None, None, ""),
    ("pro", "choice", "replay.mode", "What should replays record?", "", "",
     [["flag", "Only flagged players"], ["always", "Always the last 25 seconds of everyone (uses more memory)"]], "flag", None, None, ""),
    ("pro", "number", "replay.keep-days", "Keep replays for how many days?", "", "", [], "7", 1, 7, ""),
    ("pro", "list", "exemptions.worlds", "Worlds where no check runs", "Optional, separated by commas (e.g. minigames, event).", "", [], "", None, None, ""),
    ("pro", "list", "exemptions.regions", "WorldGuard regions where no check runs", "Optional, separated by commas (launch pads, parkour).", "", [], "", None, None, ""),
    ("pro", "list", "exemptions.players", "Players who are never checked", "Optional: names or UUIDs separated by commas (staff, test accounts).", "", [], "", None, None, ""),
    ("pro", "yesno", "ai.enabled", "Turn on AI Learning?", "", "", [], YES, None, None, ""),

    ("pro", "yesno", "ai.cloud.enabled", "Turn on Mythras Cloud AI?", "", "", [], YES, None, None, ""),
    ("pro", "yesno", "ai.cloud.share", "Share anonymous combat data to improve Cloud AI?", "Never names, UUIDs or IP addresses.", "", [], YES, None, None, "ai.cloud.enabled=yes"),
    ("pro", "yesno", "ai.can-punish", "Can the AI punish on its own?", "", "", [], NO, None, None, ""),
    ("pro", "choice", "ai.sensitivity", "How sensitive should the AI be?", "", "",
     [["cautious", "Cautious (flags only when very sure)"], ["normal", "Normal"], ["aggressive", "Aggressive"]], "normal", None, None, ""),
    ("pro", "text", "network.server-name", "BungeeCord/Velocity network: name of this server",
     "Optional, e.g. lobby. Empty = no network.", "", [], "", None, None, ""),
    ("pro", "yesno", "info.placeholderapi", "Do you use PlaceholderAPI?", "Only for us.", "", [], NO, None, None, ""),
]


# validation messages (shown translated next to the field)
ERRORS = (
    "Enter a number.", "Out of range.", "Enter the numeric IP address of your server.",
    "Enter a numeric IP address, e.g. 123.45.67.89 (not a domain name).",
    "Use names separated by commas (letters, numbers, - _ . : only).",
    "Paste a Discord webhook URL (https://discord.com/api/webhooks/...), or leave it empty.",
    "This answer is required.",
)


def texts():
    """English strings of the default questions and validation messages (translated in translations/*.json)."""
    out = set(ERRORS)
    for q in DEFAULT_QUESTIONS:
        out.update(v for v in (q[3], q[4], q[5]) if v)
        out.update(label for value, label in q[6] if q[2] != "language")
    return out


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def init(db):
    db.executescript(SCHEMA)
    if "addons" not in {r[1] for r in db.execute("PRAGMA table_info(setup_invites)")}:
        db.execute("ALTER TABLE setup_invites ADD COLUMN addons TEXT NOT NULL DEFAULT '[]'")
    if "addons" not in {r[1] for r in db.execute("PRAGMA table_info(setup_answers)")}:
        db.execute("ALTER TABLE setup_answers ADD COLUMN addons TEXT NOT NULL DEFAULT '[]'")
    if db.execute("SELECT COUNT(*) FROM setup_questions").fetchone()[0] == 0:
        reset_defaults(db)
    migrate_plans(db)


def migrate_plans(db):
    """Plans before 2026-10 were base / pro / enterprise: map questions, links and answers to free / pro + add-ons."""
    defaults = {q[2]: q[0] for q in DEFAULT_QUESTIONS}
    for row in db.execute("SELECT id, config_key FROM setup_questions WHERE plan IN ('base', 'enterprise')"
                          " OR (plan = 'pro' AND config_key LIKE 'mitigations.anti-%')").fetchall():
        db.execute("UPDATE setup_questions SET plan = ? WHERE id = ?", (defaults.get(row[1], "pro"), row[0]))
    db.execute("UPDATE setup_invites SET plan = 'pro' WHERE plan IN ('base', 'enterprise')")
    db.execute("UPDATE setup_answers SET plan = 'pro' WHERE plan IN ('base', 'enterprise')")
    db.commit()


def reset_defaults(db):
    db.execute("DELETE FROM setup_questions")
    for pos, (plan, kind, key, label, help_, warning, options, default, lo, hi, show_if) in enumerate(DEFAULT_QUESTIONS, 1):
        db.execute(
            "INSERT INTO setup_questions (position, plan, kind, config_key, label, help, warning, options, default_value,"
            " min_value, max_value, show_if) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pos * 10, plan, kind, key, label, help_, warning, json.dumps(options), default, lo, hi, show_if),
        )
    db.commit()


def _row(r):
    q = dict(r)
    try:
        q["options"] = json.loads(q["options"] or "[]")
    except ValueError:
        q["options"] = []
    return q


def all_questions(db):
    return [_row(r) for r in db.execute("SELECT * FROM setup_questions ORDER BY position, id")]


def sees(requirement, plan, addons):
    """Does a key with this plan and these add-ons get a question with this requirement?"""
    return requirement == "free" or (requirement == "pro" and plan == "pro") or requirement in (addons or ())


def questions_for(db, plan, addons=()):
    return [q for q in all_questions(db) if q["enabled"] and sees(q["plan"], plan, addons)]


def invite_addons(row):
    try:
        return [a for a in json.loads(row["addons"] or "[]") if a in ADDONS]
    except (ValueError, TypeError, IndexError, KeyError):
        return []


def _visible(q, answers):
    if not q["show_if"] or "=" not in q["show_if"]:
        return True
    key, value = q["show_if"].split("=", 1)
    return str(answers.get(key.strip(), "")) == value.strip()


def _number(raw):
    value = float(raw)
    return int(value) if value.is_integer() else value


def validate(questions, form):
    """Form -> (answers {config_key: python value}, errors {config_key: English message})."""
    raw = {q["config_key"]: (form.get(q["config_key"]) or "").strip() for q in questions}
    answers, errors = {}, {}
    for q in questions:  # questions are ordered, so a show_if always refers to an earlier answer
        key, value, kind = q["config_key"], raw[q["config_key"]], q["kind"]
        if not _visible(q, {k: ("yes" if v is True else "no" if v is False else v) for k, v in answers.items()}):
            continue
        if kind == "yesno":
            if value not in (YES, NO):
                value = q["default_value"] or NO
            answers[key] = value == YES
        elif kind == "choice":
            allowed = [str(o[0]) for o in q["options"]]
            if value not in allowed:
                value = q["default_value"] if q["default_value"] in allowed else (allowed[0] if allowed else "")
            answers[key] = _number(value) if re.fullmatch(r"-?\d+(\.\d+)?", value) else value
        elif kind == "number":
            try:
                number = _number(value if value else q["default_value"])
            except ValueError:
                errors[key] = "Enter a number."
                continue
            if (q["min_value"] is not None and number < q["min_value"]) or (q["max_value"] is not None and number > q["max_value"]):
                errors[key] = "Out of range."
                continue
            answers[key] = number
        elif kind == "ip":
            if not value:
                if q["required"] or key == "info.server_ip":
                    errors[key] = "Enter the numeric IP address of your server."
                continue
            try:
                answers[key] = str(ipaddress.ip_address(value))
            except ValueError:
                errors[key] = "Enter a numeric IP address, e.g. 123.45.67.89 (not a domain name)."
        elif kind == "list":
            items = [i.strip() for i in value.split(",") if i.strip()]
            bad = [i for i in items if not LIST_ITEM_RE.match(i)]
            if bad or len(items) > 50:
                errors[key] = "Use names separated by commas (letters, numbers, - _ . : only)."
                continue
            answers[key] = items
        elif kind == "webhook":
            if value and not WEBHOOK_RE.match(value):
                errors[key] = "Paste a Discord webhook URL (https://discord.com/api/webhooks/...), or leave it empty."
                continue
            answers[key] = value
        else:  # text
            value = re.sub(r"[\x00-\x1f\x7f]", "", value)[:MAX_TEXT]
            if not value and q["required"]:
                errors[key] = "This answer is required."
                continue
            answers[key] = value if value else q["default_value"]
    return answers, errors


def plugin_config(answers):
    """The answers the plugin uses (info.* stays on the website)."""
    return {k: v for k, v in answers.items() if not k.startswith("info.")}


# ---------- invites ----------

def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_invite(db, plan, created_by, license_key=None, discord_id=None, note="", hours=168, addons=()):
    if plan not in PLANS:
        raise ValueError("Choose a valid plan.")
    if any(a not in ADDONS for a in addons):
        raise ValueError("Choose valid add-ons.")
    addons = sorted(set(addons))
    if plan == "free" and not addons:
        raise ValueError("A Free link needs at least one add-on.")
    token = secrets.token_urlsafe(24)
    db.execute(
        "INSERT INTO setup_invites (token_hash, plan, addons, license_key, discord_id, note, created_by, created_at,"
        " expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_hash(token), plan, json.dumps(addons), license_key or None, discord_id or None, (note or "")[:200], created_by,
         iso(now()), iso(now() + timedelta(hours=max(1, min(int(hours), 24 * 60))))),
    )
    db.commit()
    return token


def find_invite(db, token):
    if not token or len(token) > 64:
        return None
    return db.execute("SELECT * FROM setup_invites WHERE token_hash = ?", (_hash(token),)).fetchone()


def invite_state(row):
    if row is None:
        return "unknown"
    if row["revoked_at"]:
        return "revoked"
    if row["used_at"]:
        return "used"
    if datetime.fromisoformat(row["expires_at"]) < now():
        return "expired"
    return "open"


def consume_invite(db, row, discord_id):
    """Mark the invite used, exactly once (safe against double submits)."""
    cur = db.execute(
        "UPDATE setup_invites SET used_at = ?, used_by = ? WHERE token_hash = ? AND used_at IS NULL AND revoked_at IS NULL"
        " AND expires_at > ?",
        (iso(now()), discord_id, row["token_hash"], iso(now())),
    )
    db.commit()
    return cur.rowcount == 1
