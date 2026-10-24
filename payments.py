"""PayPal payments: Pro as a monthly PayPal subscription, add-ons as one-time PayPal orders.

Everything runs on the server: the browser is only redirected to PayPal's approval page and back.
PayPal amounts and states are always read back from the PayPal API, never taken from the browser or
from a webhook body. Fulfilment (creating or changing license keys) is idempotent: a purchase is
claimed with an atomic UPDATE before the license server is called, so the return page and the
webhook can both run without double keys or double months.

Env:
    PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET   REST app credentials (developer.paypal.com)
    PAYPAL_MODE                              sandbox | live (default sandbox)
    PAYPAL_PRO_PLAN_ID                       billing plan of Pro (made by tools/paypal_setup.py)
    PAYPAL_WEBHOOK_ID                        webhook that points to /webhooks/paypal (same tool)
    PAYPAL_API_BASE                          only for tests (mock server)
"""

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import mythras_api
from mythras_api import MythrasError

TIMEOUT = 15
CURRENCY = "EUR"
ADDON_IDS = ("antixray", "antiesp")

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id     TEXT NOT NULL,           -- buyer, from the website session
    kind           TEXT NOT NULL,           -- pro (subscription) | addon (one-time order)
    addon          TEXT,                    -- antixray | antiesp for kind = addon
    target_key     TEXT,                    -- existing key chosen by the buyer (NULL = new key)
    license_key    TEXT,                    -- key that got the purchase
    paypal_id      TEXT UNIQUE,             -- order id or subscription id
    capture_id     TEXT,                    -- one-time orders: the PayPal capture
    amount         TEXT NOT NULL,
    currency       TEXT NOT NULL,
    status         TEXT NOT NULL,           -- created | paying | paid | fulfilled | cancelled | refunded
    sub_status     TEXT,                    -- PayPal subscription status (ACTIVE, CANCELLED, ...)
    months_applied INTEGER NOT NULL DEFAULT 0,
    next_billing   TEXT,
    consent_at     TEXT NOT NULL,           -- immediate delivery + terms accepted
    error          TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS payments_user ON payments (discord_id, created_at);
"""


class PayPalError(Exception):
    """`message` is safe to show (never contains credentials)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.message = message
        self.status = status


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init(db):
    db.executescript(SCHEMA)


def mode():
    return "live" if os.getenv("PAYPAL_MODE", "sandbox").strip().lower() == "live" else "sandbox"


def enabled():
    return bool(os.getenv("PAYPAL_CLIENT_ID") and os.getenv("PAYPAL_CLIENT_SECRET"))


def pro_enabled():
    return enabled() and bool(os.getenv("PAYPAL_PRO_PLAN_ID"))


def api_base():
    override = os.getenv("PAYPAL_API_BASE", "").rstrip("/")
    if override:
        return override
    return "https://api-m.paypal.com" if mode() == "live" else "https://api-m.sandbox.paypal.com"


def checkout_hosts():
    """Hosts the browser is sent to (CSP form-action)."""
    return ["https://www.paypal.com", "https://www.sandbox.paypal.com"]


# ---------- REST client ----------

_token = {"value": None, "until": 0.0}
_token_lock = threading.Lock()


