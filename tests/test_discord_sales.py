"""When the license server's shop is off, sales go through Discord tickets (no PayPal buttons anywhere)."""
import os, sys, tempfile, threading, sqlite3, html
from http.server import ThreadingHTTPServer
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import mock_license_server as mock
mock.SHOP["enabled"] = False
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
shop = html.unescape(c.get("/shop").data.decode())
check("pricing: paid tiers open the Discord invite", home.count(f'href="{INVITE}"') >= 3 and "Buy on Discord" in home)
check("dashboard: Discord tickets explained", f'href="{INVITE}"' in dash and "Purchases happen on our Discord server" in dash)
check("no PayPal buttons while the shop is off", all(w not in (home + dash) for w in ("Pay with PayPal", "Subscribe with PayPal", "Lemon")))
check("shop page: Coming soon + Discord ticket", shop.count("Coming soon") == 3 and "Open a ticket on our Discord server" in shop)
check("buying is refused while the shop is off", c.post("/shop/buy", data={"csrf_token": "tok", "item": "pro", "consent": "1"}).status_code == 302
      and not mock.purchases)
check("old payment routes are gone", c.post("/billing/pro", data={"csrf_token": "tok"}).status_code == 404
      and m.app.test_client().post("/webhooks/paypal").status_code in (400, 404))
csp = m.app.test_client().get("/").headers["Content-Security-Policy"]
check("CSP form-action: this site and PayPal only", "form-action 'self' https://www.paypal.com https://www.sandbox.paypal.com;" in csp)
terms = html.unescape(m.app.test_client().get("/terms").data.decode())
check("terms: Discord tickets, ticket renewals", "opening a ticket on our Discord server" in terms and "does not renew by itself" in terms)
check("privacy/cookies: no PayPal while the shop is off", "PayPal" not in m.app.test_client().get("/privacy").data.decode() + m.app.test_client().get("/cookies").data.decode())
it = m.app.test_client(); it.get("/lang/it")
check("Italian button", "Acquista su Discord" in html.unescape(it.get("/").data.decode()))
print(f"\n{len(fails)} failed" if fails else "\nALL PASSED"); srv.shutdown()
