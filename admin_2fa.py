"""Two-factor authentication (TOTP, RFC 6238) for admins.

- Secrets are encrypted at rest with Fernet using TOTP_ENC_KEY (env).
- Recovery codes are stored as bcrypt hashes and are single-use.
- A TOTP code is accepted at most once: the matched time step must be newer
  than the last accepted one (checked atomically in the database).
"""

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
import urllib.parse
from datetime import datetime, timezone

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

STEP = 30                 # seconds per TOTP step
DIGITS = 6
DRIFT = 1                 # accept ±1 step (±30 s)
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60
RECOVERY_COUNT = 10
ISSUER = "Mythras AntiCheat"
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_mfa (
    discord_id     TEXT PRIMARY KEY,
    secret_enc     TEXT,
    enabled_at     TEXT,
    last_step      INTEGER NOT NULL DEFAULT 0,
    failed_count   INTEGER NOT NULL DEFAULT 0,
    locked_until   INTEGER NOT NULL DEFAULT 0,
    last_failed_at TEXT
);
CREATE TABLE IF NOT EXISTS admin_recovery_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id TEXT NOT NULL,
    code_hash  TEXT NOT NULL,
    used_at    TEXT
);
"""


class ConfigError(Exception):
    pass


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- encryption ----------

def _fernet():
    key = os.getenv("TOTP_ENC_KEY", "")
    if not key:
        raise ConfigError("TOTP_ENC_KEY is not set.")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise ConfigError("TOTP_ENC_KEY is not a valid Fernet key.") from None


def configured():
    try:
        _fernet()
        return True
    except ConfigError:
        return False


def encrypt(text):
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise ConfigError("Stored 2FA data cannot be decrypted (TOTP_ENC_KEY changed?).") from None


# ---------- TOTP ----------

def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode()


def hotp(secret, counter):
    key = base64.b32decode(secret)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 10 ** DIGITS:0{DIGITS}d}"


def current_step(now=None):
    return int((time.time() if now is None else now) // STEP)


def matching_step(secret, code, now=None):
    """The time step this code belongs to (within ±DRIFT), or None."""
    if not (len(code) == DIGITS and code.isdigit()):
        return None
    base = current_step(now)
    for step in range(base - DRIFT, base + DRIFT + 1):
        if hmac.compare_digest(hotp(secret, step), code):
            return step
    return None


def provisioning_uri(secret, account):
    label = urllib.parse.quote(f"{ISSUER}:{account}")
    issuer = urllib.parse.quote(ISSUER)
    return f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def qr_svg(uri):
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return img.to_string(encoding="unicode")


def group_secret(secret):
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


# ---------- recovery codes ----------

def new_recovery_codes():
    codes = []
    for _ in range(RECOVERY_COUNT):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def _normalize_recovery(code):
    return code.strip().lower().replace("-", "").replace(" ", "")


def _hash(code):
    return bcrypt.hashpw(_normalize_recovery(code).encode(), bcrypt.gensalt(rounds=11)).decode()


# ---------- database ----------

def get_row(db, uid):
    return db.execute("SELECT * FROM admin_mfa WHERE discord_id = ?", (uid,)).fetchone()


def ensure_row(db, uid):
    db.execute("INSERT OR IGNORE INTO admin_mfa (discord_id) VALUES (?)", (uid,))
    db.commit()
    return get_row(db, uid)


def is_enrolled(row):
    return bool(row and row["secret_enc"])


def locked_seconds(row):
    if not row:
        return 0
    return max(0, int(row["locked_until"] - time.time()))


def register_failure(db, uid):
    """Count a wrong code. Returns (attempts_left, locked_now)."""
    row = ensure_row(db, uid)
    count = row["failed_count"] + 1
    if count >= MAX_FAILS:
        db.execute(
            "UPDATE admin_mfa SET failed_count = 0, locked_until = ?, last_failed_at = ? WHERE discord_id = ?",
            (int(time.time()) + LOCK_SECONDS, _now_iso(), uid),
        )
        db.commit()
        return 0, True
    db.execute(
        "UPDATE admin_mfa SET failed_count = ?, last_failed_at = ? WHERE discord_id = ?",
        (count, _now_iso(), uid),
    )
    db.commit()
    return MAX_FAILS - count, False


def reset_failures(db, uid):
    db.execute("UPDATE admin_mfa SET failed_count = 0, locked_until = 0 WHERE discord_id = ?", (uid,))
    db.commit()


def enroll(db, uid, secret, step, codes):
    ensure_row(db, uid)
    db.execute(
        "UPDATE admin_mfa SET secret_enc = ?, enabled_at = ?, last_step = ?, failed_count = 0, locked_until = 0"
        " WHERE discord_id = ?",
        (encrypt(secret), _now_iso(), step, uid),
    )
    db.execute("DELETE FROM admin_recovery_codes WHERE discord_id = ?", (uid,))
    db.executemany(
        "INSERT INTO admin_recovery_codes (discord_id, code_hash) VALUES (?, ?)",
        [(uid, _hash(c)) for c in codes],
    )
    db.commit()


def disable(db, uid):
    db.execute(
        "UPDATE admin_mfa SET secret_enc = NULL, enabled_at = NULL, last_step = 0 WHERE discord_id = ?", (uid,)
    )
    db.execute("DELETE FROM admin_recovery_codes WHERE discord_id = ?", (uid,))
    db.commit()


def remaining_codes(db, uid):
    return db.execute(
        "SELECT COUNT(*) FROM admin_recovery_codes WHERE discord_id = ? AND used_at IS NULL", (uid,)
    ).fetchone()[0]


def verify(db, uid, code):
    """Check a TOTP or recovery code. Returns "totp", "recovery" or None. Codes are single-use."""
    row = get_row(db, uid)
    if not is_enrolled(row):
        return None
    code = (code or "").strip().replace(" ", "")
    if len(code) == DIGITS and code.isdigit():
        step = matching_step(decrypt(row["secret_enc"]), code)
        if step is None:
            return None
        # accept only a step newer than the last accepted one (blocks replays, also across workers)
        cur = db.execute(
            "UPDATE admin_mfa SET last_step = ? WHERE discord_id = ? AND last_step < ?", (step, uid, step)
        )
        db.commit()
        return "totp" if cur.rowcount == 1 else None

    normalized = _normalize_recovery(code)
    if len(normalized) != 10:
        return None
    rows = db.execute(
        "SELECT id, code_hash FROM admin_recovery_codes WHERE discord_id = ? AND used_at IS NULL", (uid,)
    ).fetchall()
    for r in rows:
        if bcrypt.checkpw(normalized.encode(), r["code_hash"].encode()):
            cur = db.execute(
                "UPDATE admin_recovery_codes SET used_at = ? WHERE id = ? AND used_at IS NULL", (_now_iso(), r["id"])
            )
            db.commit()
            return "recovery" if cur.rowcount == 1 else None
    return None
