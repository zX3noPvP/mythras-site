"""PayPal: Pro subscription and one-time add-ons, against fake PayPal + license servers."""
import html, os, sqlite3, sys, tempfile, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import mock_license_server as mock  # noqa: E402
import mock_paypal as pp  # noqa: E402
for port, handler in ((5107, mock.Handler), (5108, pp.Handler)):
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update({"DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "p.db"), "DOWNLOADS_DIR": tempfile.mkdtemp(),
                   "MYTHRAS_API_URL": "http://127.0.0.1:5107", "MYTHRAS_API_TOKEN": "test-token",
                   "ADMIN_DISCORD_IDS": "100000000000000001", "TOTP_ENC_KEY": Fernet.generate_key().decode(),
                   "PAYPAL_CLIENT_ID": "cid", "PAYPAL_CLIENT_SECRET": "csecret", "PAYPAL_API_BASE": "http://127.0.0.1:5108",
                   "PAYPAL_PRO_PLAN_ID": pp.PLAN_ID, "PAYPAL_WEBHOOK_ID": "WH-1", "SUPPORT_DISCORD_URL": "https://discord.gg/x"})
import app as m, admin_2fa  # noqa: E402

ADMIN, BUYER, OTHER = "100000000000000001", "100000000000000004", "100000000000000005"
con = sqlite3.connect(os.environ["DATABASE_PATH"])
for uid, n in ((ADMIN, "Admin"), (BUYER, "Buyer"), (OTHER, "Other")):
    con.execute("INSERT INTO users VALUES (?,?,NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)", (uid, n))
con.commit(); admin_2fa.enroll(con, ADMIN, admin_2fa.new_secret(), 1, admin_2fa.new_recovery_codes()); con.close()
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
def client(uid, mfa=False):
    c = m.app.test_client(); c.get("/lang/en")
    with c.session_transaction() as s:
        s["discord_id"] = uid; s["csrf_token"] = "tok"
        if mfa: s["mfa"] = {"uid": uid, "at": int(time.time())}
    return c
def db():
    c = sqlite3.connect(os.environ["DATABASE_PATH"]); c.row_factory = sqlite3.Row; return c
def text(r): return html.unescape(r.data.decode())
def approve(pid):
    import urllib.request
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:5108/_approve/{pid}", data=b"", method="POST"))
def charge(sid):
    import urllib.request
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:5108/_charge/{sid}", data=b"", method="POST"))
def my_keys(uid): return [k for k in mock.keys.values() if k["owner"] == uid]
def hook(event, sig="good-signature"):
    return m.app.test_client().post("/webhooks/paypal", json=event, headers={
        "PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.paypal.com/cert", "PAYPAL-TRANSMISSION-ID": "t1",
        "PAYPAL-TRANSMISSION-SIG": sig, "PAYPAL-TRANSMISSION-TIME": "2026-10-24T10:00:00Z"})

bu = client(BUYER)
home = text(m.app.test_client().get("/"))
check("pricing: PayPal buttons", "Subscribe with PayPal" in home and "Pay with PayPal" in home)
check("CSP allows the PayPal redirect", "https://www.paypal.com" in m.app.test_client().get("/").headers["Content-Security-Policy"])
dash = text(bu.get("/dashboard"))
check("dashboard: PayPal forms with consent", 'action="/billing/pro"' in dash and 'action="/billing/addon/antixray"' in dash and 'name="consent"' in dash)

# ---------- add-on, one-time ----------
r = bu.post("/billing/addon/antixray", data={"csrf_token": "tok"})
check("consent required", r.status_code == 302 and db().execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0)
check("CSRF required", bu.post("/billing/addon/antixray", data={"consent": "1"}).status_code == 400)
check("unknown add-on 404", bu.post("/billing/addon/fly", data={"csrf_token": "tok", "consent": "1"}).status_code == 404)
r = bu.post("/billing/addon/antixray", data={"csrf_token": "tok", "consent": "1"})
check("add-on: redirect to PayPal", r.status_code == 303 and r.headers["Location"].startswith("https://www.sandbox.paypal.com/"))
order_id = r.headers["Location"].split("token=")[1]
row = db().execute("SELECT * FROM payments WHERE paypal_id = ?", (order_id,)).fetchone()
check("order stored for this buyer, 5.99 EUR", row["discord_id"] == BUYER and row["amount"] == "5.99" and row["currency"] == "EUR" and row["consent_at"])
check("PayPal order has the right amount and id", pp.orders[order_id]["purchase_units"][0]["amount"] == {"currency_code": "EUR", "value": "5.99"}
      and pp.orders[order_id]["purchase_units"][0]["custom_id"] == str(row["id"]))
