import os, sys, tempfile, threading, sqlite3, time, html
from http.server import ThreadingHTTPServer
from cryptography.fernet import Fernet
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import mock_license_server as mock
srv = ThreadingHTTPServer(("127.0.0.1", 5091), mock.Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update({"DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "r.db"), "DOWNLOADS_DIR": tempfile.mkdtemp(),
    "MYTHRAS_API_URL": "http://127.0.0.1:5091", "MYTHRAS_API_TOKEN": "test-token",
    "ADMIN_DISCORD_IDS": "100000000000000001", "TOTP_ENC_KEY": Fernet.generate_key().decode()})
import app as m, admin_2fa
ADMIN, CUST = "100000000000000001", "100000000000000002"
con = sqlite3.connect(os.environ["DATABASE_PATH"]); con.row_factory = sqlite3.Row
con.execute("INSERT INTO users VALUES (?,'Admin',NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)", (ADMIN,))
con.execute("INSERT INTO users VALUES (?,'Cust',NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)", (CUST,))
con.commit(); admin_2fa.enroll(con, ADMIN, admin_2fa.new_secret(), 1, admin_2fa.new_recovery_codes()); con.close()
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
def client(uid, lang="en", mfa=True):
    c = m.app.test_client(); c.get(f"/lang/{lang}")
    with c.session_transaction() as s:
        s["discord_id"] = uid; s["csrf_token"] = "tok"
        if mfa: s["mfa"] = {"uid": uid, "at": int(time.time())}
    return c
DAY = mock.DAY
K = "MYTH-TEST-0001-ABCD-EFGH"   # seeded pro key, owner CUST, never expires
mock.keys[K]["expires"] = mock.now() + 10 * DAY

adm = client(ADMIN)
t = html.unescape(adm.get(f"/admin/keys/{K}").data.decode())
check("key page has Renew (+1 month) button", "Renew (+1 month)" in t and f"/admin/keys/{K}/renew" in t)
before = mock.keys[K]["expires"]
r = adm.post(f"/admin/keys/{K}/renew", data={"csrf_token": "tok"}, follow_redirects=True)
check("renew adds 30 days from expiry", abs(mock.keys[K]["expires"] - before - 30 * DAY) < 5000 and "Renewed for 1 month." in html.unescape(r.data.decode()))
check("renew logged with admin actor", mock.events[0]["label"] == "Key renewed" and "Admin" in mock.events[0]["actor"])
check("renew needs CSRF", adm.post(f"/admin/keys/{K}/renew").status_code == 400)
check("renew needs admin", client(CUST).post(f"/admin/keys/{K}/renew", data={"csrf_token": "tok"}).status_code == 403)
check("renew needs 2FA", client(ADMIN, mfa=False).post(f"/admin/keys/{K}/renew", data={"csrf_token": "tok"}).status_code in (302, 303))
mock.keys[K]["expires"] = mock.now() - 2 * DAY
adm.post(f"/admin/keys/{K}/renew", data={"csrf_token": "tok"})
check("renew of an expired key counts from today", abs(mock.keys[K]["expires"] - mock.now() - 30 * DAY) < 5000)
t = html.unescape(client(ADMIN, "it").get(f"/admin/keys/{K}").data.decode())
check("IT button", "Rinnova (+1 mese)" in t)
t = html.unescape(adm.get("/admin/keys/new").data.decode())
check("create form: empty = 1 month", "Empty = 1 month, 0 = never expires" in t)

cu = client(CUST)
def dash(lang="en"):
    return html.unescape(client(CUST, lang).get("/dashboard").data.decode())
mock.keys[K]["expires"] = mock.now() + 20 * DAY + 3600_000
t = dash()
check("customer sees Expires on + date + days left", "Expires on" in t and "21 days left" in t and 'class="expiry-date"' in t)
mock.keys[K]["expires"] = mock.now() + 2 * DAY
t = dash()
check("expiring soon is highlighted", 'class="expiry is-soon"' in t and "2 days left" in t and "Expires soon" in t)
mock.keys[K]["expires"] = mock.now() + 3600_000
check("1 day left", "1 day left" in dash())
mock.keys[K]["expires"] = mock.now() - DAY
t = dash()
check("expired: Expired on + Free mode note", "Expired on" in t and "runs in Free mode" in t and "is-expired" in t)
mock.keys[K]["expires"] = 0
check("never expires", 'class="expiry-date">Never' in dash())
mock.keys[K]["expires"] = mock.now() + 15 * DAY
con = sqlite3.connect(os.environ["DATABASE_PATH"])
con.execute("INSERT INTO orders (order_id, discord_id, plan, license_key, status, subscription_id, sub_status, renews_at, created_at, updated_at) VALUES ('1', ?, 'pro', ?, 'fulfilled', '5', 'active', '2026-11-01T00:00:00Z', '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00')", (CUST, K))
con.commit(); con.close()
t = dash()
check("no automatic-renewal text any more", "Renews automatically" not in t)
t = dash("it")
check("IT: Scade il + giorni rimasti", "Scade il" in t and "giorni rimasti" in t and "Rinnova (+1 mese)" not in t)
t = dash("de")
check("DE: Läuft ab am", "Läuft ab am" in t and "Noch 15 Tage" in t)
print(f"\n{len(fails)} failed" if fails else "\nALL PASSED"); srv.shutdown()
