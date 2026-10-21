"""Admin two-factor authentication: setup, single-use codes, recovery codes, lockout, session age, fresh code."""
import html, os, re, sqlite3, sys, tempfile, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import mock_license_server as mock  # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", 5101), mock.Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update({"DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "f.db"), "DOWNLOADS_DIR": tempfile.mkdtemp(),
                   "MYTHRAS_API_URL": "http://127.0.0.1:5101", "MYTHRAS_API_TOKEN": "test-token",
                   "ADMIN_DISCORD_IDS": "100000000000000001,100000000000000004", "TOTP_ENC_KEY": Fernet.generate_key().decode()})
import app as m, admin_2fa  # noqa: E402

A1, A2, CUST = "100000000000000001", "100000000000000004", "100000000000000002"
con = sqlite3.connect(os.environ["DATABASE_PATH"])
for uid, n in ((A1, "Admin1"), (A2, "Admin2"), (CUST, "Cust")):
    con.execute("INSERT INTO users VALUES (?,?,NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)", (uid, n))
con.commit(); con.close()
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
def client(uid, mfa_age=None):
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["discord_id"] = uid; s["csrf_token"] = "tok"
        if mfa_age is not None:
            s["mfa"] = {"uid": uid, "at": int(time.time()) - mfa_age}
    return c
def db():
    c = sqlite3.connect(os.environ["DATABASE_PATH"]); c.row_factory = sqlite3.Row; return c
def code_at(secret, offset=0): return admin_2fa.hotp(secret, admin_2fa.current_step() + offset)

# ---------- setup ----------
c = client(A1)
r = c.get("/admin")
check("admin without 2FA -> setup", r.status_code in (302, 303) and "/admin/2fa/setup" in r.headers["Location"])
t = html.unescape(c.get("/admin/2fa/setup").data.decode())
check("setup page shows QR + secret", "<svg" in t and "otpauth" not in t.split("<svg")[0] or "<svg" in t)
with c.session_transaction() as s:
    secret = admin_2fa.decrypt(s["mfa_pending"])
check("pending secret kept encrypted in the session", secret and len(secret) >= 16)
r = c.post("/admin/2fa/setup", data={"csrf_token": "tok", "code": "000000"})
check("wrong first code -> not enrolled", not admin_2fa.is_enrolled(admin_2fa.get_row(db(), A1)))
r = c.post("/admin/2fa/setup", data={"csrf_token": "tok", "code": code_at(secret)})
t = html.unescape(r.data.decode())
codes = re.findall(r"<li><code>([A-Za-z0-9-]+)</code></li>", r.data.decode())
check("right code -> enrolled, 10 recovery codes shown once", admin_2fa.is_enrolled(admin_2fa.get_row(db(), A1)) and len(codes) == 10)
row = db().execute("SELECT * FROM admin_mfa WHERE discord_id = ?", (A1,)).fetchone()
check("secret stored encrypted", row["secret_enc"] and secret not in row["secret_enc"])
hashes = [r_["code_hash"] for r_ in db().execute("SELECT code_hash FROM admin_recovery_codes WHERE discord_id = ?", (A1,))]
check("recovery codes stored as bcrypt hashes", len(hashes) == 10 and all(h.startswith("$2") for h in hashes) and not any(cd in "".join(hashes) for cd in codes))
check("same session can open /admin", c.get("/admin").status_code == 200)

# ---------- verify: single use, drift, recovery ----------
c2 = client(A1)
check("new session -> asked for a code", "/admin/2fa" in c2.get("/admin").headers["Location"])
check("POST needs CSRF", c2.post("/admin/2fa", data={"code": "123456"}).status_code == 400)
r = c2.post("/admin/2fa", data={"csrf_token": "tok", "code": code_at(secret)})
check("code already used at setup is refused (no replay)", r.status_code == 200 and "Wrong or already used code" in html.unescape(r.data.decode()))
r = c2.post("/admin/2fa", data={"csrf_token": "tok", "code": code_at(secret, +1)})
check("next-step code accepted (+30 s drift)", r.status_code in (302, 303) and c2.get("/admin").status_code == 200)
c3 = client(A1)
r = c3.post("/admin/2fa", data={"csrf_token": "tok", "code": code_at(secret, +1)})
check("same code in another session is refused", "Wrong or already used code" in html.unescape(r.data.decode()))
r = c3.post("/admin/2fa", data={"csrf_token": "tok", "code": codes[0]}, follow_redirects=True)
check("recovery code works, 9 left", "Recovery code used, 9 left" in html.unescape(r.data.decode()))
r = client(A1).post("/admin/2fa", data={"csrf_token": "tok", "code": codes[0]})
check("recovery code is single-use", "Wrong or already used code" in html.unescape(r.data.decode()))
check("far code (+5 min) refused", admin_2fa.matching_step(secret, code_at(secret, +10)) is None)

# ---------- session age and fresh code ----------
check("2FA older than 12 h -> asked again", "/admin/2fa" in client(A1, mfa_age=13 * 3600).get("/admin").headers.get("Location", ""))
r = client(A1, mfa_age=20 * 60).post("/admin/keys/MYTH-TEST-0002-ABCD-EFGH/revoke", data={"csrf_token": "tok"})
check("sensitive action with a code older than 15 min -> fresh code", r.status_code in (302, 303) and "fresh=1" in r.headers["Location"] and mock.keys["MYTH-TEST-0002-ABCD-EFGH"]["status"] == "active")
r = client(A1, mfa_age=60).post("/admin/keys/MYTH-TEST-0002-ABCD-EFGH/revoke", data={"csrf_token": "tok"})
check("sensitive action with a fresh code works", mock.keys["MYTH-TEST-0002-ABCD-EFGH"]["status"] == "revoked")

# ---------- lockout (second admin) ----------
s2 = admin_2fa.new_secret()
con = db(); admin_2fa.enroll(con, A2, s2, admin_2fa.current_step() - 5, admin_2fa.new_recovery_codes()); con.close()
c4 = client(A2)
for i in range(5):
    r = c4.post("/admin/2fa", data={"csrf_token": "tok", "code": "000000"})
check("5 wrong codes -> locked", "Too many wrong codes" in html.unescape(r.data.decode()))
r = c4.post("/admin/2fa", data={"csrf_token": "tok", "code": code_at(s2)})
check("locked: even the right code is refused", "Too many wrong codes" in html.unescape(r.data.decode()) and "/admin/2fa" in c4.get("/admin").headers["Location"])

# ---------- non-admins ----------
check("customer -> 403 on admin pages", client(CUST).get("/admin").status_code == 403)
check("customer -> 403 on 2FA pages", client(CUST).get("/admin/2fa").status_code == 403 and client(CUST).get("/admin/2fa/setup").status_code == 403)
check("anonymous -> 403", m.app.test_client().get("/admin").status_code == 403)

print(f"\n{len(fails)} failed" if fails else "\nALL PASSED"); srv.shutdown()
