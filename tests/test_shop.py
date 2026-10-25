"""Shop: PayPal through the license server (API-SITO.md "Payments"); the site only starts and confirms."""
import html, os, sqlite3, sys, tempfile, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import mock_license_server as mock  # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", 5109), mock.Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update({"DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "s.db"), "DOWNLOADS_DIR": tempfile.mkdtemp(),
                   "MYTHRAS_API_URL": "http://127.0.0.1:5109", "MYTHRAS_API_TOKEN": "test-token",
                   "ADMIN_DISCORD_IDS": "100000000000000001", "TOTP_ENC_KEY": Fernet.generate_key().decode(),
                   "SUPPORT_DISCORD_URL": "https://discord.gg/x"})
for k in [k for k in os.environ if k.startswith("PAYPAL_")]:
    del os.environ[k]
import app as m, admin_2fa  # noqa: E402

ADMIN, BUYER, OTHER = "100000000000000001", "100000000000000006", "100000000000000007"
con = sqlite3.connect(os.environ["DATABASE_PATH"])
for uid, n in ((ADMIN, "Admin"), (BUYER, "Buyer"), (OTHER, "Other")):
    con.execute("INSERT INTO users VALUES (?,?,NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)", (uid, n))
con.commit(); admin_2fa.enroll(con, ADMIN, admin_2fa.new_secret(), 1, admin_2fa.new_recovery_codes()); con.close()
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
def client(uid=None, mfa=False):
    c = m.app.test_client(); c.get("/lang/en")
    with c.session_transaction() as s:
        s["csrf_token"] = "tok"
        if uid: s["discord_id"] = uid
        if mfa: s["mfa"] = {"uid": uid, "at": int(time.time())}
    return c
def text(r): return html.unescape(r.data.decode())
def fresh_shop(): m._shop_cache.update(at=0.0, data=None)
def buy(c, item, key="", consent="1"):
    return c.post("/shop/buy", data={"csrf_token": "tok", "item": item, "key": key, "consent": consent})
def pid_of(r): return r.headers["Location"].rsplit("=", 1)[-1]
def my_keys(uid): return [k for k in mock.keys.values() if k["owner"] == uid]

# ---------- shop page ----------
anon = client()
t = text(anon.get("/shop"))
check("shop: Free, Pro and the 2 add-ons with prices from the license server", "€19.99" in t and t.count("€5.99") >= 2
      and "One payment, yours for ever" in t and "Works on Free and Pro" in t and "Servers per key" in t)
check("shop: logged out -> log in to buy", "Log in with Discord to buy" in t and "/login?next=/shop" in t)
check("navbar links the shop", 'href="/shop"' in text(anon.get("/")))
check("homepage buttons go to the shop", 'href="/shop#pro"' in text(anon.get("/")))
check("CSP lets the Buy form redirect to PayPal", "https://www.paypal.com" in anon.get("/shop").headers["Content-Security-Policy"])
check("test mode banner hidden from customers", "Test mode" not in text(client(BUYER).get("/shop")))
check("test mode banner for admins", "Test mode" in text(client(ADMIN, mfa=True).get("/shop")))
mock.SHOP["enabled"] = False; fresh_shop()
check("shop disabled -> Coming soon", text(client(BUYER).get("/shop")).count("Coming soon") == 3)
mock.SHOP["enabled"] = True; mock.SHOP_ITEMS[2]["available"] = False; fresh_shop()
check("item not available -> Coming soon", text(client(BUYER).get("/shop")).count("Coming soon") == 1)
mock.SHOP_ITEMS[2]["available"] = True; fresh_shop()

# ---------- add-on, new customer ----------
bu = client(BUYER)
check("buy needs login", anon.post("/shop/buy", data={"csrf_token": "tok", "item": "antixray", "consent": "1"}).status_code in (302, 303)
      and not mock.purchases)
r = buy(bu, "antixray", consent="")
check("consent required", r.status_code == 302 and not mock.purchases)
check("CSRF required", bu.post("/shop/buy", data={"item": "antixray", "consent": "1"}).status_code == 400)
check("unknown item refused", buy(bu, "fly").status_code == 302 and not mock.purchases)
r = buy(bu, "antixray")
check("buy -> redirect to PayPal approveUrl", r.status_code == 303 and r.headers["Location"].startswith("https://www.sandbox.paypal.com/"))
order = pid_of(r)
check("subscribe called with the session's Discord ID", mock.purchases[order]["owner"] == BUYER)
r = client(OTHER).get(f"/shop/done?token={order}&PayerID=X")
check("someone else's id: license server error shown, nothing given", "This subscription is not yours." in text(r) and not my_keys(OTHER))
r = bu.get(f"/shop/done?token={order}&PayerID=X")
t = text(r)
check("not paid yet: Waiting for PayPal with polling", "Waiting for PayPal" in t and "data-shop-poll" in t and not my_keys(BUYER))
r = bu.post("/shop/done/check", data={"csrf_token": "tok", "id": order})
check("poll: still waiting", r.get_json() == {"ok": True, "paid": False, "waiting": True})
mock.pay(order)
check("poll: paid", bu.post("/shop/done/check", data={"csrf_token": "tok", "id": order}).get_json()["paid"] is True)
t = text(bu.get(f"/shop/done?token={order}&PayerID=X"))
keys = my_keys(BUYER)
check("paid: Free key with Anti-XRay, shown with copy and install steps", len(keys) == 1 and keys[0]["plan"] == "free"
      and keys[0]["addons"] == ["antixray"] and keys[0]["key"] in t and "mlicense " + keys[0]["key"] in t and "data-copy" in t
      and "Anti-XRay is now active on" in t)