def _request(path, method="GET", body=None, token=None, headers=None, form=None):
    data = None
    hdrs = {"Accept": "application/json", "User-Agent": "MythrasWebsite/1.0"}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    hdrs.update(headers or {})
    req = urllib.request.Request(api_base() + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        try:
            detail = json.load(err)
        except ValueError:
            detail = {}
        name = detail.get("name") or detail.get("error") or f"HTTP {err.code}"
        raise PayPalError(f"PayPal error: {name}.", err.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise PayPalError("PayPal is not reachable right now. Try again in a minute.") from None


def _access_token():
    with _token_lock:
        if _token["value"] and time.time() < _token["until"]:
            return _token["value"]
        basic = base64.b64encode(
            f"{os.getenv('PAYPAL_CLIENT_ID', '')}:{os.getenv('PAYPAL_CLIENT_SECRET', '')}".encode()).decode()
        data = _request("/v1/oauth2/token", method="POST", form={"grant_type": "client_credentials"},
                        headers={"Authorization": f"Basic {basic}"})
        _token["value"] = data["access_token"]
        _token["until"] = time.time() + max(60, int(data.get("expires_in", 300)) - 120)
        return _token["value"]


def call(path, method="GET", body=None, headers=None):
    if not enabled():
        raise PayPalError("PayPal is not configured on this website.")
    return _request(path, method=method, body=body, token=_access_token(), headers=headers)


def _link(resource, rel):
    for link in resource.get("links") or []:
        if link.get("rel") == rel:
            return link.get("href")
    return None


# ---------- starting a purchase ----------

def _insert(db, discord_id, kind, addon, target_key, amount):
    now = now_iso()
    cur = db.execute(
        "INSERT INTO payments (discord_id, kind, addon, target_key, amount, currency, status, consent_at, created_at,"
        " updated_at) VALUES (?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)",
        (discord_id, kind, addon, target_key, amount, CURRENCY, now, now, now))
    db.commit()
    return cur.lastrowid


def start_addon(db, discord_id, addon, target_key, amount, name, return_url, cancel_url):
    """Create a PayPal order for one add-on. Returns the PayPal approval URL."""
    pid = _insert(db, discord_id, "addon", addon, target_key, amount)
    order = call("/v2/checkout/orders", method="POST", headers={"PayPal-Request-Id": f"mythras-order-{pid}"}, body={
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": addon,
            "custom_id": str(pid),
            "description": f"Mythras AntiCheat {name} add-on (one-time, never expires)",
            "amount": {"currency_code": CURRENCY, "value": amount},
        }],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": "Mythras AntiCheat", "shipping_preference": "NO_SHIPPING", "user_action": "PAY_NOW",
            "return_url": return_url, "cancel_url": cancel_url,
        }}},
    })
    db.execute("UPDATE payments SET paypal_id = ?, updated_at = ? WHERE id = ?", (order["id"], now_iso(), pid))
    db.commit()
    url = _link(order, "payer-action") or _link(order, "approve")
    if not url:
        raise PayPalError("PayPal did not return a payment page.")
    return url


def start_pro(db, discord_id, target_key, amount, return_url, cancel_url):
    """Create a PayPal subscription for Pro. Returns the PayPal approval URL."""
    pid = _insert(db, discord_id, "pro", None, target_key, amount)
    sub = call("/v1/billing/subscriptions", method="POST", headers={"PayPal-Request-Id": f"mythras-sub-{pid}"}, body={
        "plan_id": os.getenv("PAYPAL_PRO_PLAN_ID", ""),
        "custom_id": str(pid),
        "application_context": {
            "brand_name": "Mythras AntiCheat", "shipping_preference": "NO_SHIPPING", "user_action": "SUBSCRIBE_NOW",
            "return_url": return_url, "cancel_url": cancel_url,
        },
    })
    db.execute("UPDATE payments SET paypal_id = ?, sub_status = ?, updated_at = ? WHERE id = ?",
               (sub["id"], sub.get("status"), now_iso(), pid))
    db.commit()
    url = _link(sub, "approve")
    if not url:
        raise PayPalError("PayPal did not return a payment page.")
    return url


def active_subscription(db, discord_id):
    return db.execute(
        "SELECT * FROM payments WHERE discord_id = ? AND kind = 'pro' AND sub_status IN ('ACTIVE', 'SUSPENDED')"
        " ORDER BY id DESC LIMIT 1", (discord_id,)).fetchone()


def by_paypal_id(db, paypal_id):
    return db.execute("SELECT * FROM payments WHERE paypal_id = ?", (paypal_id,)).fetchone()


def _set(db, pid, **fields):
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE payments SET {cols} WHERE id = ?", (*fields.values(), pid))
    db.commit()


# ---------- fulfilment (license keys) ----------

ACTOR = "website: PayPal"


def _owned_key(discord_id, key):
    """The buyer's own key (not revoked), or None."""
    if not key:
        return None
    for k in mythras_api.user_keys(discord_id).get("keys", []):
        if k.get("key") == key and k.get("status") != "revoked":
            return k
    return None


def _give_addon(row):
    """Put the add-on on the chosen key, or on a new Free key that never expires. Returns the key."""
    target = _owned_key(row["discord_id"], row["target_key"])
    if target:
        current = mythras_api.get_key(target["key"]).get("addons") or []
        mythras_api.key_action(target["key"], "addons", ACTOR, addons=sorted(set(current) | {row["addon"]}))
        return target["key"]
    created = mythras_api.create_key("free", days=0, owner=row["discord_id"], addons=[row["addon"]], actor=ACTOR,
                                     note=f"PayPal {row['paypal_id']}")
    return created["key"]


def _give_pro_months(row, months):
    """Add `months` of Pro: on the key already used by this subscription, on the chosen key, or on a new key."""
    key = row["license_key"]
    if not key:
        target = _owned_key(row["discord_id"], row["target_key"])
        key = target["key"] if target else None
    if not key:
        created = mythras_api.create_key("pro", days=30 * months, owner=row["discord_id"], actor=ACTOR,
                                         note=f"PayPal subscription {row['paypal_id']}")
        return created["key"]
    if mythras_api.get_key(key).get("plan") != "pro":
        mythras_api.key_action(key, "plan", ACTOR, plan="pro")
    mythras_api.key_action(key, "renew", ACTOR, months=months)
    return key


