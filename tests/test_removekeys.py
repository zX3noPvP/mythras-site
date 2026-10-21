import os, sys, tempfile, threading, sqlite3, time, html
from http.server import ThreadingHTTPServer
from cryptography.fernet import Fernet
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import mock_license_server as mock
srv = ThreadingHTTPServer(("127.0.0.1", 5093), mock.Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update({"DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "x.db"), "DOWNLOADS_DIR": tempfile.mkdtemp(),
    "MYTHRAS_API_URL": "http://127.0.0.1:5093", "MYTHRAS_API_TOKEN": "test-token",
    "ADMIN_DISCORD_IDS": "100000000000000001", "TOTP_ENC_KEY": Fernet.generate_key().decode()})
import app as m, admin_2fa
ADMIN, CUST = "100000000000000001", "100000000000000002"
con = sqlite3.connect(os.environ["DATABASE_PATH"]); con.row_factory = sqlite3.Row
con.execute("INSERT INTO users VALUES (?,'Admin',NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)", (ADMIN,))
con.execute("INSERT INTO users VALUES (?,'Cust',NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)", (CUST,))
con.execute("INSERT INTO orders (order_id, discord_id, plan, license_key, status, created_at, updated_at) VALUES ('1', ?, 'pro', 'MYTH-TEST-0001-ABCD-EFGH', 'fulfilled', 'x', 'x')", (CUST,))
con.commit(); admin_2fa.enroll(con, ADMIN, admin_2fa.new_secret(), 1, admin_2fa.new_recovery_codes()); con.close()
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
def client(uid, lang="en", fresh=True):
    c = m.app.test_client(); c.get(f"/lang/{lang}")
    with c.session_transaction() as s:
        s["discord_id"] = uid; s["csrf_token"] = "tok"
        s["mfa"] = {"uid": uid, "at": int(time.time()) - (0 if fresh else 3600)}
    return c
owned = [k for k, v in mock.keys.items() if v["owner"] == CUST]
mock.keys["MYTH-TEST-0002-ABCD-EFGH"]["owner"] = CUST; owned.append("MYTH-TEST-0002-ABCD-EFGH")
check("customer has keys before", len(owned) >= 2 and "MYTH-TEST-0001" in html.unescape(client(CUST).get("/dashboard").data.decode()))
t = html.unescape(client(ADMIN).get(f"/admin/keys?q={CUST}").data.decode())
check("keys page shows the panel with the ID prefilled", "Delete a customer's keys" in t and f'value="{CUST}"' in t)
check("non-admin -> 403", client(CUST).post("/admin/users/remove-keys", data={"csrf_token": "tok", "discord_id": CUST}).status_code == 403)
check("needs CSRF", client(ADMIN).post("/admin/users/remove-keys", data={"discord_id": CUST}).status_code == 400)
r = client(ADMIN, fresh=False).post("/admin/users/remove-keys", data={"csrf_token": "tok", "discord_id": CUST})
check("stale 2FA -> asks for a fresh code, nothing changed", r.status_code in (302, 303) and "/admin/2fa" in r.headers["Location"] and all(k in mock.keys and mock.keys[k]["owner"] == CUST for k in owned))
check("invalid ID refused", "valid Discord ID" in html.unescape(client(ADMIN).post("/admin/users/remove-keys", data={"csrf_token": "tok", "discord_id": "abc"}, follow_redirects=True).data.decode()))
r = client(ADMIN).post("/admin/users/remove-keys", data={"csrf_token": "tok", "discord_id": CUST, "delete_orders": "1"}, follow_redirects=True)
t = html.unescape(r.data.decode())
check("success message", f"Deleted {len(owned)} keys of {CUST}" in t and "Purchases deleted: 1" in t)
check("all keys deleted on the license server", not any(k in mock.keys for k in owned))
check("gone from the admin list too", not any(k in html.unescape(client(ADMIN).get("/admin/keys").data.decode()) for k in owned))
d = html.unescape(client(CUST).get("/dashboard").data.decode())
check("customer area no longer shows them", not any(k in d for k in owned) and "MYTH-TEST-0001" not in d)
check("purchase history deleted", sqlite3.connect(os.environ["DATABASE_PATH"]).execute("SELECT COUNT(*) FROM orders WHERE discord_id=?", (CUST,)).fetchone()[0] == 0)
check("actions logged with admin as actor", any(e["action"] == "delete" and "Admin" in e["actor"] for e in mock.events))
r = client(ADMIN, "it").post("/admin/users/remove-keys", data={"csrf_token": "tok", "discord_id": CUST}, follow_redirects=True)
check("again -> no keys (IT)", "Questo utente non ha chiavi." in html.unescape(r.data.decode()))

# single key
K3 = "MYTH-TEST-0003-ABCD-EFGH"
t = html.unescape(client(ADMIN).get(f"/admin/keys/{K3}").data.decode())
check("key page has Delete button", f"/admin/keys/{K3}/delete" in t and "Delete key" in t)
check("delete key: non-admin 403", client(CUST).post(f"/admin/keys/{K3}/delete", data={"csrf_token": "tok"}).status_code == 403)
r = client(ADMIN, fresh=False).post(f"/admin/keys/{K3}/delete", data={"csrf_token": "tok"})
check("delete key: stale 2FA asks for code", "/admin/2fa" in r.headers.get("Location", "") and K3 in mock.keys)
r = client(ADMIN).post(f"/admin/keys/{K3}/delete", data={"csrf_token": "tok"}, follow_redirects=True)
check("delete key: gone + message", K3 not in mock.keys and f"Key {K3} deleted." in html.unescape(r.data.decode()))
r = client(ADMIN).post("/admin/keys/MYTH-NOPE-0000-ABCD-EFGH/delete", data={"csrf_token": "tok"}, follow_redirects=True)
check("delete unknown key -> error shown", "Unknown key" in html.unescape(r.data.decode()))

print(f"\n{len(fails)} failed" if fails else "\nALL PASSED"); srv.shutdown()