check("someone else cannot use the return URL", client(OTHER).get(f"/billing/return?token={order_id}").status_code == 404)
r = bu.get(f"/billing/return?token={order_id}", follow_redirects=True)
check("not approved yet: nothing delivered", not my_keys(BUYER) and "waiting for PayPal" in text(r))
approve(order_id)
r = bu.get(f"/billing/return?token={order_id}", follow_redirects=True)
keys = my_keys(BUYER)
check("approved: captured, Free key with Anti-XRay that never expires", len(keys) == 1 and keys[0]["plan"] == "free"
      and keys[0]["addons"] == ["antixray"] and keys[0]["expires"] == 0 and "Payment received, thank you" in text(r))
row = db().execute("SELECT * FROM payments WHERE paypal_id = ?", (order_id,)).fetchone()
check("payment fulfilled with capture id", row["status"] == "fulfilled" and row["capture_id"] == "CAP" + order_id and row["license_key"] == keys[0]["key"])
bu.get(f"/billing/return?token={order_id}")
check("return again: no second key", len(my_keys(BUYER)) == 1)
free_key = keys[0]["key"]

# second add-on onto the same key, delivered by the webhook only
r = bu.post("/billing/addon/antiesp", data={"csrf_token": "tok", "consent": "1", "key": free_key})
order2 = r.headers["Location"].split("token=")[1]
approve(order2)
check("unsigned webhook refused", hook({"event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": order2}}, sig="forged").status_code == 400
      and mock.keys[free_key]["addons"] == ["antixray"])
r = hook({"event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": order2}})
check("webhook delivers the add-on to the chosen key", r.status_code == 200 and mock.keys[free_key]["addons"] == ["antiesp", "antixray"] and len(my_keys(BUYER)) == 1)
check("webhook needs no CSRF token but is not open to GET", m.app.test_client().get("/webhooks/paypal").status_code == 405)

# someone else's key as target: gets a new key instead
r = client(OTHER).post("/billing/addon/antixray", data={"csrf_token": "tok", "consent": "1", "key": free_key})
o3 = r.headers["Location"].split("token=")[1]; approve(o3); client(OTHER).get(f"/billing/return?token={o3}")
check("cannot add to a key of another customer", mock.keys[free_key]["addons"] == ["antiesp", "antixray"] and len(my_keys(OTHER)) == 1)

# refund removes the add-on
cap2 = db().execute("SELECT capture_id FROM payments WHERE paypal_id = ?", (order2,)).fetchone()[0]
hook({"event_type": "PAYMENT.CAPTURE.REFUNDED", "resource": {"id": "REF1", "links": [{"rel": "up", "href": f"https://api.paypal.com/v2/payments/captures/{cap2}"}]}})
check("refund removes the add-on", mock.keys[free_key]["addons"] == ["antixray"]
      and db().execute("SELECT status FROM payments WHERE paypal_id = ?", (order2,)).fetchone()[0] == "refunded")

# cancelled on PayPal
r = bu.post("/billing/addon/antiesp", data={"csrf_token": "tok", "consent": "1"})
o4 = r.headers["Location"].split("token=")[1]
r = bu.get(f"/billing/cancelled?token={o4}", follow_redirects=True)
check("cancel on PayPal", "Payment cancelled" in text(r) and db().execute("SELECT status FROM payments WHERE paypal_id = ?", (o4,)).fetchone()[0] == "cancelled")

# ---------- Pro subscription ----------
r = bu.post("/billing/pro", data={"csrf_token": "tok", "consent": "1", "key": free_key})
check("Pro: redirect to PayPal", r.status_code == 303 and "sandbox.paypal.com" in r.headers["Location"])
sub_id = r.headers["Location"].split("ba_token=")[1]
check("subscription uses the Pro plan", pp.subs[sub_id]["plan_id"] == pp.PLAN_ID)
r = bu.get(f"/billing/subscribed?subscription_id={sub_id}", follow_redirects=True)
check("not approved: still Free", mock.keys[free_key]["plan"] == "free")
approve(sub_id)
before = time.time() * 1000
r = bu.get(f"/billing/subscribed?subscription_id={sub_id}", follow_redirects=True)
k = mock.keys[free_key]
check("first month: the Free key becomes Pro for 30 days, add-ons kept", k["plan"] == "pro" and k["addons"] == ["antixray"]
      and 29 * 86400000 < k["expires"] - before < 31 * 86400000 and "Payment received, thank you" in text(r))