def sync_order(db, order_id):
    """Capture an approved add-on order and deliver it. Safe to call many times. Returns the payment row."""
    row = by_paypal_id(db, order_id)
    if row is None or row["kind"] != "addon":
        return None
    if row["status"] in ("fulfilled", "refunded", "cancelled"):
        return row
    capture = None
    if row["status"] in ("created", "paying"):
        order = call(f"/v2/checkout/orders/{urllib.parse.quote(order_id, safe='')}")
        unit = (order.get("purchase_units") or [{}])[0]
        if unit.get("custom_id") != str(row["id"]):
            _set(db, row["id"], error="PayPal order does not match this purchase.")
            return by_paypal_id(db, order_id)
        if order.get("status") == "APPROVED":
            order = call(f"/v2/checkout/orders/{urllib.parse.quote(order_id, safe='')}/capture", method="POST",
                         body={}, headers={"PayPal-Request-Id": f"mythras-capture-{row['id']}"})
            unit = (order.get("purchase_units") or [{}])[0]
        captures = ((unit.get("payments") or {}).get("captures")) or []
        capture = captures[0] if captures else None
        if order.get("status") != "COMPLETED" or not capture or capture.get("status") != "COMPLETED":
            if order.get("status") in ("VOIDED",):
                _set(db, row["id"], status="cancelled")
            return by_paypal_id(db, order_id)
        amount = capture.get("amount") or {}
        if amount.get("value") != row["amount"] or amount.get("currency_code") != row["currency"]:
            _set(db, row["id"], status="paid", capture_id=capture.get("id"),
                 error=f"Paid {amount.get('value')} {amount.get('currency_code')}, expected {row['amount']} {row['currency']}.")
            return by_paypal_id(db, order_id)
        db.execute("UPDATE payments SET status = 'paid', capture_id = ?, updated_at = ? WHERE id = ? AND status IN"
                   " ('created', 'paying')", (capture.get("id"), now_iso(), row["id"]))
        db.commit()
        row = by_paypal_id(db, order_id)
    if row["status"] != "paid" or (row["error"] or "").startswith("Paid "):
        return row
    # claim the delivery: only one caller gets past this line
    claimed = db.execute("UPDATE payments SET status = 'delivering', updated_at = ? WHERE id = ? AND status = 'paid'",
                         (now_iso(), row["id"])).rowcount
    db.commit()
    if not claimed:
        return by_paypal_id(db, order_id)
    try:
        key = _give_addon(row)
        _set(db, row["id"], status="fulfilled", license_key=key, error=None)
    except MythrasError as err:
        _set(db, row["id"], status="paid", error=f"License server: {err.message}")
    return by_paypal_id(db, order_id)


def _cycles_paid(sub):
    done = 0
    for cycle in (sub.get("billing_info") or {}).get("cycle_executions") or []:
        if cycle.get("tenure_type") == "REGULAR":
            done += int(cycle.get("cycles_completed") or 0)
    return done


def sync_subscription(db, sub_id):
    """Read the subscription from PayPal and add the months paid since the last sync. Returns the payment row."""
    row = by_paypal_id(db, sub_id)
    if row is None or row["kind"] != "pro":
        return None
    sub = call(f"/v1/billing/subscriptions/{urllib.parse.quote(sub_id, safe='')}")
    if sub.get("custom_id") != str(row["id"]) or sub.get("plan_id") != os.getenv("PAYPAL_PRO_PLAN_ID", ""):
        _set(db, row["id"], error="PayPal subscription does not match this purchase.")
        return by_paypal_id(db, sub_id)
    status = sub.get("status")
    next_billing = (sub.get("billing_info") or {}).get("next_billing_time")
    _set(db, row["id"], sub_status=status, next_billing=next_billing)
    if status == "CANCELLED" and row["status"] == "created":
        _set(db, row["id"], status="cancelled")
    paid = _cycles_paid(sub)
    row = by_paypal_id(db, sub_id)
    due = paid - row["months_applied"]
    if due <= 0 or row["status"] in ("refunded",):
        return row
    # claim these months (compare-and-set), then deliver; undo the claim if the license server fails
    claimed = db.execute("UPDATE payments SET months_applied = ?, updated_at = ? WHERE id = ? AND months_applied = ?",
                         (paid, now_iso(), row["id"], row["months_applied"])).rowcount
    db.commit()
    if not claimed:
        return by_paypal_id(db, sub_id)
    try:
        key = _give_pro_months(row, due)
        _set(db, row["id"], status="fulfilled", license_key=key, error=None)
    except MythrasError as err:
        db.execute("UPDATE payments SET months_applied = months_applied - ?, error = ?, updated_at = ? WHERE id = ?",
                   (due, f"License server: {err.message}", now_iso(), row["id"]))
        db.commit()
    return by_paypal_id(db, sub_id)


