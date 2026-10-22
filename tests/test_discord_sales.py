import os, sys, tempfile, threading, sqlite3, html
from http.server import ThreadingHTTPServer
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import mock_license_server as mock
srv = ThreadingHTTPServer(("127.0.0.1", 5097), mock.Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
INVITE = "https://discord.gg/mythrasTEST"
os.environ.update({"DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "d.db"), "DOWNLOADS_DIR": tempfile.mkdtemp(),
                   "MYTHRAS_API_URL": "http://127.0.0.1:5097", "MYTHRAS_API_TOKEN": "test-token", "SUPPORT_DISCORD_URL": INVITE})
import app as m
UID = "100000000000000002"
con = sqlite3.connect(os.environ["DATABASE_PATH"])
con.execute("INSERT INTO users VALUES (?,'Cust',NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)", (UID,)); con.commit(); con.close()
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
c = m.app.test_client()
with c.session_transaction() as s: s["discord_id"] = UID; s["csrf_token"] = "tok"
home = html.unescape(m.app.test_client().get("/").data.decode())
dash = html.unescape(c.get("/dashboard").data.decode())
check("pricing: paid tiers open the Discord invite", home.count(f'href="{INVITE}"') >= 3 and "Buy on Discord" in home)
check("dashboard: 3 Discord buttons + steps", dash.count(f'href="{INVITE}"') >= 3 and "Open a ticket" in dash and "Get your key" in dash)
check("no online payment left", all(w not in (home + dash) for w in ("PayPal", "Lemon", 'action="/buy"', "test_pro")))
check("/buy and payment webhooks are gone", c.post("/buy", data={"csrf_token": "tok", "plan": "pro"}).status_code == 404
      and m.app.test_client().post("/webhooks/paypal").status_code in (400, 404)
      and m.app.test_client().get("/webhooks/paypal").status_code in (404, 405))
check("CSP form-action only self", "form-action 'self';" in m.app.test_client().get("/").headers["Content-Security-Policy"])
terms = html.unescape(m.app.test_client().get("/terms").data.decode())
check("terms: Discord tickets, no automatic renewal", "opening a ticket on our Discord server" in terms and "does not renew by itself" in terms)
check("privacy/cookies: no PayPal", "PayPal" not in m.app.test_client().get("/privacy").data.decode() + m.app.test_client().get("/cookies").data.decode())
c.get("/lang/it")
check("Italian button", "Acquista su Discord" in html.unescape(c.get("/dashboard").data.decode()))
print(f"\n{len(fails)} failed" if fails else "\nALL PASSED"); srv.shutdown()
