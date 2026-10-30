"""Mythras AntiCheat website: Discord login, customer area and admin dashboard.

Licenses live on the Mythras license server; this app talks to it only from the
backend through mythras_api (the API token never reaches the browser).
"""

import ipaddress
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from markupsafe import Markup
from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    send_file,
    render_template,
    request,
    session,
    url_for,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

import admin_2fa  # noqa: E402
import i18n  # noqa: E402
import mythras_api  # noqa: E402  (reads env after load_dotenv)
import downloads  # noqa: E402
import setup_forms  # noqa: E402
import docs  # noqa: E402
import changelog as releases  # noqa: E402
import ratelimit  # noqa: E402
from mythras_api import MythrasError  # noqa: E402

log = logging.getLogger("mythras.site")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DISCORD_API = "https://discord.com/api"
DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv(
    "DISCORD_REDIRECT_URI", "http://localhost:5000/auth/discord/callback"
)
ADMIN_IDS = {x.strip() for x in os.getenv("ADMIN_DISCORD_IDS", "").split(",") if x.strip()}
DATABASE = Path(os.getenv("DATABASE_PATH", BASE_DIR / "data" / "mythras.db"))

# Discord server invite: support, and purchases by ticket (the PayPal shop is run by the license server, /api/shop)
SUPPORT_DISCORD = os.getenv("SUPPORT_DISCORD_URL", "")
# the Discord bot creates setup links with POST /api/bot/invites and this token (empty = bot API off)
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "")
# Free version download pages (only https links are shown)
FREE_LINKS = [
    {"name": name, "url": os.getenv(env, "").strip()}
    for name, env in (("Modrinth", "FREE_URL_MODRINTH"), ("SpigotMC", "FREE_URL_SPIGOT"),
                      ("BuiltByBit (MC-Market)", "FREE_URL_MCMARKET"))
    if os.getenv(env, "").strip().startswith("https://")
]
# shown on the Privacy Policy (GDPR art. 13): who runs the site and how to reach them
LEGAL_CONTROLLER = os.getenv("LEGAL_CONTROLLER", "")
LEGAL_EMAIL = os.getenv("LEGAL_EMAIL", "")
LEGAL_VAT = os.getenv("LEGAL_VAT", "")  # partita IVA, shown in the footer and on the legal pages when set

KEY_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,63}$")
DISCORD_ID_RE = re.compile(r"^\d{17,20}$")
KEY_STATUSES = ("active", "revoked", "expired")
DEFAULT_PLANS = [{"plan": "pro"}]
DAY_MS = 86_400_000

# Marketing copy for the pricing page. Prices and features: edit them here.
TIERS = {
    "free": {
        "name": "Free",
        "price": "€0",
        "period": "forever",
        "description": "Install and play. No license key needed.",
        "features": [
            "No license key needed",
            "Basic movement checks: Speed, Flight/NoFall, Jesus",
            "Crash and BadPackets protection",
            "Alerts only: no setbacks, no punishments",
            "Add-ons available",
            "Community support",
        ],
    },
    "pro": {
        "name": "Pro",
        "price": "€19.99",
        "period": "per month",
        "description": "Every check and every feature of Mythras, for any server.",
        "features": [
            "All checks: movement, combat, Crystal PvP, world and protocol",
            "Setbacks, automatic punishments, staff GUI, Discord webhook",
            "AI Learning + Mythras Cloud AI (global model)",
            "Replays kept for 7 days",
            "Whitelist and exemptions, combat mitigations, health filter",
            "Folia, cross-server alerts, developer API, PlaceholderAPI",
            "1 server per key",
            "Add-ons available",
            "Priority support",
            "Monthly PayPal subscription, cancel any time",
        ],
    },
}

# Add-ons: bought once on top of Free or Pro, they never expire (they stay on the key even after Pro expires).
ADDONS = {
    "antixray": {
        "name": "Anti-XRay",
        "price": "€5.99",
        "period": "one-time, forever",
        "description": "Ores out of sight are sent as rock, also those open to caves, until the player can see them.",
        "features": [
            "Hides ores behind walls and in caves",
            "Pay once, yours forever",
            "Works with Free and Pro",
            "Height and blocks configurable",
        ],
    },
    "antiesp": {
        "name": "Anti-ESP",
        "price": "€5.99",
        "period": "one-time, forever",
        "description": "Players and containers behind walls are not sent: ESP, tracers, chams, radar and StorageESP see nothing.",
        "features": [
            "Hides players behind walls (Anti-ESP)",
            "Hides chests and containers behind walls (Anti-StorageESP)",
            "Pay once, yours forever",
            "Works with Free and Pro",
        ],
    },
}
ADDON_IDS = tuple(ADDONS)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    discord_id TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    avatar     TEXT,
    email      TEXT,
    created_at TEXT NOT NULL,
    last_login TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    order_id     TEXT PRIMARY KEY,   -- PayPal order id, or sub-<subscription id> (idempotency key)
    order_number TEXT,
    discord_id   TEXT NOT NULL,
    plan         TEXT NOT NULL,
    license_key  TEXT,
    status       TEXT NOT NULL,      -- processing | fulfilled | refunded
    test_mode    INTEGER NOT NULL DEFAULT 0,
    consent_at   TEXT,               -- when the buyer accepted the terms + immediate delivery
    subscription_id TEXT UNIQUE,     -- PayPal subscription (monthly plans)
    sub_status   TEXT,               -- active | on_trial | past_due | unpaid | cancelled | expired | paused
    renews_at    TEXT,
    ends_at      TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""

PRODUCTION = os.getenv("COOKIE_SECURE", "0") == "1"
if PRODUCTION and not os.getenv("SECRET_KEY"):
    # every gunicorn worker would invent its own key and sessions would break
    raise RuntimeError("SECRET_KEY must be set in production (COOKIE_SECURE=1).")
if PRODUCTION and ADMIN_IDS and not admin_2fa.configured():
    raise RuntimeError("TOTP_ENC_KEY must be set to a valid key when ADMIN_DISCORD_IDS is used.")

MFA_MAX_AGE = 12 * 3600      # a verified admin session lasts at most 12 hours
MFA_FRESH = 15 * 60          # sensitive actions need a code verified in the last 15 minutes

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=PRODUCTION,
    MAX_CONTENT_LENGTH=64 * 1024,
)

if os.getenv("TRUST_PROXY", "0") == "1":
    # behind nginx on the same machine: trust one hop of X-Forwarded-* headers
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


# ---------- helpers ----------

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_ms():
    return int(time.time() * 1000)


def get_db():
    if "db" not in g:
        DATABASE.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE)
    db.executescript(SCHEMA)
    db.executescript(admin_2fa.SCHEMA)
    setup_forms.init(db)
    ratelimit.init(db)
    have = {r[1] for r in db.execute("PRAGMA table_info(orders)")}
    for col in ("subscription_id", "sub_status", "renews_at", "ends_at"):
        if col not in have:  # databases created before monthly plans
            db.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS orders_subscription ON orders (subscription_id)")
    db.execute("UPDATE users SET email = NULL WHERE email IS NOT NULL")  # e-mail no longer collected
    db.commit()
    db.close()


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def current_user():
    if "user" not in g:
        discord_id = session.get("discord_id")
        row = None
        if discord_id:
            row = get_db().execute(
                "SELECT * FROM users WHERE discord_id = ?", (discord_id,)
            ).fetchone()
        g.user = row
    return g.user


def is_admin(user):
    return user is not None and user["discord_id"] in ADMIN_IDS


def actor_name():
    user = current_user()
    return f"{user['username']} ({user['discord_id']})"


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("index"))
        return view(*args, **kwargs)

    return wrapper


def mfa_age():
    """Seconds since this session last passed 2FA for the current admin, or None."""
    mfa = session.get("mfa")
    user = current_user()
    if not mfa or not user or mfa.get("uid") != user["discord_id"]:
        return None
    return int(time.time()) - int(mfa.get("at", 0))


def safe_next(value=None):
    """Only allow redirects back to admin pages on this site."""
    if value is None:
        if request.method == "GET":
            value = request.full_path.rstrip("?")
        else:
            ref = urllib.parse.urlsplit(request.referrer or "")
            same_host = not ref.netloc or ref.netloc == request.host
            value = (ref.path + (f"?{ref.query}" if ref.query else "")) if same_host else ""
    if value.startswith("/admin") and not value.startswith("//") and "\\" not in value:
        return value
    return url_for("admin")