def cancel_subscription(db, row, reason="Cancelled by the customer on mythras.eu"):
    call(f"/v1/billing/subscriptions/{urllib.parse.quote(row['paypal_id'], safe='')}/cancel", method="POST",
         body={"reason": reason[:127]})
    _set(db, row["id"], sub_status="CANCELLED", next_billing=None)


def sync(db, row):
    """Admin / return pages: bring one payment up to date."""
    if not row["paypal_id"]:
        return row
    return sync_subscription(db, row["paypal_id"]) if row["kind"] == "pro" else sync_order(db, row["paypal_id"])


# ---------- webhooks ----------

WEBHOOK_EVENTS = (
    "CHECKOUT.ORDER.APPROVED", "PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED",
    "BILLING.SUBSCRIPTION.ACTIVATED", "BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.SUSPENDED",
    "BILLING.SUBSCRIPTION.EXPIRED", "BILLING.SUBSCRIPTION.PAYMENT.FAILED", "PAYMENT.SALE.COMPLETED",
    "PAYMENT.SALE.REFUNDED", "PAYMENT.SALE.REVERSED",
)


def verify_webhook(headers, event):
    """Ask PayPal whether this webhook call is genuine."""
    webhook_id = os.getenv("PAYPAL_WEBHOOK_ID", "")
    if not webhook_id:
        return False
    names = {"auth_algo": "PAYPAL-AUTH-ALGO", "cert_url": "PAYPAL-CERT-URL", "transmission_id": "PAYPAL-TRANSMISSION-ID",
             "transmission_sig": "PAYPAL-TRANSMISSION-SIG", "transmission_time": "PAYPAL-TRANSMISSION-TIME"}
    body = {k: headers.get(h, "") for k, h in names.items()}
    if not all(body.values()):
        return False
    body.update(webhook_id=webhook_id, webhook_event=event)
    try:
        return call("/v1/notifications/verify-webhook-signature", method="POST", body=body).get(
            "verification_status") == "SUCCESS"
    except PayPalError:
        return False


def _refund(db, capture_id, log):
    row = db.execute("SELECT * FROM payments WHERE capture_id = ?", (capture_id,)).fetchone()
    if row is None or row["status"] == "refunded":
        return
    _set(db, row["id"], status="refunded")
    if row["license_key"] and row["addon"]:
        try:
            current = mythras_api.get_key(row["license_key"]).get("addons") or []
            mythras_api.key_action(row["license_key"], "addons", ACTOR,
                                   addons=sorted(set(current) - {row["addon"]}))
        except MythrasError as err:
            _set(db, row["id"], error=f"Refunded; remove the add-on by hand: {err.message}")
    log.info("PayPal refund for payment %s: add-on %s removed from %s", row["id"], row["addon"], row["license_key"])


def handle_event(db, event, log):
    """Act on a verified webhook event. The resource is re-read from PayPal where it matters."""
    kind = event.get("event_type", "")
    res = event.get("resource") or {}
    if kind == "CHECKOUT.ORDER.APPROVED":
        sync_order(db, res.get("id", ""))
    elif kind == "PAYMENT.CAPTURE.COMPLETED":
        order_id = ((res.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
        if order_id:
            sync_order(db, order_id)
    elif kind in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
        up = _link(res, "up") or ""
        capture_id = up.rstrip("/").rsplit("/", 1)[-1] if "/captures/" in up else res.get("id")
        _refund(db, capture_id, log)
    elif kind.startswith("BILLING.SUBSCRIPTION."):
        sync_subscription(db, res.get("id", ""))
    elif kind == "PAYMENT.SALE.COMPLETED":
        if res.get("billing_agreement_id"):
            sync_subscription(db, res["billing_agreement_id"])
    elif kind in ("PAYMENT.SALE.REFUNDED", "PAYMENT.SALE.REVERSED"):
        sub_id = res.get("billing_agreement_id")
        row = by_paypal_id(db, sub_id) if sub_id else None
        if row:
            _set(db, row["id"], error=f"A payment of this subscription was refunded ({kind}): check the key by hand.")
            log.warning("PayPal %s on subscription %s (payment %s)", kind, sub_id, row["id"])
