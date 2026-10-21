"""Mythras AntiCheat website: Discord login, customer area and admin dashboard.

Licenses live on the Mythras license server; this app talks to it only from the
backend through mythras_api (the API token never reaches the browser).
"""

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
import docs  # noqa: E402
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

# Discord server invite: support, and purchases (customers open a ticket there; no online payments on the site)
SUPPORT_DISCORD = os.getenv("SUPPORT_DISCORD_URL", "")
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
DEFAULT_PLANS = [{"plan": "base"}, {"plan": "pro"}, {"plan": "enterprise"}]
DAY_MS = 86_400_000

# Marketing copy for the pricing page. Prices and features are placeholders: edit them here.
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
            "Community support",
        ],
    },
    "base": {
        "name": "Base",
        "price": "€9.99",
        "period": "per month",
        "description": "Essential protection for growing servers.",
        "features": [
            "Complete movement checks",
            "Reach, hitbox and X-Ray checks",
            "World and protocol checks: FreeCam, Scaffold, FastPlace and more",
            "Setbacks, automatic punishments, staff GUI, Discord webhook",
            "1 server",
            "Email support",
            "Monthly license: buy and renew on Discord",
        ],
    },
    "pro": {
        "name": "Pro",
        "price": "€24.99",
        "period": "per month",
        "description": "Advanced detection for competitive servers.",
        "features": [
            "All Base checks",
            "KillAura, autoclicker and timer checks",
            "AI Learning (adaptive false-positive filtering)",
            "Replays kept for 7 days",
            "Whitelist and exemptions",
            "Anti-ESP, Anti-StorageESP, Anti-XRay and combat mitigations",
            "1 server",
            "Priority support",
            "Monthly license: buy and renew on Discord",
        ],
    },
    "enterprise": {
        "name": "Enterprise",
        "price": "€79.99",
        "period": "per month",
        "lifetime_price": "€150",
        "description": "Maximum protection for networks and large communities.",
        "features": [
            "All Pro checks",
            "Aim, Mace, Backtrack, AimBow and Crystal PvP checks",
            "Advanced AI Learning + Mythras Cloud AI (global model)",
            "Replays kept for 7 days",
            "Unlimited servers (multi-server)",
            "Cross-server alerts, ban wave, developer API, PlaceholderAPI",
            "Folia support",
            "Dedicated support",
            "Monthly license: buy and renew on Discord",
        ],
    },
}

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
    # webhooks come from the payment provider and are authenticated by their signature instead
    if request.method == "POST" and not request.path.startswith("/webhooks/"):
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
        "form-action 'self'",
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
    return TIERS.get(plan, {}).get("name") or str(plan or "").title()


@app.context_processor
def inject_globals():
    return {
        "csrf_token": csrf_token(),
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
        "SUPPORT_DISCORD": SUPPORT_DISCORD,
        "FREE_LINKS": FREE_LINKS,
    }


# ---------- public pages ----------

@app.get("/")
def index():
    return render_template("index.html", PLAN_MATRIX=docs.PLAN_MATRIX)


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
    """Public list of published plugin versions with their release notes (no download links: those need a key)."""
    return render_template("changelog.html", versions=downloads.versions())


@app.get("/robots.txt")
def robots_txt():
    body = "\n".join([
        "User-agent: *",
        "Disallow: /admin",
        "Disallow: /dashboard",
        "Disallow: /panel",
        "Disallow: /download",
        "Disallow: /heads/",
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
    return redirect(url_for("dashboard"))


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
    return render_template(
        "dashboard.html",
        keys=keys,
        download=latest_jar if active else None,
        download_exists=latest_jar is not None,
        active_count=len(active),
        servers_online=sum(k.get("activeServers") or 0 for k in active),
        reset_hours=reset_hours,
        api_error=api_error,
    )


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
    resp = jsonify(ok=True, html=html_rows, next=page.get("next") or 0)
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
        servers = parse_int(form.get("servers"), 0, "Servers must be 0 (unlimited) or more (empty = plan default).")
        owner = form.get("owner", "").strip()
        if owner and not DISCORD_ID_RE.match(owner):
            raise ValueError("Owner must be a Discord user ID (17-20 digits).")
        note = form.get("note", "").strip()[:500]
        created = mythras_api.create_key(
            plan, days=days, servers=servers, note=note, owner=owner, actor=actor_name()
        )
    except (ValueError, MythrasError) as err:
        flash(err.message if isinstance(err, MythrasError) else t(str(err)), "error")
        session["create_form"] = {k: form.get(k, "")[:500] for k in ("plan", "days", "servers", "owner", "note")}
        return redirect(url_for("admin_new_key"))
    log.info("Key %s created by %s", created["key"], actor_name())
    # PRG: show the new key once on the next GET, so a refresh can't create a second key
    session["created_key"] = {
        k: created.get(k) for k in ("key", "plan", "expires", "owner", "maxServers", "note")
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
    )


ACTION_MESSAGES = {
    "revoke": "Key revoked. The server stops within 6 hours.",
    "unrevoke": "Key restored.",
    "reset": "Servers reset.",
    "plan": "Plan changed. It applies at the server's next restart.",
    "renew": "Renewed for 1 month.",
    "extend": "Expiry updated.",
    "servers": "Server limit updated.",
    "note": "Note saved.",
    "owner": "Owner updated.",
}
SENSITIVE_ACTIONS = {"revoke", "plan"}


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
        elif action == "renew":
            fields["months"] = 1
        elif action == "extend":
            fields["days"] = parse_int(form.get("days"), 0, "Days must be 0 or more (0 = never expires).", default=None)
            if fields["days"] is None:
                raise ValueError("Enter a number of days (0 = never expires).")
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
    return render_template("admin/downloads.html", versions=downloads.versions(), max_mb=downloads.MAX_UPLOAD // 1048576)


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
        info = downloads.save_upload(storage, request.form.get("changelog", ""))
    except ValueError as err:
        flash(t(str(err)), "error")
        return redirect(url_for("admin_downloads"))
    log.warning("New plugin build %s (%s) uploaded by %s, sha256 %s", info["file"], info["version"], actor_name(), info["sha256"])
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