def admin_identity_required(view):
    """Admin Discord account, without the 2FA check (used by the 2FA pages themselves)."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_admin(current_user()):
            abort(403)
        if not admin_2fa.configured():
            return render_template(
                "error.html", code=503, message=t("Two-factor authentication is not configured on this website.")
            ), 503
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    """Admin account + 2FA verified in this session within the last 12 hours.

    Checked on the server for every admin page and every admin POST route.
    """
    @wraps(view)
    @admin_identity_required
    def wrapper(*args, **kwargs):
        uid = current_user()["discord_id"]
        if not admin_2fa.is_enrolled(admin_2fa.get_row(get_db(), uid)):
            return redirect(url_for("admin_2fa_setup"))
        age = mfa_age()
        if age is None or age > MFA_MAX_AGE:
            session.pop("mfa", None)
            return redirect(url_for("admin_2fa_verify", next=safe_next()))
        return view(*args, **kwargs)

    return wrapper


def stale_for_sensitive_action():
    """Redirect response if the last 2FA check is older than 15 minutes, else None."""
    age = mfa_age()
    if age is not None and age <= MFA_FRESH:
        return None
    flash(t("For security, enter a fresh code, then repeat the action."), "error")
    return redirect(url_for("admin_2fa_verify", next=safe_next(), fresh=1))


def check_code_with_lockout(uid, check):
    """Run a 2FA check with the 5-attempt / 15-minute lockout. Returns (result, error_message)."""
    db = get_db()
    row = admin_2fa.ensure_row(db, uid)
    wait = admin_2fa.locked_seconds(row)
    if wait:
        return None, t("Too many wrong codes. Try again in %(n)s minutes.", n=math.ceil(wait / 60))
    result = check()
    if result:
        admin_2fa.reset_failures(db, uid)
        return result, None
    left, locked = admin_2fa.register_failure(db, uid)
    log.warning("2FA failed for admin %s from %s (%s)", uid, request.remote_addr,
                "locked for 15 minutes" if locked else f"{left} attempt{'s' if left != 1 else ''} left")
    if locked:
        return None, t("Too many wrong codes. 2FA is locked for 15 minutes.")
    if left == 1:
        return None, t("Wrong or already used code. 1 attempt left.")
    return None, t("Wrong or already used code. %(n)s attempts left.", n=left)


def normalize_key(value):
    key = (value or "").strip().upper()
    return key if KEY_RE.match(key) else None


def parse_int(raw, minimum, message, maximum=None, default=None):
    raw = (raw or "").strip()
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(message) from None
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(message)
    return value


_plans_cache = {"at": 0.0, "data": None}


def get_plans():
    """Plans from the license server, cached for a minute; defaults if unreachable."""
    if _plans_cache["data"] is None or time.time() - _plans_cache["at"] > 60:
        try:
            _plans_cache["data"] = mythras_api.plans()
            _plans_cache["at"] = time.time()
        except MythrasError:
            return {"plans": DEFAULT_PLANS}
    return _plans_cache["data"]


def plan_ids():
    return [p["plan"] for p in get_plans().get("plans", DEFAULT_PLANS)]


def known_names():
    """Discord ID -> username for people who have logged in to this website."""
    rows = get_db().execute("SELECT discord_id, username FROM users").fetchall()
    return {r["discord_id"]: r["username"] for r in rows}


def _discord_request(url, data=None, headers=None):
    headers = dict(headers or {})
    headers["User-Agent"] = "MythrasAntiCheatSite (1.0)"
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def avatar_url(user_json):
    avatar = user_json.get("avatar")
    if avatar:
        return f"https://cdn.discordapp.com/avatars/{user_json['id']}/{avatar}.png"
    return None


# ---------- CSRF protection and template helpers ----------

def allow_large_upload():
    if request.path == "/admin/downloads/upload":
        request.max_content_length = downloads.MAX_UPLOAD


app.before_request_funcs.setdefault(None, []).insert(0, allow_large_upload)


@app.before_request
def check_csrf():
    # the bot API has no cookies: it is authenticated by its own token instead
    if request.method == "POST" and not request.path.startswith("/api/bot/"):
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(sent, expected):
            abort(400)


def csp_nonce():
    if "csp_nonce" not in g:
        g.csp_nonce = secrets.token_urlsafe(16)
    return g.csp_nonce


@app.after_request
def security_headers(resp):
    csp = [
        "default-src 'self'",
        f"script-src 'self' 'nonce-{csp_nonce()}'",
        "style-src 'self' 'unsafe-inline'",
        "font-src 'self'",
        "img-src 'self' data: https://cdn.discordapp.com",
        "media-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self' " + " ".join(PAYPAL_HOSTS),  # "Buy" posts here, then we redirect to PayPal
        "frame-ancestors 'none'",
    ]
    if PRODUCTION:
        csp.append("upgrade-insecure-requests")
    resp.headers.setdefault("Content-Security-Policy", "; ".join(csp))
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if PRODUCTION:  # browsers use HTTPS only for a year, also for license.mythras.eu
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.path.startswith(("/admin", "/dashboard", "/download", "/panel")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def t(text, **values):
    """Translate an English UI string into the visitor's language."""
    lang = g.get("lang") or i18n.pick(request)
    return i18n.gettext(lang, text, **values)


@app.before_request
def set_language():
    g.lang = i18n.pick(request)


RATE_RULES = {  # endpoint -> rule of ratelimit.RULES; everything else: "post" for forms, "page" otherwise
    "login": "login", "discord_callback": "login", "claim_key": "claim", "shop_buy": "buy",
    "shop_done_check": "shop_check", "setup_page": "setup", "self_setup": "setup", "bot_create_invite": "bot",
    "download_plugin": "download", "download_proxy": "download", "player_head": "heads",
}
RATE_FREE = {"static", "healthz"}  # assets (cached by Cloudflare) and the uptime monitor


@app.before_request
def rate_limit():
    if request.endpoint in RATE_FREE or request.endpoint is None:
        return None
    rule = RATE_RULES.get(request.endpoint) or ("post" if request.method == "POST" else "page")
    uid = session.get("discord_id")
    who = f"u:{uid}" if uid and rule not in ("login", "bot", "page") else f"ip:{request.remote_addr}"
    wait = ratelimit.hit(get_db(), rule, who)
    if not wait:
        return None
    log.warning("Rate limit %s for %s on %s", rule, who, request.path)
    if request.path.startswith(("/api/", "/shop/done/check", "/panel/feed", "/admin/panel/feed")):
        resp = jsonify(ok=False, error="too many requests")
    else:
        resp = app.make_response((render_template(
            "error.html", code=429, message=t("Too many requests. Please wait %(n)s seconds and try again.", n=wait)), 429))
    resp.status_code = 429
    resp.headers["Retry-After"] = str(wait)
    return resp


@app.url_defaults
def static_cache_bust(endpoint, values):
    """Add ?v=<last change> to static URLs, so browsers and Cloudflare fetch new CSS/JS after an update."""
    if endpoint == "static" and "filename" in values:
        try:
            values["v"] = int((Path(app.static_folder) / values["filename"]).stat().st_mtime)
        except OSError:
            pass


@app.template_filter("date")
def format_date(value):
    if not value:
        return ""
    try:
        fmt = "%d %b %Y" if g.get("lang", "en") == "en" else "%d/%m/%Y"
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime(fmt)
    except ValueError:
        return value


@app.template_filter("ms_date")
def format_ms(value, never="Never"):
    if not value:
        return t(never) if never == "Never" else never
    fmt = "%d %b %Y, %H:%M UTC" if g.get("lang", "en") == "en" else "%d/%m/%Y %H:%M UTC"
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime(fmt)


@app.template_filter("ms_iso")
def format_ms_iso(value):
    """Epoch ms -> ISO 8601 UTC, for <time datetime>; the browser shows it in local time."""
    if not value:
        return ""
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@app.template_filter("ms_day")
def format_ms_day(value):
    if not value:
        return ""
    fmt = "%d %b %Y" if g.get("lang", "en") == "en" else "%d/%m/%Y"
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime(fmt)


@app.template_filter("ms_day_short")
def format_ms_day_short(value):
    if not value:
        return ""
    fmt = "%d %b" if g.get("lang", "en") == "en" else "%d/%m"
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime(fmt)