check("the site does not trust a made-up id", "Something went wrong" in text(bu.get("/shop/done?token=FAKE12345")))
check("bad id format -> back to the shop", bu.get("/shop/done?token=../x").status_code == 302)
free_key = keys[0]["key"]

# ---------- second add-on on the same key, Pro upgrade ----------
t = text(bu.get("/shop"))
check("add-on already on the key: option disabled + Owned", "Owned" in t and "Upgrade " + free_key + " (keeps its add-ons)" in t)
r = buy(bu, "antixray", key=free_key)
check("license server refusal shown (already has it)", "already has the Anti-XRay add-on" in text(bu.get("/shop")) and r.status_code == 302)
r = buy(bu, "antiesp", key=free_key); mock.pay(pid_of(r)); bu.get(f"/shop/done?token={pid_of(r)}")
check("Anti-ESP added to the chosen key", mock.keys[free_key]["addons"] == ["antiesp", "antixray"])
t = text(bu.get("/shop"))
check("both add-ons owned -> Owned, no buy button for them", t.count("Owned") >= 2 and t.count('value="antiesp"') == 0)
r = buy(bu, "pro", key=free_key)
sub = pid_of(r)
check("Pro: subscription started for the Free key", sub.startswith("I-") and mock.purchases[sub]["_target"] == free_key)
mock.pay(sub)
t = text(bu.get(f"/shop/done?subscription_id={sub}&ba_token=X"))
k = mock.keys[free_key]
check("Pro paid: the Free key is now Pro and keeps its add-ons", k["plan"] == "pro" and k["addons"] == ["antiesp", "antixray"] and free_key in t)

# ---------- customer area ----------
d = text(bu.get("/dashboard"))
check("dashboard: Renews on + Lifetime add-ons", "Renews on" in d and d.count("Lifetime") >= 2)
check("dashboard: Purchases with Monthly / One-time and states", "Purchases" in d and "Monthly" in d and "One-time" in d and "Active" in d and "Paid" in d)
check("dashboard: Cancel only for the active Pro", d.count("/cancel") == 1 and "Your key keeps working until" in d)
r = bu.post(f"/shop/purchases/{order}/cancel", data={"csrf_token": "tok"}, follow_redirects=True)
check("add-ons cannot be cancelled", "Add-ons are paid once" in text(r))
r = bu.post(f"/shop/purchases/{sub}/cancel", data={"csrf_token": "tok"}, follow_redirects=True)
check("cancel Pro: subscription cancelled, key still Pro", mock.purchases[sub]["status"] == "CANCELLED" and "will not be charged again" in text(r)
      and mock.keys[free_key]["plan"] == "pro")
check("someone else cannot cancel", "not yours" in text(client(OTHER).post(f"/shop/purchases/{sub}/cancel", data={"csrf_token": "tok"}, follow_redirects=True)))
oc = client(OTHER)
fk = [k for k in my_keys(OTHER)]
r = buy(oc, "antiesp"); mock.pay(pid_of(r)); oc.get(f"/shop/done?token={pid_of(r)}")
d = text(oc.get("/dashboard"))
check("Free key: Never expires, Upgrade to Pro, Add Anti-XRay", "Never expires" in d and "Upgrade to Pro" in d and "Add Anti-XRay" in d and "Add Anti-ESP" not in d)

# ---------- admin ----------
ad = client(ADMIN, mfa=True)
t = text(ad.get("/admin/purchases"))
check("admin purchases: all, with sync and cancel only for active Pro", sub in t and order in t and "Sync from PayPal" in t)
check("admin purchases: admin only", bu.get("/admin/purchases").status_code == 403)
check("admin filter by owner", order not in text(ad.get(f"/admin/purchases?owner={OTHER}")))
r = buy(oc, "pro"); s2 = pid_of(r); mock.pay(s2)
r = ad.post(f"/admin/purchases/{s2}/sync", data={"csrf_token": "tok"}, follow_redirects=True)
check("admin sync applies a paid purchase", "Synced from PayPal: Active" in text(r) and any(k["plan"] == "pro" for k in my_keys(OTHER)))
r = ad.post(f"/admin/purchases/{s2}/cancel", data={"csrf_token": "tok"}, follow_redirects=True)
check("admin cancel", mock.purchases[s2]["status"] == "CANCELLED" and "Subscription cancelled at PayPal" in text(r))

# ---------- legal + docs ----------
terms = text(anon.get("/terms"))
check("terms: Payments and refunds", "Payments and refunds" in terms and "renews every month" in terms and "for ever" in terms and "1 server per key" in terms)
check("privacy: PayPal listed when the shop is on", "PayPal (Europe)" in text(anon.get("/privacy")))
docs_t = text(anon.get("/docs"))
check("docs 1.25: add-ons, buying, /ac license, MythrasProxy", "Anti-StorageESP" in docs_t and "Buying." in docs_t
      and "/ac license" in docs_t and "MythrasProxy" in docs_t and "1.25" in docs_t and "IP not allowed" in docs_t)
it = client(BUYER); it.get("/lang/it")
check("Italian shop", "Abbonati con PayPal" in text(it.get("/shop")))

print(f"\n{len(fails)} failed" if fails else "\nALL PASSED"); srv.shutdown()