exp1 = k["expires"]
bu.get(f"/billing/subscribed?subscription_id={sub_id}")
hook({"event_type": "BILLING.SUBSCRIPTION.ACTIVATED", "resource": {"id": sub_id}})
check("same month seen again: not added twice", mock.keys[free_key]["expires"] == exp1)
dash = text(bu.get("/dashboard"))
check("dashboard shows the subscription", "Pro subscription" in dash and "Cancel subscription" in dash and "You are subscribed" in dash)
r = bu.post("/billing/pro", data={"csrf_token": "tok", "consent": "1"}, follow_redirects=True)
check("one active subscription per customer", "already have an active Pro subscription" in text(r))
charge(sub_id)
hook({"event_type": "PAYMENT.SALE.COMPLETED", "resource": {"id": "SALE2", "billing_agreement_id": sub_id}})
check("monthly renewal from the webhook: +1 month", 29 * 86400000 < mock.keys[free_key]["expires"] - exp1 < 31 * 86400000)
exp2 = mock.keys[free_key]["expires"]
hook({"event_type": "PAYMENT.SALE.COMPLETED", "resource": {"id": "SALE2", "billing_agreement_id": sub_id}})
check("same webhook twice: no extra month", mock.keys[free_key]["expires"] == exp2)
check("months counted", db().execute("SELECT months_applied FROM payments WHERE paypal_id = ?", (sub_id,)).fetchone()[0] == 2)

# license server down during a renewal: retried later, never lost
real = os.environ["MYTHRAS_API_URL"]; os.environ["MYTHRAS_API_URL"] = "http://127.0.0.1:9"
charge(sub_id)
hook({"event_type": "PAYMENT.SALE.COMPLETED", "resource": {"id": "SALE3", "billing_agreement_id": sub_id}})
row = db().execute("SELECT * FROM payments WHERE paypal_id = ?", (sub_id,)).fetchone()
check("license server down: month not marked as given, error saved", row["months_applied"] == 2 and "License server" in (row["error"] or ""))
os.environ["MYTHRAS_API_URL"] = real
ad = client(ADMIN, mfa=True)
t = text(ad.get("/admin/payments"))
check("admin payments page", "Payments" in t and sub_id in t and "License server" in t)
check("admin payments page is admin-only", bu.get("/admin/payments").status_code == 403)
ad.post(f"/admin/payments/{row['id']}/sync", data={"csrf_token": "tok"})
check("admin sync delivers the missing month", mock.keys[free_key]["expires"] > exp2 and
      db().execute("SELECT months_applied FROM payments WHERE paypal_id = ?", (sub_id,)).fetchone()[0] == 3)

r = bu.post("/billing/subscription/cancel", data={"csrf_token": "tok"}, follow_redirects=True)
check("customer cancels: PayPal cancelled, key kept until expiry", pp.subs[sub_id]["status"] == "CANCELLED" and "will not be charged again" in text(r)
      and mock.keys[free_key]["plan"] == "pro")
check("can subscribe again after cancelling", 'action="/billing/pro"' in text(bu.get("/dashboard")))

# new customer, no key yet: Pro creates a new key
nc = client(OTHER)
r = nc.post("/billing/pro", data={"csrf_token": "tok", "consent": "1"})
s2 = r.headers["Location"].split("ba_token=")[1]; approve(s2); nc.get(f"/billing/subscribed?subscription_id={s2}")
pro_keys = [k for k in my_keys(OTHER) if k["plan"] == "pro"]
check("Pro without a key: new Pro key for the buyer", len(pro_keys) == 1 and pro_keys[0]["expires"] > 0)

# a subscription with another plan id is not trusted
r = nc.post("/billing/addon/antiesp", data={"csrf_token": "tok", "consent": "1", "key": pro_keys[0]["key"]})
o5 = r.headers["Location"].split("token=")[1]
pp.orders[o5]["purchase_units"][0]["amount"]["value"] = "0.01"; approve(o5); nc.get(f"/billing/return?token={o5}")
row = db().execute("SELECT * FROM payments WHERE paypal_id = ?", (o5,)).fetchone()
check("wrong amount paid: not delivered, flagged", "antiesp" not in mock.keys[pro_keys[0]["key"]]["addons"] and "expected 5.99" in (row["error"] or ""))

# terms + privacy mention PayPal
terms = text(m.app.test_client().get("/terms")); priv = text(m.app.test_client().get("/privacy"))
check("terms: subscription renews until cancelled", "renews every month" in terms and "cancel at any time" in terms)
check("privacy: PayPal listed", "PayPal (Europe)" in priv)
c = client(BUYER); c.get("/lang/it")
check("Italian dashboard", "Abbonati con PayPal" in text(c.get("/dashboard")) or "Annulla abbonamento" in text(c.get("/dashboard")))

print(f"\n{len(fails)} failed" if fails else "\nALL PASSED")