@app.template_filter("ago")
def format_ago(value):
    if not value:
        return t("never")
    seconds = max(0, (now_ms() - value) // 1000)
    for size, label in ((86400, "%(n)sd ago"), (3600, "%(n)sh ago"), (60, "%(n)sm ago")):
        if seconds >= size:
            return t(label, n=seconds // size)
    return t("just now")


@app.template_filter("plan_name")
def plan_name(plan):
    return TIERS.get(plan, {}).get("name") or ADDONS.get(plan, {}).get("name") or str(plan or "").title()


def addon_list(value):
    """Add-ons of a key or setup link: a list from the license server, or JSON text from the site DB."""
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except ValueError:
            return []
    return [a for a in (value or []) if a in ADDONS]


@app.template_filter("addon_badges")
def addon_badges(value):
    return Markup("".join(f' <span class="badge badge-addon">+ {Markup.escape(ADDONS[a]["name"])}</span>'
                          for a in addon_list(value)))


def addons_from_form(form):
    picked = form.getlist("addons")
    if any(a not in ADDONS for a in picked):
        raise ValueError("Choose valid add-ons.")
    return sorted(set(picked))


@app.context_processor
def inject_globals():
    return {
        "csrf_token": csrf_token(),
        "SHOP": shop_info(),
        "cl_split": releases.split,
        "cl_render": releases.render,
        "cl_date": lambda value: releases.format_date(value, g.get("lang", "en")),
        "price_of": price_of,
        "csp_nonce": csp_nonce(),
        "current_user": current_user(),
        "is_admin": is_admin(current_user()),
        "now_year": datetime.now(timezone.utc).year,
        "_": t,
        "lang": g.get("lang", i18n.DEFAULT),
        "LANGUAGES": i18n.LANGUAGES,
        "cookie_notice_seen": request.cookies.get("cookie_notice") == "1",
        "LEGAL_CONTROLLER": LEGAL_CONTROLLER,
        "LEGAL_EMAIL": LEGAL_EMAIL,
        "LEGAL_VAT": LEGAL_VAT,
        "TIERS": TIERS,
        "ADDONS": ADDONS,
        "SUPPORT_DISCORD": SUPPORT_DISCORD,
        "FREE_LINKS": FREE_LINKS,
    }


# ---------- public pages ----------

@app.get("/")
def index():
    return render_template("index.html", PLAN_MATRIX=docs.PLAN_MATRIX, news=releases.latest(g.get("lang", "en")))


def _local_next(default="/"):
    """Redirect target from ?next=, limited to paths on this site."""
    target = request.args.get("next", "")
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default


def _pref_cookie(resp, name, value):
    # preference cookies set at the visitor's request: strictly necessary, no consent needed
    resp.set_cookie(name, value, max_age=365 * 24 * 3600, samesite="Lax", secure=PRODUCTION, httponly=True)
    return resp


@app.get("/lang/<code>")
def set_lang(code):
    if code not in i18n.LANGUAGES:
        abort(404)
    return _pref_cookie(redirect(_local_next()), i18n.COOKIE, code)


@app.get("/cookies/ok")
def cookie_notice_ok():
    return _pref_cookie(redirect(_local_next()), "cookie_notice", "1")


@app.get("/privacy")
def privacy():
    return render_template("legal/privacy.html")


@app.get("/cookies")
def cookie_policy():
    return render_template("legal/cookies.html")


@app.get("/terms")
def terms():
    return render_template("legal/terms.html")


@app.get("/changelog")
def changelog():
    """Official changelog published by the license server (cached; see changelog.py)."""
    return render_template("changelog.html", versions=releases.versions(g.get("lang", "en")))


@app.get("/changelog.xml")
def changelog_rss():
    items = []
    for v in (releases.versions() or [])[:30]:
        d = releases.parse_date(v["date"])
        pub = d.strftime("%a, %d %b %Y 00:00:00 +0000") if d else ""
        link = f"https://mythras.eu/changelog#v{v['version']}"
        body = "\n".join("- " + releases.plain(c) for c in v["changes"])
        items.append(f"<item><title>{xml_escape('Mythras ' + v['version'])}</title><link>{xml_escape(link)}</link>"
                     f"<guid isPermaLink=\"true\">{xml_escape(link)}</guid>"
                     + (f"<pubDate>{pub}</pubDate>" if pub else "")
                     + f"<description>{xml_escape(body)}</description></item>")
    body = ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Mythras AntiCheat changelog</title>'
            '<link>https://mythras.eu/changelog</link><description>New versions of Mythras AntiCheat</description>'
            + "".join(items) + "</channel></rss>")
    resp = app.response_class(body, mimetype="application/rss+xml")
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


def xml_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


@app.get("/robots.txt")
def robots_txt():
    body = "\n".join([
        "User-agent: *",
        "Disallow: /admin",
        "Disallow: /dashboard",
        "Disallow: /panel",
        "Disallow: /download",
        "Disallow: /heads/",
        "Disallow: /setup/",
        "Disallow: /api/",
        "Disallow: /login",
        "Disallow: /auth/",
        f"Sitemap: {url_for('sitemap_xml', _external=True)}",
        "",
    ])
    return app.response_class(body, mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml():
    pages = ["index", "docs_page", "changelog", "terms", "privacy", "cookie_policy"]
    urls = "".join(f"<url><loc>{url_for(p, _external=True)}</loc></url>" for p in pages)
    body = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return app.response_class(body, mimetype="application/xml")


@app.get("/healthz")
def healthz():
    """For uptime monitors: 200 when the site, its database and the license server all answer, else 503."""
    checks = {"site": True}
    try:
        get_db().execute("SELECT 1").fetchone()
        checks["database"] = True
    except sqlite3.Error:
        checks["database"] = False
    try:
        mythras_api.plans()
        checks["license_server"] = True
    except MythrasError:
        checks["license_server"] = False
    ok = all(checks.values())
    resp = jsonify(ok=ok, **checks)
    resp.headers["Cache-Control"] = "no-store"
    return resp, (200 if ok else 503)


@app.get("/docs")
def docs_page():
    return render_template("docs.html", DOCS=docs)


@app.get("/wiki")
def wiki_redirect():
    return redirect(url_for("docs_page"), code=301)


# ---------- Discord login / registration ----------

@app.get("/login")
def login():
    """Login and registration: on Discord the account is created on first sign-in."""
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET:
        flash(t("Discord login is not configured on the server yet."), "error")
        return redirect(url_for("index"))
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    session["login_next"] = _local_next("")
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    }
    return redirect(f"{DISCORD_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}")


@app.get("/auth/discord/callback")
def discord_callback():
    expected_state = session.pop("oauth_state", None)
    next_url = session.pop("login_next", "")
    state = request.args.get("state", "")
    if not expected_state or not secrets.compare_digest(state, expected_state):
        abort(400)
    if request.args.get("error"):
        flash(t("Discord sign-in was cancelled."), "error")
        return redirect(url_for("index"))
    code = request.args.get("code")
    if not code:
        abort(400)

    try:
        token = _discord_request(
            f"{DISCORD_API}/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            },
        )
        me = _discord_request(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
    except (urllib.error.URLError, KeyError, ValueError):
        flash(t("Could not complete Discord sign-in. Please try again."), "error")
        return redirect(url_for("index"))

    db = get_db()
    db.execute(
        """
        INSERT INTO users (discord_id, username, avatar, email, created_at, last_login)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(discord_id) DO UPDATE SET
            username = excluded.username,
            avatar = excluded.avatar,
            email = excluded.email,
            last_login = excluded.last_login
        """,
        (
            me["id"],
            me.get("global_name") or me["username"],
            avatar_url(me),
            None,  # e-mail not requested (data minimisation)
            now_iso(),
            now_iso(),
        ),
    )
    db.commit()

    # regenerate the session to prevent session fixation
    session.clear()
    session["discord_id"] = me["id"]
    flash(t("Signed in with Discord."), "success")
    return redirect(next_url or url_for("dashboard"))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ---------- customer area (Discord ID only ever comes from the session) ----------

@app.get("/dashboard")
@login_required
def dashboard():
    user = current_user()
    keys, reset_hours, api_error = [], None, None
    try:
        data = mythras_api.user_keys(user["discord_id"])
        keys = data.get("keys", [])
        reset_hours = data.get("resetHours")
    except MythrasError as err:
        api_error = err.message
    now = now_ms()
    for k in keys:
        wait = (k.get("nextReset") or 0) - now
        k["reset_wait_hours"] = math.ceil(wait / 3_600_000) if wait > 0 else 0
        left = (k.get("expires") or 0) - now
        k["days_left"] = math.ceil(left / DAY_MS) if k.get("expires") and left > 0 else 0
    active = [k for k in keys if k.get("status") == "active"]
    latest_jar = downloads.latest()
    purchases = []
    if not api_error:
        try:
            purchases = mythras_api.user_purchases(user["discord_id"])
        except MythrasError:
            purchases = []
    renews = {p["key"]: p for p in purchases if p.get("item") == "pro" and p.get("status") == "ACTIVE" and p.get("key")}
    return render_template(
        "dashboard.html",
        keys=keys,
        purchases=purchases,
        renews=renews,
        news=recent_release(),
        download=latest_jar if active else None,
        proxy=downloads.latest("proxy") if any(k.get("plan") == "pro" for k in active) else None,
        download_exists=latest_jar is not None,
        active_count=len(active),
        servers_online=sum(k.get("activeServers") or 0 for k in active),
        reset_hours=reset_hours,
        api_error=api_error,
    )


def recent_release(days=7):
    """The latest version if it came out in the last `days` days (customer-area banner)."""
    v = releases.latest(g.get("lang", "en"))
    d = releases.parse_date(v["date"]) if v else None
    if d and (datetime.now(timezone.utc).date() - d).days <= days:
        return v
    return None


@app.post("/dashboard/claim")
@login_required
def claim_key():
    user = current_user()
    key = normalize_key(request.form.get("key"))
    if not key:
        flash(t("Enter a valid license key, e.g. MYTH-ABCD-EFGH-JKLM-NPQR."), "error")
        return redirect(url_for("dashboard"))
    try:
        linked = mythras_api.user_claim(user["discord_id"], key, name=user["username"])
        flash(t("Key %(key)s is now linked to your account.", key=linked["key"]), "success")
    except MythrasError as err:
        flash(err.message, "error")
    return redirect(url_for("dashboard"))


@app.post("/dashboard/keys/<key>/reset")
@login_required
def reset_my_key(key):
    user = current_user()
    key = normalize_key(key)
    if not key:
        abort(404)
    try:
        mythras_api.user_reset(user["discord_id"], key, name=user["username"])
        flash(t("Servers reset for %(key)s. Start your server again to activate it.", key=key), "success")
    except MythrasError as err:
        flash(err.message, "error")
    return redirect(url_for("dashboard"))


# ---------- plugin download (license holders only) ----------

@app.get("/download")
@login_required
def download_plugin():
    user = current_user()
    try:
        keys = mythras_api.user_keys(user["discord_id"]).get("keys", [])
    except MythrasError as err:
        flash(err.message, "error")
        return redirect(url_for("dashboard"))
    if not any(k.get("status") == "active" for k in keys):
        flash(t("You need an active license to download the plugin."), "error")
        return redirect(url_for("dashboard", _anchor="plans"))
    jar = downloads.latest()
    if not jar:
        flash(t("No download is available yet. Please try again later."), "error")
        return redirect(url_for("dashboard"))
    log.info("Download %s by %s", jar["file"], user["discord_id"])
    resp = send_file(jar["path"], as_attachment=True, download_name=jar["download_name"],
                     mimetype="application/java-archive", max_age=0)
    resp.headers["X-Content-SHA256"] = jar["sha256"]
    return resp


@app.get("/download/proxy")
@login_required
def download_proxy():
    """MythrasProxy (Velocity / BungeeCord): network alerts and bans are a Pro feature."""
    user = current_user()
    try:
        keys = mythras_api.user_keys(user["discord_id"]).get("keys", [])
    except MythrasError as err:
        flash(err.message, "error")
        return redirect(url_for("dashboard"))
    if not any(k.get("status") == "active" and k.get("plan") == "pro" for k in keys):
        flash(t("MythrasProxy needs an active Pro license."), "error")
        return redirect(url_for("dashboard"))
    jar = downloads.latest("proxy")
    if not jar:
        flash(t("No download is available yet. Please try again later."), "error")
        return redirect(url_for("dashboard"))
    log.info("Download %s by %s", jar["file"], user["discord_id"])
    resp = send_file(jar["path"], as_attachment=True, download_name=jar["download_name"],
                     mimetype="application/java-archive", max_age=0)
    resp.headers["X-Content-SHA256"] = jar["sha256"]
    return resp


# ---------- setup links: single-use invites with the questions of a plan ----------
# An admin (or the Discord bot) creates a link; the customer signs in, answers, and the answers become the plugin
# config of their key on the license server. Each link works once.

INVITE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def setup_link(token):
    return url_for("setup_page", token=token, _external=True)


def apply_setup(db, answer_id, key, actor):
    """Send the plugin config of a set of answers to the license server, for this key. Returns an error or None."""
    row = db.execute("SELECT * FROM setup_answers WHERE id = ?", (answer_id,)).fetchone()
    config = setup_forms.plugin_config(json.loads(row["answers"]))
    try:
        mythras_api.key_action(key, "config", actor, config=config)
    except MythrasError as err:
        db.execute("UPDATE setup_answers SET license_key = ?, apply_error = ? WHERE id = ?", (key, err.message, answer_id))
        db.commit()
        return err.message
    db.execute("UPDATE setup_answers SET license_key = ?, applied_at = ?, apply_error = NULL WHERE id = ?",
               (key, now_iso(), answer_id))
    db.commit()
    return None


@app.route("/setup/<token>", methods=["GET", "POST"])
def setup_page(token):
    user = current_user()
    if user is None:
        return redirect(url_for("login", next=request.path))
    db = get_db()
    invite = setup_forms.find_invite(db, token)
    state = setup_forms.invite_state(invite)
    if state != "open":
        messages = {
            "used": t("This setup link was already used. Ask for a new one on our Discord server."),
            "expired": t("This setup link has expired. Ask for a new one on our Discord server."),
        }
        return render_template("error.html", code=410, message=messages.get(state, t("This setup link is not valid."))), 410
    if invite["discord_id"] and invite["discord_id"] != user["discord_id"]:
        return render_template("error.html", code=403, message=t("This setup link is for another Discord account.")), 403
    addons = setup_forms.invite_addons(invite)
    questions = setup_forms.questions_for(db, invite["plan"], addons)
    values, errors = {}, {}
    if request.method == "POST":
        answers, errors = setup_forms.validate(questions, request.form)
        values = {k: request.form.get(k, "") for k in (q["config_key"] for q in questions)}
        if not errors:
            if not setup_forms.consume_invite(db, invite, user["discord_id"]):
                return render_template("error.html", code=410, message=t("This setup link was already used. Ask for a new one on our Discord server.")), 410
            cur = db.execute(
                "INSERT INTO setup_answers (token_hash, discord_id, plan, addons, license_key, answers, server_ip,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (invite["token_hash"], user["discord_id"], invite["plan"], json.dumps(addons), invite["license_key"],
                 json.dumps(answers), answers.get("info.server_ip"), now_iso()),
            )
            db.commit()
            applied, ip_set = False, None
            if invite["license_key"]:
                actor = f"website: {user['username']} (setup)"
                applied = apply_setup(db, cur.lastrowid, invite["license_key"], actor) is None
                ip_set = setup_lock_ip(invite, answers.get("info.server_ip"), actor)
            log.info("Setup answers %s from %s (%s)", cur.lastrowid, user["discord_id"], invite["plan"])
            return render_template("setup_done.html", applied=applied, plan=invite["plan"], ip_set=ip_set,
                                   key=invite["license_key"])
    return render_template("setup.html", invite=invite, addons=addons, questions=questions, values=values, errors=errors)


SELF_SETUP = "Customer (shop)"  # created_by of the setup links customers open themselves


def setup_lock_ip(invite, ip, actor):
    """Since license server 1.13 a key without IP works nowhere: the IP answered in the setup becomes the key's IP.
    Links the customer opened from the shop always set it; links made by an admin only when the key has none yet
    (an IP set by hand is never overwritten). Returns the IP set, or None."""
    if not ip:
        return None
    try:
        current = mythras_api.get_key(invite["license_key"]).get("ips") or []
        if current and invite["created_by"] != SELF_SETUP:
            return None
        if current == [ip]:
            return ip
        mythras_api.key_action(invite["license_key"], "ips", actor, ips=[ip])
        log.info("Setup: key %s now works from %s", invite["license_key"], ip)
        return ip
    except MythrasError as err:
        log.warning("Setup: could not set the IP of %s: %s", invite["license_key"], err.message)
        return None


@app.post("/setup/key/<key>")
@login_required
def self_setup(key):
    """Customer: open the setup questions for one of their own keys (after a purchase, or later from the dashboard)."""
    user = current_user()
    key = normalize_key(key)
    try:
        mine = next((k for k in mythras_api.user_keys(user["discord_id"]).get("keys", [])
                     if k.get("key") == key and k.get("status") != "revoked"), None)
    except MythrasError as err:
        flash(err.message, "error")
        return redirect(url_for("dashboard"))
    if not mine:
        abort(404)
    plan = mine.get("plan") if mine.get("plan") in setup_forms.PLANS else "free"
    addons = [a for a in mine.get("addons") or [] if a in ADDONS]
    db = get_db()
    # one open self-service link per key: older ones stop working
    db.execute("UPDATE setup_invites SET revoked_at = ? WHERE license_key = ? AND discord_id = ? AND created_by = ?"
               " AND used_at IS NULL AND revoked_at IS NULL", (now_iso(), key, user["discord_id"], SELF_SETUP))
    try:
        token = setup_forms.create_invite(db, plan, SELF_SETUP, license_key=key, discord_id=user["discord_id"],
                                          note="Opened by the customer", hours=24, addons=addons)
    except ValueError as err:
        flash(t(str(err)), "error")
        return redirect(url_for("dashboard"))
    return redirect(url_for("setup_page", token=token))


@app.get("/admin/invites")
@admin_required
def admin_invites():
    db = get_db()
    invites = db.execute("SELECT * FROM setup_invites ORDER BY created_at DESC LIMIT 100").fetchall()
    answers = db.execute("SELECT * FROM setup_answers ORDER BY created_at DESC LIMIT 100").fetchall()
    try:
        keys = mythras_api.list_keys()
    except MythrasError:
        keys = []
    return render_template("admin/invites.html", invites=invites, answers=answers, keys=keys,
                           states={i["token_hash"]: setup_forms.invite_state(i) for i in invites},
                           new_link=session.pop("new_invite_link", None), names=known_names(),
                           customers=db.execute("SELECT discord_id, username FROM users ORDER BY COALESCE(last_login, created_at) DESC LIMIT 500").fetchall())


@app.post("/admin/invites")
@admin_required
def admin_create_invite():
    form = request.form
    owner = form.get("discord_id", "").strip()
    key = normalize_key(form.get("key")) or None
    try:
        if owner and not DISCORD_ID_RE.match(owner):
            raise ValueError("Owner must be a Discord user ID (17-20 digits), or empty to remove.")
        hours = parse_int(form.get("hours"), 1, "Enter a number of hours.", maximum=24 * 60, default=168)
        token = setup_forms.create_invite(get_db(), form.get("plan", ""), actor_name(), license_key=key,
                                          discord_id=owner or None, note=form.get("note", ""), hours=hours,
                                          addons=form.getlist("addons"))
    except ValueError as err:
        flash(t(str(err)), "error")
        return redirect(url_for("admin_invites"))
    session["new_invite_link"] = setup_link(token)
    log.info("Setup invite (%s) created by %s", form.get("plan"), actor_name())
    return redirect(url_for("admin_invites"))


@app.post("/admin/invites/<token_hash>/revoke")
@admin_required
def admin_revoke_invite(token_hash):
    if not INVITE_HASH_RE.match(token_hash):
        abort(404)
    db = get_db()
    db.execute("UPDATE setup_invites SET revoked_at = ? WHERE token_hash = ? AND used_at IS NULL", (now_iso(), token_hash))
    db.commit()
    flash(t("Link revoked."), "success")
    return redirect(url_for("admin_invites"))


@app.get("/admin/setups/<int:answer_id>")
@admin_required
def admin_setup(answer_id):
    db = get_db()
    row = db.execute("SELECT * FROM setup_answers WHERE id = ?", (answer_id,)).fetchone()
    if not row:
        abort(404)
    answers = json.loads(row["answers"])
    labels = {q["config_key"]: q for q in setup_forms.all_questions(db)}
    key_info, api_error = None, None
    try:
        keys = mythras_api.list_keys()
        if row["license_key"]:
            key_info = mythras_api.get_key(row["license_key"])
    except MythrasError as err:
        keys, api_error = [], err.message
    return render_template("admin/setup.html", row=row, answers=answers, labels=labels, keys=keys, key_info=key_info,
                           api_error=api_error, names=known_names())


@app.post("/admin/setups/<int:answer_id>/apply")
@admin_required
def admin_apply_setup(answer_id):
    db = get_db()
    row = db.execute("SELECT * FROM setup_answers WHERE id = ?", (answer_id,)).fetchone()
    if not row:
        abort(404)
    key = normalize_key(request.form.get("key")) or row["license_key"]
    if not key:
        flash(t("Choose the key to configure."), "error")
        return redirect(url_for("admin_setup", answer_id=answer_id))
    error = apply_setup(db, answer_id, key, actor_name())
    flash(error if error else t("Configuration sent to the license server for %(key)s.", key=key), "error" if error else "success")
    return redirect(url_for("admin_setup", answer_id=answer_id))


@app.post("/admin/setups/<int:answer_id>/lock-ip")
@admin_required
def admin_setup_lock_ip(answer_id):
    db = get_db()
    row = db.execute("SELECT * FROM setup_answers WHERE id = ?", (answer_id,)).fetchone()
    if not row or not row["license_key"] or not row["server_ip"]:
        abort(404)
    stale = stale_for_sensitive_action()
    if stale:
        return stale
    try:
        mythras_api.key_action(row["license_key"], "ips", actor_name(), ips=[row["server_ip"]])
        flash(t("Key %(key)s now works only from %(ip)s.", key=row["license_key"], ip=row["server_ip"]), "success")
    except MythrasError as err:
        flash(err.message, "error")
    return redirect(url_for("admin_setup", answer_id=answer_id))


# ---------- admin: questions of the setup page ----------

def question_from_form(form):
    """Admin form -> validated column values. Raises ValueError with an English message."""
    kind = form.get("kind", "")
    plan = form.get("plan", "")
    key = form.get("config_key", "").strip().lower()
    label = form.get("label", "").strip()[:200]
    if kind not in setup_forms.KINDS:
        raise ValueError("Choose a valid answer type.")
    if plan not in setup_forms.REQUIREMENTS:
        raise ValueError("Choose a valid plan.")
    if not setup_forms.CONFIG_KEY_RE.match(key):
        raise ValueError("The config key can use lowercase letters, numbers, dots, - and _ only.")
    if not label:
        raise ValueError("Write the question.")
    options = []
    for line in form.get("options", "").splitlines():
        if line.strip():
            value, _, text = line.partition("=")
            options.append([value.strip()[:64], (text.strip() or value.strip())[:120]])
    if kind == "choice" and len(options) < 2:
        raise ValueError("A choice needs at least 2 options, one per line: value = label.")

    def number(name):
        raw = form.get(name, "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            raise ValueError("Minimum and maximum must be numbers.") from None

    show_if = form.get("show_if", "").strip()[:120]
    if show_if and not re.fullmatch(r"[a-z0-9_.\-]{2,80}=[A-Za-z0-9_.\-]{1,40}", show_if):
        raise ValueError("Show only if: write config_key=value, e.g. bedrock.enabled=yes.")
    return {
        "plan": plan, "kind": kind, "config_key": key, "label": label,
        "help": form.get("help", "").strip()[:300], "warning": form.get("warning", "").strip()[:200],
        "options": json.dumps(options), "default_value": form.get("default_value", "").strip()[:200],
        "min_value": number("min_value"), "max_value": number("max_value"), "show_if": show_if,
        "position": parse_int(form.get("position"), 0, "Position must be 0 or more.", default=999),
        "required": 1 if form.get("required") == "1" else 0, "enabled": 1 if form.get("enabled") == "1" else 0,
    }


@app.get("/admin/questions")
@admin_required
def admin_questions():
    return render_template("admin/questions.html", questions=setup_forms.all_questions(get_db()))


@app.route("/admin/questions/<qid>", methods=["GET", "POST"])
@admin_required
def admin_question(qid):
    db = get_db()
    q = None
    if qid != "new":
        if not qid.isdigit():
            abort(404)
        row = db.execute("SELECT * FROM setup_questions WHERE id = ?", (int(qid),)).fetchone()
        if not row:
            abort(404)
        q = setup_forms._row(row)
    if request.method == "POST":
        try:
            data = question_from_form(request.form)
            cols = ", ".join(f"{k} = ?" for k in data)
            if q:
                db.execute(f"UPDATE setup_questions SET {cols} WHERE id = ?", (*data.values(), q["id"]))
            else:
                db.execute(f"INSERT INTO setup_questions ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                           tuple(data.values()))
            db.commit()
        except ValueError as err:
            flash(t(str(err)), "error")
            return render_template("admin/question.html", q=q, form=request.form)
        except sqlite3.IntegrityError:
            flash(t("Another question already uses this config key."), "error")
            return render_template("admin/question.html", q=q, form=request.form)
        flash(t("Question saved."), "success")
        return redirect(url_for("admin_questions"))
    return render_template("admin/question.html", q=q, form=None)


@app.post("/admin/questions/<int:qid>/delete")
@admin_required
def admin_delete_question(qid):
    db = get_db()
    db.execute("DELETE FROM setup_questions WHERE id = ?", (qid,))
    db.commit()
    flash(t("Question deleted."), "success")
    return redirect(url_for("admin_questions"))


@app.post("/admin/questions/reset")
@admin_required
def admin_reset_questions():
    setup_forms.reset_defaults(get_db())
    flash(t("Default questions restored."), "success")
    return redirect(url_for("admin_questions"))


# ---------- shop: the license server runs PayPal (API-SITO.md "Payments"); the site starts and confirms ----------
# The site never decides that someone paid: it only tells the license server which PayPal id came back.

PAYPAL_HOSTS = ("https://www.paypal.com", "https://www.sandbox.paypal.com")
PURCHASE_ID_RE = re.compile(r"^[A-Za-z0-9-]{5,64}$")
SHOP_ITEMS = ("pro",) + ADDON_IDS
SHOP_TTL = 60
_shop_cache = {"at": 0.0, "data": None}
PURCHASE_STATES = {
    "ACTIVE": "Active", "COMPLETED": "Paid", "APPROVAL_PENDING": "Waiting for payment", "PENDING": "Waiting for payment",
    "CREATED": "Waiting for payment", "APPROVED": "Waiting for payment", "CANCELLED": "Cancelled",
    "SUSPENDED": "Payment failed", "EXPIRED": "Ended", "REFUNDED": "Refunded",
}
PAID_STATES = ("ACTIVE", "COMPLETED")
WAITING_STATES = ("APPROVAL_PENDING", "PENDING", "CREATED", "APPROVED")


def shop_info():
    """GET /api/shop, cached for a minute: {enabled, mode, currency, items: {item: {...}}}. Disabled if unreachable."""
    if _shop_cache["data"] is None or time.time() - _shop_cache["at"] > SHOP_TTL:
        try:
            data = mythras_api.shop()
            data["items"] = {i["item"]: i for i in data.get("items") or [] if i.get("item") in SHOP_ITEMS}
        except MythrasError:
            data = {"enabled": False, "mode": "", "currency": "EUR", "items": {}}
        _shop_cache.update(at=time.time(), data=data)
    return _shop_cache["data"]


def money(value, currency="EUR"):
    return ("€" if currency == "EUR" else currency + " ") + str(value)


def price_of(item, fallback=""):
    """Price shown on the site: from the license server's shop when it answers, else the fallback text."""
    info = shop_info()
    it = info["items"].get(item)
    return money(it["price"], info.get("currency", "EUR")) if it and it.get("price") else fallback


def can_buy(item):
    info = shop_info()
    it = info["items"].get(item)
    return bool(info.get("enabled") and it and it.get("available", True))


@app.template_filter("purchase_state")
def purchase_state(status):
    return t(PURCHASE_STATES.get(status or "", "Unknown"))


@app.get("/shop")
def shop():
    user = current_user()
    keys, owned = [], {}
    if user:
        try:
            keys = [k for k in mythras_api.user_keys(user["discord_id"]).get("keys", []) if k.get("status") != "revoked"]
        except MythrasError:
            keys = []
    return render_template("shop.html", keys=keys, free_keys=[k for k in keys if k.get("plan") == "free"],
                           addon_slots={a: [k for k in keys if a not in (k.get("addons") or [])] for a in ADDON_IDS},
                           pick_key=normalize_key(request.args.get("key")) or "", pick_item=request.args.get("item", ""),
                           DOCS=docs, news=releases.latest(g.get("lang", "en")))


@app.post("/shop/buy")
@login_required
def shop_buy():
    user = current_user()
    item = request.form.get("item", "")
    key = normalize_key(request.form.get("key")) if request.form.get("key") else ""
    back = redirect(url_for("shop", _anchor=item if item in SHOP_ITEMS else None))
    if item not in SHOP_ITEMS or not can_buy(item):
        flash(t("This item is not for sale right now."), "error")
        return back
    if request.form.get("key") and not key:
        flash(t("Choose one of your keys, or a new key."), "error")
        return back
    if request.form.get("consent") != "1":
        flash(t("Tick the box to accept the Terms of Sale and the immediate delivery."), "error")
        return back
    try:
        started = mythras_api.user_subscribe(user["discord_id"], item, key=key, name=user["username"])
    except MythrasError as err:
        flash(err.message, "error")
        return back
    url = str(started.get("approveUrl") or "")
    if not url.startswith(tuple(h + "/" for h in PAYPAL_HOSTS)):  # only ever send the browser to PayPal
        log.warning("Shop: unexpected approveUrl for %s", item)
        flash(t("PayPal is not available right now. Try again in a few minutes or buy on our Discord server."), "error")
        return back
    log.info("Shop: %s started by %s (%s)", item, user["discord_id"], started.get("id"))
    return redirect(url, 303)


def confirm_purchase(purchase_id):
    """Ask the license server about this purchase (it reads PayPal). Returns (purchase, key, error)."""
    user = current_user()
    try:
        data = mythras_api.user_confirm(user["discord_id"], purchase_id, name=user["username"])
    except MythrasError as err:
        return None, None, err.message
    return data.get("subscription") or {}, data.get("key"), None


@app.get("/shop/done")
def shop_done():
    if current_user() is None:
        return redirect(url_for("login", next=request.full_path.rstrip("?")))
    purchase_id = request.args.get("subscription_id") or request.args.get("token") or ""
    if not PURCHASE_ID_RE.match(purchase_id):
        return redirect(url_for("shop"))
    purchase, key, error = confirm_purchase(purchase_id)
    state = "error" if error else ("paid" if purchase.get("status") in PAID_STATES
                                   else "waiting" if purchase.get("status") in WAITING_STATES else "other")
    if state == "paid":
        log.info("Shop: %s %s paid by %s", purchase.get("item"), purchase_id, current_user()["discord_id"])
    latest_jar = downloads.latest()
    return render_template("shop_done.html", purchase=purchase, key=key, error=error, state=state,
                           purchase_id=purchase_id, download_exists=latest_jar is not None)


@app.post("/shop/done/check")
@login_required
def shop_done_check():
    """Polled by the "Waiting for PayPal" page (same site only): asks the license server again."""
    purchase_id = request.form.get("id", "")
    if not PURCHASE_ID_RE.match(purchase_id):
        abort(404)
    purchase, _key, error = confirm_purchase(purchase_id)
    status = (purchase or {}).get("status", "")
    return jsonify(ok=error is None, paid=status in PAID_STATES, waiting=status in WAITING_STATES)


@app.post("/shop/purchases/<purchase_id>/cancel")
@login_required
def cancel_my_purchase(purchase_id):
    if not PURCHASE_ID_RE.match(purchase_id):
        abort(404)
    user = current_user()
    try:
        mythras_api.user_cancel(user["discord_id"], purchase_id, name=user["username"])
        flash(t("Subscription cancelled: you will not be charged again. Pro stays active until the end of the month you paid."), "success")
        log.info("Shop: subscription %s cancelled by its owner", purchase_id)
    except MythrasError as err:
        flash(err.message, "error")
    return redirect(url_for("dashboard", _anchor="purchases"))


@app.get("/admin/purchases")
@admin_required
def admin_purchases():
    owner = request.args.get("owner", "").strip()
    rows, api_error = [], None
    try:
        rows = mythras_api.purchases(owner=owner if DISCORD_ID_RE.match(owner) else None)
    except MythrasError as err:
        api_error = err.message
    return render_template("admin/purchases.html", rows=rows, owner=owner, names=known_names(), api_error=api_error)


@app.post("/admin/purchases/<purchase_id>/<action>")
@admin_required
def admin_purchase_action(purchase_id, action):
    if not PURCHASE_ID_RE.match(purchase_id) or action not in ("sync", "cancel"):
        abort(404)
    try:
        if action == "sync":
            row = mythras_api.purchase_sync(purchase_id, actor=actor_name())
            flash(t("Synced from PayPal: %(status)s.", status=purchase_state(row.get("status"))), "success")
        else:
            mythras_api.purchase_cancel(purchase_id, actor=actor_name())
            flash(t("Subscription cancelled at PayPal. The key keeps working until its expiry."), "success")
    except MythrasError as err:
        flash(err.message, "error")
    return redirect(url_for("admin_purchases", owner=request.form.get("owner") or None))


# ---------- Discord bot API (token, no cookies): create a setup link ----------

@app.post("/api/bot/invites")
def bot_create_invite():
    token = request.headers.get("Authorization", "")
    if not BOT_API_TOKEN or not secrets.compare_digest(token, f"Bearer {BOT_API_TOKEN}"):
        return jsonify(ok=False, error="unauthorized"), 401
    body = request.get_json(silent=True) or {}
    owner = str(body.get("discord_id") or "").strip()
    key = normalize_key(body.get("key")) if body.get("key") else None
    if owner and not DISCORD_ID_RE.match(owner):
        return jsonify(ok=False, error="discord_id must be a Discord user ID"), 400
    if body.get("key") and not key:
        return jsonify(ok=False, error="invalid key"), 400
    addons = body.get("addons") or []
    try:
        if not isinstance(addons, list):
            raise ValueError
        hours = int(body.get("hours") or 168)
        token_value = setup_forms.create_invite(get_db(), str(body.get("plan") or ""), "Discord bot",
                                                license_key=key, discord_id=owner or None,
                                                note=str(body.get("note") or ""), hours=hours,
                                                addons=[str(a) for a in addons])
    except (ValueError, TypeError):
        return jsonify(ok=False, error="plan must be free or pro, addons a list of antixray/antiesp, hours a number"), 400
    log.info("Setup invite (%s) created by the Discord bot", body.get("plan"))
    return jsonify(ok=True, url=setup_link(token_value))


# ---------- panel: servers, alerts, punishments and replays reported by Mythras ----------
# Customer views call /api/users/<session Discord ID>/panel/...; admin views call /api/panel/... over all keys.
# The browser only talks to these routes (live feed polling included), never to the license server.

PANEL_DAYS = (1, 7, 30)
EVENT_TYPES = ("alert", "punish", "replay")
PLAYER_RE = re.compile(r"^[A-Za-z0-9_.*\-]{1,32}$")      # Java names, plus Geyser prefixes
SERVER_ID_RE = re.compile(r"^[A-Za-z0-9\-]{1,64}$")
CHECK_RE = re.compile(r"^[A-Za-z0-9_ ,]{1,48}$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")
FEED_PAGE = 50


def _arg(name, pattern):
    value = request.args.get(name, "").strip()
    return value if value and pattern.match(value) else ""


def panel_filters():
    """Filters from the query string, validated (unknown values are dropped)."""
    raw_days = request.args.get("days", "")
    days = int(raw_days) if raw_days.isdigit() and len(raw_days) < 4 else 7
    return {
        "key": normalize_key(request.args.get("key")) or "",
        "days": days if days in PANEL_DAYS else 7,
        "type": request.args.get("type") if request.args.get("type") in EVENT_TYPES else "",
        "server": _arg("server", SERVER_ID_RE),
        "check": _arg("check", CHECK_RE),
        "player": _arg("player", PLAYER_RE),
    }


def panel_chart(stats, days):
    """Stacked bars per UTC day (alerts below, punishments on top) for the last `days` days."""
    today = now_ms() - now_ms() % 86_400_000
    starts = [today - i * 86_400_000 for i in range(days - 1, -1, -1)]
    counts = {d: {"alert": 0, "punish": 0} for d in starts}
    for row in stats.get("daily") or []:
        day, kind = row.get("day"), row.get("type")
        if day in counts and kind in counts[day]:
            counts[day][kind] += int(row.get("count") or 0)
    top = max([c["alert"] + c["punish"] for c in counts.values()] + [0])
    scale = top if top else 1
    # a round axis maximum, so the gridlines get readable labels
    step = 1
    for candidate in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000):
        if candidate * 4 >= scale:
            step = candidate
            break
    else:
        step = math.ceil(scale / 4)
    axis_max = step * 4
    width, height, left, bottom, top_pad = 720, 220, 40, 26, 8
    plot_w, plot_h = width - left - 8, height - bottom - top_pad
    slot = plot_w / len(starts)
    bar_w = max(4, min(36, slot * 0.6))
    bars = []
    for i, day in enumerate(starts):
        a, p = counts[day]["alert"], counts[day]["punish"]
        x = left + i * slot + (slot - bar_w) / 2
        h_a = plot_h * a / axis_max
        h_p = plot_h * p / axis_max
        base = top_pad + plot_h
        bars.append({
            "day": day, "alerts": a, "punish": p, "x": round(x, 1), "w": round(bar_w, 1),
            "ya": round(base - h_a, 1), "ha": round(h_a, 1),
            # 2px surface gap between the two stacked segments
            "yp": round(base - h_a - h_p - (2 if a and p else 0), 1), "hp": round(h_p, 1),
            "label": days <= 7 or i % 5 == 0 or i == len(starts) - 1,
            "lx": round(x + bar_w / 2, 1),
        })
    grid = [{"y": round(top_pad + plot_h - plot_h * k / 4, 1), "value": step * k} for k in range(5)]
    return {"bars": bars, "grid": grid, "width": width, "height": height, "left": left,
            "base": top_pad + plot_h, "empty": top == 0}


def panel_page(discord_id, admin):
    f = panel_filters()
    data = {"filters": f, "admin": admin, "servers": [], "stats": None, "chart": None, "events": [], "next": 0,
            "keys": [], "owners": {}, "api_error": None}
    try:
        if admin:
            all_keys = mythras_api.list_keys()
            data["owners"] = {k["key"]: k.get("owner") for k in all_keys}
            data["keys"] = all_keys
        else:
            data["keys"] = mythras_api.user_keys(discord_id).get("keys", [])
        key = f["key"] or None
        data["servers"] = mythras_api.panel_servers(discord_id, key=key)
        data["stats"] = mythras_api.panel_stats(discord_id, key=key, days=f["days"])
        data["chart"] = panel_chart(data["stats"], f["days"])
        page = mythras_api.panel_events(discord_id, key=key, server=f["server"] or None, type=f["type"] or None,
                                        player=f["player"] or None, check=f["check"] or None, limit=FEED_PAGE)
        data["events"], data["next"] = page.get("events", []), page.get("next") or 0
    except MythrasError as err:
        data["api_error"] = err.message
    data["server_names"] = {s["server"]: s.get("name") or s["server"][:8] for s in data["servers"]}
    data["names"] = known_names() if admin else {}
    return render_template("panel/panel.html", **data)


def panel_feed(discord_id, admin):
    """JSON for the live feed: the newest page, or an older page with ?before=."""
    f = panel_filters()
    before = request.args.get("before", "")
    try:
        page = mythras_api.panel_events(discord_id, key=f["key"] or None, server=f["server"] or None,
                                        type=f["type"] or None, player=f["player"] or None,
                                        check=f["check"] or None, before=int(before) if before.isdigit() else None,
                                        limit=FEED_PAGE)
        servers = mythras_api.panel_servers(discord_id, key=f["key"] or None)
        owners = {k["key"]: k.get("owner") for k in mythras_api.list_keys()} if admin else {}
    except MythrasError as err:
        return jsonify(ok=False, error=err.message), 502
    html_rows = render_template("panel/_rows.html", events=page.get("events", []), admin=admin,
                                server_names={s["server"]: s.get("name") or s["server"][:8] for s in servers},
                                owners=owners, names=known_names() if admin else {})
    html_servers = render_template("panel/_servers.html", servers=servers, admin=admin, api_error=None,
                                   owners=owners, names=known_names() if admin else {})
    resp = jsonify(ok=True, html=html_rows, servers=html_servers, next=page.get("next") or 0)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def panel_player_page(discord_id, admin, name):
    if not PLAYER_RE.match(name or ""):
        abort(404)
    f = panel_filters()
    data, api_error = None, None
    try:
        data = mythras_api.panel_player(name, discord_id, key=f["key"] or None, days=f["days"])
        servers = mythras_api.panel_servers(discord_id, key=f["key"] or None)
        owners = {k["key"]: k.get("owner") for k in mythras_api.list_keys()} if admin else {}
    except MythrasError as err:
        api_error, servers, owners = err.message, [], {}
    top = max([c.get("count") or 0 for c in (data or {}).get("checks", [])] + [1])
    return render_template("panel/player.html", p=data, name=name, filters=f, admin=admin, top=top,
                           api_error=api_error, owners=owners, names=known_names() if admin else {},
                           server_names={s["server"]: s.get("name") or s["server"][:8] for s in servers})


@app.get("/panel")
@login_required
def panel():
    return panel_page(current_user()["discord_id"], admin=False)


@app.get("/panel/feed")
@login_required
def panel_feed_view():
    return panel_feed(current_user()["discord_id"], admin=False)


@app.get("/panel/players/<name>")
@login_required
def panel_player(name):
    return panel_player_page(current_user()["discord_id"], admin=False, name=name)


@app.get("/admin/panel")
@admin_required
def admin_panel():
    return panel_page(None, admin=True)


@app.get("/admin/panel/feed")
@admin_required
def admin_panel_feed():
    return panel_feed(None, admin=True)


@app.get("/admin/panel/players/<name>")
@admin_required
def admin_panel_player(name):
    return panel_player_page(None, admin=True, name=name)


@app.get("/admin/ai")
@admin_required
def admin_ai():
    data, api_error = {}, None
    try:
        data = mythras_api.ai_stats(runs=50)
    except MythrasError as err:
        api_error = err.message
    history = list(reversed(data.get("history") or []))  # oldest first for the chart
    chart = None
    if history:
        width, height, left, bottom, top_pad = 720, 220, 44, 26, 10
        plot_w, plot_h = width - left - 12, height - bottom - top_pad
        lo = 0.5  # coin flip
        pts = []
        for i, run in enumerate(history):
            acc = max(lo, min(1.0, float(run.get("accuracy") or 0)))
            x = left + (plot_w * i / max(1, len(history) - 1))
            y = top_pad + plot_h * (1 - (acc - lo) / (1 - lo))
            pts.append({"x": round(x, 1), "y": round(y, 1), "run": run})
        chart = {"points": pts, "path": " ".join(f"{p['x']},{p['y']}" for p in pts), "width": width,
                 "height": height, "left": left, "base": top_pad + plot_h,
                 "grid": [{"y": round(top_pad + plot_h * (1 - (v - lo) / (1 - lo)), 1), "value": v}
                          for v in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)]}
    owners = {}
    try:
        owners = {k["key"]: k.get("owner") for k in mythras_api.list_keys()}
    except MythrasError:
        pass
    return render_template("admin/ai.html", ai=data, chart=chart, api_error=api_error, owners=owners,
                           names=known_names())


@app.get("/heads/<uuid>.png")
@login_required
def player_head(uuid):
    """Player head served from this website (cached), so visitors' browsers never contact a third party."""
    if not UUID_RE.match(uuid):
        abort(404)
    uuid = uuid.lower().replace("-", "")
    folder = DATABASE.parent / "heads"
    path = folder / f"{uuid}.png"
    fresh = path.exists() and time.time() - path.stat().st_mtime < 86_400
    if not fresh:
        try:
            req = urllib.request.Request(f"https://mc-heads.net/avatar/{uuid}/32",
                                         headers={"User-Agent": "MythrasAntiCheatSite (1.0)"})
            with urllib.request.urlopen(req, timeout=5) as r:
                body = r.read(200_000)
            if body[:8] == b"\x89PNG\r\n\x1a\n":
                folder.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(body)
                os.replace(tmp, path)
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/png", max_age=86_400)


# ---------- admin: two-factor authentication ----------

@app.route("/admin/2fa/setup", methods=["GET", "POST"])
@admin_identity_required
def admin_2fa_setup():
    user = current_user()
    uid = user["discord_id"]
    db = get_db()
    if admin_2fa.is_enrolled(admin_2fa.get_row(db, uid)):
        return redirect(url_for("admin_2fa_verify"))

    # the secret is only saved after the first code is confirmed; until then it
    # lives encrypted in the session
    secret = None
    if session.get("mfa_pending"):
        try:
            secret = admin_2fa.decrypt(session["mfa_pending"])
        except admin_2fa.ConfigError:
            secret = None
    if not secret:
        secret = admin_2fa.new_secret()
        session["mfa_pending"] = admin_2fa.encrypt(secret)

    error = None
    if request.method == "POST":
        code = request.form.get("code", "").strip().replace(" ", "")
        matched = {}

        def check():
            matched["step"] = admin_2fa.matching_step(secret, code)
            return matched["step"] is not None

        ok, error = check_code_with_lockout(uid, check)
        if ok:
            codes = admin_2fa.new_recovery_codes()
            admin_2fa.enroll(db, uid, secret, matched["step"], codes)
            session.pop("mfa_pending", None)
            session["mfa"] = {"uid": uid, "at": int(time.time())}
            log.info("2FA enabled for admin %s", uid)
            # shown exactly once, never stored in plain text
            return render_template("admin/2fa_recovery.html", codes=codes)

    uri = admin_2fa.provisioning_uri(secret, user["username"])
    return render_template(
        "admin/2fa_setup.html",
        qr=Markup(admin_2fa.qr_svg(uri)),
        secret=admin_2fa.group_secret(secret),
        error=error,
    )


@app.route("/admin/2fa", methods=["GET", "POST"])
@admin_identity_required
def admin_2fa_verify():
    uid = current_user()["discord_id"]
    db = get_db()
    if not admin_2fa.is_enrolled(admin_2fa.get_row(db, uid)):
        return redirect(url_for("admin_2fa_setup"))
    next_url = safe_next(request.values.get("next", ""))
    fresh = request.values.get("fresh") == "1"

    error = None
    if request.method == "POST":
        code = request.form.get("code", "")
        result, error = check_code_with_lockout(uid, lambda: admin_2fa.verify(db, uid, code))
        if result:
            session["mfa"] = {"uid": uid, "at": int(time.time())}
            if result == "recovery":
                left = admin_2fa.remaining_codes(db, uid)
                log.warning("Recovery code used by admin %s from %s (%d left)", uid, request.remote_addr, left)
                flash(t("Recovery code used, %(n)s left. Lost your phone? Reset 2FA from the Admins page.", n=left), "success")
            return redirect(next_url)

    wait = admin_2fa.locked_seconds(admin_2fa.get_row(db, uid))
    return render_template(
        "admin/2fa_verify.html",
        next_url=next_url,
        fresh=fresh,
        error=error,
        locked_minutes=math.ceil(wait / 60) if wait else 0,
    )


# ---------- admin dashboard (every route: admin + 2FA, checked on the server) ----------

@app.get("/admin")
@admin_required
def admin():
    stats, events, api_error = None, [], None
    try:
        stats = mythras_api.stats()
        events = mythras_api.log(limit=20)
    except MythrasError as err:
        api_error = err.message
    return render_template("admin/overview.html", stats=stats, events=events, api_error=api_error)


@app.get("/admin/keys")
@admin_required
def admin_keys():
    q = request.args.get("q", "").strip()[:100]
    plan = request.args.get("plan", "")
    status = request.args.get("status", "")
    plans = get_plans()
    if plan not in [p["plan"] for p in plans.get("plans", [])]:
        plan = ""
    if status not in KEY_STATUSES:
        status = ""
    keys, api_error = [], None
    try:
        keys = mythras_api.list_keys(q=q, plan=plan, status=status)
    except MythrasError as err:
        api_error = err.message
    return render_template(
        "admin/keys.html",
        keys=keys,
        plans=plans,
        statuses=KEY_STATUSES,
        filters={"q": q, "plan": plan, "status": status},
        names=known_names(),
        api_error=api_error,
    )


@app.post("/admin/users/remove-keys")
@admin_required
def admin_remove_user_keys():
    """Delete every key of one customer on the license server (optionally also their purchase history here)."""
    stale = stale_for_sensitive_action()
    if stale:
        return stale
    discord_id = request.form.get("discord_id", "").strip()
    back = redirect(url_for("admin_keys", q=discord_id))
    if not DISCORD_ID_RE.match(discord_id):
        flash(t("Enter a valid Discord ID (17-20 digits)."), "error")
        return redirect(url_for("admin_keys"))
    try:
        count = len(mythras_api.user_keys(discord_id).get("keys", []))
        if count:
            answer = mythras_api.delete_owner_keys(discord_id, actor_name())
            if isinstance(answer.get("deleted"), list):  # {deleted: ["MYTH-...", ...]}
                count = len(answer["deleted"])
    except MythrasError as err:
        flash(err.message, "error")
        return back
    # old purchase rows (from when the site took payments) go with the keys
    db = get_db()
    purchases = db.execute("DELETE FROM orders WHERE discord_id = ?", (discord_id,)).rowcount
    db.commit()
    log.info("Admin %s deleted %s keys and %s purchases of %s", session.get("discord_id"), count, purchases, discord_id)
    if not count and not purchases:
        flash(t("This user has no keys."), "error")
    else:
        flash(t("Deleted %(n)s keys of %(id)s. Purchases deleted: %(p)s.", n=count, id=discord_id, p=purchases), "success")
    return back


@app.post("/admin/keys/<key>/delete")
@admin_required
def admin_delete_key(key):
    key = normalize_key(key)
    if not key:
        abort(404)
    stale = stale_for_sensitive_action()
    if stale:
        return stale
    try:
        mythras_api.delete_key(key, actor_name())
    except MythrasError as err:
        flash(err.message, "error")
        return redirect(url_for("admin_key", key=key))
    log.info("Admin %s deleted key %s", session.get("discord_id"), key)
    flash(t("Key %(key)s deleted.", key=key), "success")
    return redirect(url_for("admin_keys"))


@app.get("/admin/keys/new")
@admin_required
def admin_new_key():
    return render_template(
        "admin/create.html",
        customers=get_db().execute("SELECT discord_id, username FROM users ORDER BY COALESCE(last_login, created_at) DESC LIMIT 500").fetchall(),
        plans=get_plans(),
        created=session.pop("created_key", None),
        form=session.pop("create_form", {}),
    )


@app.post("/admin/keys/new")
@admin_required
def admin_create_key():
    stale = stale_for_sensitive_action()
    if stale:
        return stale
    form = request.form
    try:
        plan = form.get("plan", "")
        if plan not in plan_ids():
            raise ValueError("Choose a valid plan.")
        days = parse_int(form.get("days"), 0, "Days must be 0 or more (empty = 1 month, 0 = never expires).")
        if plan == "free":
            days = 0  # Free keys only carry add-ons, and add-ons never expire
        servers = parse_int(form.get("servers"), 0, "Servers must be 0 (unlimited) or more (empty = plan default).")
        owner = form.get("owner", "").strip()
        if owner and not DISCORD_ID_RE.match(owner):
            raise ValueError("Owner must be a Discord user ID (17-20 digits).")
        note = form.get("note", "").strip()[:500]
        addons = addons_from_form(form)
        created = mythras_api.create_key(
            plan, days=days, servers=servers, note=note, owner=owner, actor=actor_name(), addons=addons
        )
    except (ValueError, MythrasError) as err:
        flash(err.message if isinstance(err, MythrasError) else t(str(err)), "error")
        session["create_form"] = {k: form.get(k, "")[:500] for k in ("plan", "days", "servers", "owner", "note")}
        session["create_form"]["addons"] = [a for a in form.getlist("addons") if a in ADDONS]
        return redirect(url_for("admin_new_key"))
    log.info("Key %s created by %s", created["key"], actor_name())
    # PRG: show the new key once on the next GET, so a refresh can't create a second key
    session["created_key"] = {
        k: created.get(k) for k in ("key", "plan", "addons", "expires", "owner", "maxServers", "note")
    }
    return redirect(url_for("admin_new_key"))


@app.get("/admin/keys/<key>")
@admin_required
def admin_key(key):
    key = normalize_key(key)
    if not key:
        abort(404)
    data, api_error = None, None
    try:
        data = mythras_api.get_key(key)
    except MythrasError as err:
        api_error = err.message
    return render_template(
        "admin/key.html",
        k=data,
        key_id=key,
        plans=get_plans(),
        names=known_names(),
        api_error=api_error,
        setups=get_db().execute("SELECT id, created_at, server_ip, applied_at FROM setup_answers WHERE license_key = ?"
                                " ORDER BY created_at DESC LIMIT 5", (key,)).fetchall(),
    )


ACTION_MESSAGES = {
    "revoke": "Key revoked. The server stops within 6 hours.",
    "unrevoke": "Key restored.",
    "reset": "Servers reset.",
    "plan": "Plan changed. It applies at the server's next restart.",
    "renew": "Renewed for 1 month.",
    "extend": "Expiry updated.",
    "ips": "Allowed IP addresses updated.",
    "servers": "Server limit updated.",
    "note": "Note saved.",
    "owner": "Owner updated.",
    "addons": "Add-ons updated. They apply at the server's next restart.",
}
SENSITIVE_ACTIONS = {"revoke", "plan", "ips", "addons"}


@app.post("/admin/keys/<key>/<action>")
@admin_required
def admin_key_action(key, action):
    key = normalize_key(key)
    if not key or action not in ACTION_MESSAGES:
        abort(404)
    if action in SENSITIVE_ACTIONS:
        stale = stale_for_sensitive_action()
        if stale:
            return stale
    form = request.form
    fields = {}
    try:
        if action == "plan":
            plan = form.get("plan", "")
            if plan not in plan_ids():
                raise ValueError("Choose a valid plan.")
            fields["plan"] = plan
        elif action == "addons":
            fields["addons"] = addons_from_form(form)
        elif action == "renew":
            fields["months"] = 1
        elif action == "extend":
            fields["days"] = parse_int(form.get("days"), 0, "Days must be 0 or more (0 = never expires).", default=None)
            if fields["days"] is None:
                raise ValueError("Enter a number of days (0 = never expires).")
        elif action == "ips":
            ips = []
            for item in re.split(r"[\s,]+", form.get("ips", "").strip()):
                if item:
                    try:
                        ips.append(str(ipaddress.ip_address(item)))
                    except ValueError:
                        raise ValueError("Enter numeric IP addresses only, e.g. 123.45.67.89 (one per line).") from None
            if len(ips) > 10:
                raise ValueError("At most 10 IP addresses per key.")
            fields["ips"] = ips
        elif action == "servers":
            fields["servers"] = parse_int(form.get("servers"), -1, "Server limit must be -1, 0 or more.", default=None)
            if fields["servers"] is None:
                raise ValueError("Enter a server limit (-1 = plan default, 0 = unlimited).")
        elif action == "note":
            fields["note"] = form.get("note", "").strip()[:500]
        elif action == "owner":
            owner = form.get("owner", "").strip()
            if owner and not DISCORD_ID_RE.match(owner):
                raise ValueError("Owner must be a Discord user ID (17-20 digits), or empty to remove.")
            fields["owner"] = owner
        mythras_api.key_action(key, action, actor_name(), **fields)
        flash(t(ACTION_MESSAGES[action]), "success")
    except ValueError as err:
        flash(t(str(err)), "error")
    except MythrasError as err:
        flash(err.message, "error")
    return redirect(url_for("admin_key", key=key))


@app.get("/admin/log")
@admin_required
def admin_log():
    try:
        limit = parse_int(request.args.get("limit"), 1, "", maximum=1000, default=200)
    except ValueError:
        limit = 200
    key = normalize_key(request.args.get("key")) or ""
    events, api_error = [], None
    try:
        events = mythras_api.log(limit=limit, key=key or None)
    except MythrasError as err:
        api_error = err.message
    return render_template("admin/log.html", events=events, limit=limit, key_filter=key, api_error=api_error)


@app.get("/admin/downloads")
@admin_required
def admin_downloads():
    return render_template("admin/downloads.html", versions=downloads.versions(), proxy_versions=downloads.versions("proxy"),
                           max_mb=downloads.MAX_UPLOAD // 1048576)


@app.post("/admin/downloads/upload")
@admin_required
def admin_downloads_upload():
    stale = stale_for_sensitive_action()
    if stale:
        return stale
    storage = request.files.get("jar")
    if not storage or not storage.filename:
        flash(t("Choose a .jar file to upload."), "error")
        return redirect(url_for("admin_downloads"))
    try:
        product = request.form.get("product", "plugin")
        if product not in downloads.PRODUCTS:
            raise ValueError("Choose what you are publishing.")
        info = downloads.save_upload(storage, product)
    except ValueError as err:
        flash(t(str(err)), "error")
        return redirect(url_for("admin_downloads"))
    log.warning("New %s build %s (%s) uploaded by %s, sha256 %s", product, info["file"], info["version"], actor_name(), info["sha256"])
    flash(t("Published %(name)s %(version)s. Customers now download this version.", name=info["name"], version=info["version"]), "success")
    return redirect(url_for("admin_downloads"))


@app.errorhandler(413)
def too_large(_e):
    return render_template("error.html", code=413, message=t("The file is too large.")), 413


@app.get("/admin/customers")
@admin_required
def admin_customers():
    """Everyone who signed in with Discord, with their keys: find a Discord ID for a ticket in one search."""
    q = request.args.get("q", "").strip()[:64]
    db = get_db()
    if q:
        like = f"%{q}%"
        rows = db.execute("SELECT * FROM users WHERE username LIKE ? OR discord_id LIKE ? ORDER BY last_login DESC LIMIT 200",
                          (like, like)).fetchall()
    else:
        rows = db.execute("SELECT * FROM users ORDER BY COALESCE(last_login, created_at) DESC LIMIT 200").fetchall()
    total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    keys_by_owner, api_error = {}, None
    try:
        for k in mythras_api.list_keys():
            if k.get("owner"):
                keys_by_owner.setdefault(k["owner"], []).append(k)
    except MythrasError as err:
        api_error = err.message
    return render_template("admin/customers.html", users=rows, q=q, total=total, keys_by_owner=keys_by_owner,
                           api_error=api_error)


@app.get("/admin/admins")
@admin_required
def admin_admins():
    db = get_db()
    names = known_names()
    me = current_user()["discord_id"]
    admins = []
    for uid in sorted(ADMIN_IDS):
        row = admin_2fa.get_row(db, uid)
        enrolled = admin_2fa.is_enrolled(row)
        admins.append({
            "id": uid,
            "name": names.get(uid),
            "enrolled": enrolled,
            "enabled_at": row["enabled_at"] if row else None,
            "codes_left": admin_2fa.remaining_codes(db, uid) if enrolled else 0,
            "locked": admin_2fa.locked_seconds(row) > 0,
            "last_failed": row["last_failed_at"] if row else None,
            "me": uid == me,
        })
    return render_template("admin/admins.html", admins=admins)


@app.post("/admin/admins/reset-2fa")
@admin_required
def admin_reset_2fa():
    uid = current_user()["discord_id"]
    db = get_db()
    code = request.form.get("code", "")
    result, error = check_code_with_lockout(uid, lambda: admin_2fa.verify(db, uid, code))
    if not result:
        flash(error, "error")
        return redirect(url_for("admin_admins"))
    admin_2fa.disable(db, uid)
    session.pop("mfa", None)
    session.pop("mfa_pending", None)
    log.warning("2FA reset by admin %s from %s", uid, request.remote_addr)
    flash(t("Two-factor authentication was reset. Set it up again now."), "success")
    return redirect(url_for("admin_2fa_setup"))


# ---------- errors ----------

@app.errorhandler(400)
def bad_request(_e):
    return render_template("error.html", code=400, message=t("Invalid request.")), 400


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403, message=t("Access denied.")), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, message=t("Page not found.")), 404


@app.errorhandler(500)
def server_error(_e):
    log.exception("Unhandled error on %s %s", request.method, request.path)
    try:
        return render_template("error.html", code=500, message=t("Something went wrong on our side. Please try again in a moment.")), 500
    except Exception:  # the template itself failed: plain text is better than nothing
        return "Internal error", 500


# Create the database at startup (also with gunicorn/waitress)
init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
