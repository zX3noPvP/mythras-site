import os, sys, tempfile, threading, sqlite3, time, html
from http.server import ThreadingHTTPServer
from cryptography.fernet import Fernet
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import mock_license_server as mock
srv = ThreadingHTTPServer(("127.0.0.1", 5081), mock.Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update({"DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "a.db"), "DOWNLOADS_DIR": tempfile.mkdtemp(),
    "MYTHRAS_API_URL": "http://127.0.0.1:5081", "MYTHRAS_API_TOKEN": "test-token",
    "ADMIN_DISCORD_IDS": "100000000000000001,100000000000000004", "TOTP_ENC_KEY": Fernet.generate_key().decode()})
import app as m, admin_2fa
con = sqlite3.connect(os.environ["DATABASE_PATH"]); con.row_factory = sqlite3.Row
con.execute("INSERT INTO users VALUES ('100000000000000001','Admin',NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)")
con.execute("INSERT INTO users VALUES ('100000000000000004','NewAdmin',NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)")
con.commit(); admin_2fa.enroll(con, "100000000000000001", admin_2fa.new_secret(), 1, admin_2fa.new_recovery_codes()); con.close()
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
def client(uid, lang, mfa=True):
    c = m.app.test_client(); c.get(f"/lang/{lang}")
    with c.session_transaction() as s:
        s["discord_id"] = uid; s["csrf_token"] = "tok"
        if mfa: s["mfa"] = {"uid": uid, "at": int(time.time())}
    return c
pages = {"/admin": "Panoramica", "/admin/keys": "Chiavi", "/admin/keys/new": "Nuova licenza",
         "/admin/keys/MYTH-TEST-0003-ABCD-EFGH": "Azioni", "/admin/log": "Ultimi 200", "/admin/downloads": "Pubblica una nuova build", "/admin/admins": "Resetta la mia 2FA"}
it = client("100000000000000001", "it")
for url, word in pages.items():
    r = it.get(url); t = html.unescape(r.data.decode())
    check(f"IT {url} -> 200 + '{word}'", r.status_code == 200 and word in t)
de = client("100000000000000001", "de")
t = html.unescape(de.get("/admin/keys/MYTH-TEST-0003-ABCD-EFGH").data.decode())
check("DE key page", "Schlüssel sperren" in t and "Serverlimit" in t)
t = html.unescape(client("100000000000000001", "fr", mfa=False).get("/admin/2fa?next=/admin").data.decode())
check("FR 2FA verify page", "Saisissez votre code" in t)
t = html.unescape(client("100000000000000004", "es", mfa=False).get("/admin/2fa/setup").data.decode())
check("ES 2FA setup page", "Configura la autenticación en dos pasos" in t)
r = client("100000000000000001", "it").post("/admin/keys/MYTH-TEST-0003-ABCD-EFGH/note", data={"csrf_token": "tok", "note": "x"}, follow_redirects=True)
check("IT action flash translated", "Nota salvata." in html.unescape(r.data.decode()))
r = client("100000000000000001", "it").post("/admin/keys/new", data={"csrf_token": "tok", "plan": "gold"}, follow_redirects=True)
check("IT validation error translated", "Scegli un piano valido." in html.unescape(r.data.decode()))
lk = client("100000000000000001", "it", mfa=False)
r = lk.post("/admin/2fa", data={"csrf_token": "tok", "code": "000000"}, follow_redirects=True)
check("IT wrong-code message translated", "Codice sbagliato o già usato" in html.unescape(r.data.decode()))
print(f"\n{len(fails)} failed" if fails else "\nALL PASSED"); srv.shutdown()
