import os, re, sys, tempfile, threading, sqlite3, html
from http.server import ThreadingHTTPServer

from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]

import mock_license_server as mock
srv = ThreadingHTTPServer(("127.0.0.1", 5056), mock.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["MYTHRAS_API_URL"] = "http://127.0.0.1:5056"
os.environ["MYTHRAS_API_TOKEN"] = "test-token"
os.environ["ADMIN_DISCORD_IDS"] = "100000000000000001"
from cryptography.fernet import Fernet
os.environ["TOTP_ENC_KEY"] = Fernet.generate_key().decode()
import time
import app as m
import admin_2fa

con = sqlite3.connect(os.environ["DATABASE_PATH"])
con.execute("INSERT INTO users VALUES ('100000000000000001','MythrasAdmin',NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)")
con.execute("INSERT INTO users VALUES ('100000000000000002','BlockBuilder',NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)")
con.commit()
con.row_factory = sqlite3.Row
admin_2fa.enroll(con, "100000000000000001", admin_2fa.new_secret(), 1, admin_2fa.new_recovery_codes())
con.close()

fails = []
bodies = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

def client(uid=None, mfa=False):
    c = m.app.test_client()
    if uid:
        with c.session_transaction() as s:
            s["discord_id"] = uid; s["csrf_token"] = "tok"
            if mfa: s["mfa"] = {"uid": uid, "at": int(time.time())}
    return c

def get(c, url):
    r = c.get(url, follow_redirects=True); bodies.append(r.data); return r, r.data.decode()

def post(c, url, data):
    d = dict(data, csrf_token="tok")
    r = c.post(url, data=d, follow_redirects=True); bodies.append(r.data); return r, html.unescape(r.data.decode())

anon = client()
check("index 200", anon.get("/").status_code == 200)
check("dashboard needs login", anon.get("/dashboard").status_code == 302)
check("admin: anonymous gets 403", anon.get("/admin").status_code == 403)

# ---- customer
cu = client("100000000000000002")
r, t = get(cu, "/dashboard")
check("customer sees own key (masked)", "MYTH-••••-••••-••••-EFGH" in t and 'data-key="MYTH-TEST-0001-ABCD-EFGH"' in t)
check("customer does not see others' keys", "MYTH-TEST-0003" not in t)
check("reset disabled until nextReset", "Available in 5h" in t and "disabled" in t)
check("server list shown", "Online" in t)
r, t = post(cu, "/dashboard/keys/MYTH-TEST-0001-ABCD-EFGH/reset", {})
check("reset refused shows API error", "You can reset again in 5 hours" in t)
r, t = post(cu, "/dashboard/claim", {"key": "myth-test-0002-abcd-efgh"})
check("claim unowned key", "is now linked to your account" in t and 'data-key="MYTH-TEST-0002-ABCD-EFGH"' in t)
r, t = post(cu, "/dashboard/claim", {"key": "MYTH-TEST-0003-ABCD-EFGH"})
check("claim someone else's key shows API error", "This key belongs to someone else" in t)
r, t = post(cu, "/dashboard/claim", {"key": "../api/stats"})
check("claim rejects malformed key locally", "Enter a valid license key" in t)
r, t = post(cu, "/dashboard/keys/MYTH-TEST-0002-ABCD-EFGH/reset", {})
check("reset own key ok", "Servers reset for MYTH-TEST-0002-ABCD-EFGH" in t)
r, t = post(cu, "/dashboard/keys/MYTH-TEST-0003-ABCD-EFGH/reset", {})
check("cannot reset another user's key", "This key is not yours" in t)
check("customer blocked from admin", cu.get("/admin").status_code == 403)
check("customer blocked from admin action", cu.post("/admin/keys/MYTH-TEST-0003-ABCD-EFGH/revoke", data={"csrf_token": "tok"}).status_code == 403)
check("POST without CSRF rejected", cu.post("/dashboard/claim", data={"key": "MYTH-TEST-0002-ABCD-EFGH"}).status_code == 400)

# ---- admin
ad = client("100000000000000001", mfa=True)
r, t = get(ad, "/admin")
check("overview stats", "Total keys" in t and "Servers online" in t and "Latest events" in t)
r, t = get(ad, "/admin/keys?plan=pro")
check("keys filter by plan", "MYTH-TEST-0001-ABCD-EFGH" in t and "MYTH-TEST-0003-ABCD-EFGH" not in t)
r, t = get(ad, "/admin/keys?q=network")
check("keys search", "MYTH-TEST-0003-ABCD-EFGH" in t and "MYTH-TEST-0001-ABCD-EFGH" not in t)
r, t = post(ad, "/admin/keys/new", {"plan": "pro", "days": "30", "servers": "", "owner": "100000000000000002", "note": "Order #99"})
new_key = re.search(r'class="key">(MYTH-[A-Z0-9-]+)<', t)
check("create key -> shown with copy", bool(new_key) and "License created" in t)
nk = new_key.group(1) if new_key else "MYTH-TEST-0004-ABCD-EFGH"
r, t = post(ad, "/admin/keys/new", {"plan": "gold"})
check("create rejects unknown plan", "Choose a valid plan" in t)
r, t = post(ad, "/admin/keys/new", {"plan": "pro", "owner": "abc"})
check("create rejects bad owner", "Owner must be a Discord user ID" in t)
r, t = post(ad, f"/admin/keys/{nk}/revoke", {})
check("revoke", "Key revoked" in t and "Unrevoke" in t)
r, t = post(ad, f"/admin/keys/{nk}/revoke", {})
check("revoke twice shows API error", "This key is already revoked" in t)
r, t = post(ad, f"/admin/keys/{nk}/unrevoke", {})
check("unrevoke", "Key restored" in t)
r, t = post(ad, f"/admin/keys/{nk}/plan", {"plan": "enterprise"})
check("change plan", "Plan changed" in t)
r, t = post(ad, f"/admin/keys/{nk}/extend", {"days": "15"})
check("extend", "Expiry updated" in t)
r, t = post(ad, f"/admin/keys/{nk}/extend", {"days": "-3"})
check("extend rejects negative", "Days must be 0 or more" in t)
r, t = post(ad, f"/admin/keys/{nk}/servers", {"servers": "5"})
check("server limit", "Server limit updated" in t and "/5" in t)
r, t = post(ad, f"/admin/keys/{nk}/note", {"note": "VIP customer"})
check("note", "Note saved" in t and "VIP customer" in t)
r, t = post(ad, f"/admin/keys/{nk}/owner", {"owner": ""})
check("owner removed", "Owner updated" in t)
r, t = post(ad, f"/admin/keys/{nk}/reset", {})
check("admin reset", "Servers reset." in t)
check("unknown action 404", ad.post(f"/admin/keys/{nk}/explode", data={"csrf_token": "tok"}).status_code == 404)
r, t = get(ad, "/admin/keys/MYTH-NOPE-0000-0000-0000")
check("unknown key shows API error", "Unknown key" in t)
r, t = get(ad, f"/admin/log?key={nk}&limit=50")
check("log filtered by key", nk in t and "MythrasAdmin (100000000000000001)" in t)
check("actor sent on every admin action", all(e["actor"] == "MythrasAdmin (100000000000000001)" for e in mock.events if e["key"] == nk))
r, t = get(ad, "/admin/keys/MYTH-TEST-0003-ABCD-EFGH")
check("detail shows server ips + events", "10.0.0.1" in t and "Key created" in t)

# ---- token never leaves the server
check("token never in any page", not any(b"test-token" in b for b in bodies))

# ---- bad token / not configured
os.environ["MYTHRAS_API_TOKEN"] = "wrong"
r, t = get(cu, "/dashboard")
check("bad token -> friendly error", "could not authenticate with the license server" in t)
os.environ["MYTHRAS_API_URL"] = ""
r, t = get(cu, "/dashboard")
check("not configured -> friendly error", "not configured" in t)
os.environ["MYTHRAS_API_URL"] = "http://127.0.0.1:5999"
os.environ["MYTHRAS_API_TOKEN"] = "test-token"
r, t = get(ad, "/admin")
check("unreachable -> friendly error", "unreachable" in t)

print(f"\n{len(fails)} failed" if fails else "\nALL PASSED")
srv.shutdown()
