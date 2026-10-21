import os, sys, tempfile, threading, sqlite3, time, html, re
from http.server import ThreadingHTTPServer
from cryptography.fernet import Fernet
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import mock_license_server as mock
srv = ThreadingHTTPServer(("127.0.0.1", 5095), mock.Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update({"DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "p.db"), "DOWNLOADS_DIR": tempfile.mkdtemp(),
    "MYTHRAS_API_URL": "http://127.0.0.1:5095", "MYTHRAS_API_TOKEN": "test-token",
    "ADMIN_DISCORD_IDS": "100000000000000001", "TOTP_ENC_KEY": Fernet.generate_key().decode()})
import app as m, admin_2fa
ADMIN, CUST, NOBODY = "100000000000000001", "100000000000000002", "100000000000000009"
con = sqlite3.connect(os.environ["DATABASE_PATH"]); con.row_factory = sqlite3.Row
for uid, n in ((ADMIN, "Admin"), (CUST, "Cust"), (NOBODY, "Nobody")):
    con.execute("INSERT INTO users VALUES (?,?,NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)", (uid, n))
con.commit(); admin_2fa.enroll(con, ADMIN, admin_2fa.new_secret(), 1, admin_2fa.new_recovery_codes()); con.close()
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
def client(uid, lang="en"):
    c = m.app.test_client(); c.get(f"/lang/{lang}")
    with c.session_transaction() as s:
        s["discord_id"] = uid; s["csrf_token"] = "tok"; s["mfa"] = {"uid": uid, "at": int(time.time())}
    return c
def page(c, url):
    r = c.get(url); return r, html.unescape(r.data.decode())

K1, K3 = "MYTH-TEST-0001-ABCD-EFGH", "MYTH-TEST-0003-ABCD-EFGH"
check("panel needs login", m.app.test_client().get("/panel").status_code in (302, 303))
cu = client(CUST)
r, t = page(cu, "/panel")
check("customer panel 200", r.status_code == 200)
check("own servers shown, other customer's not", "Survival" in t and "Lobby" in t and "Network-1" not in t)
check("online/offline + TPS hidden when -1", "Online" in t and "Offline" in t and t.count(">TPS<") == 1)
check("chart + legend + table view", "<svg" in r.data.decode() and "Alerts and punishments per day" in t and "Show as table" in t)
check("top checks and top players", "Top checks" in t and "Top players" in t and "/panel/players/" in t)
rows = re.findall(r'<li class="feed-row type-(\w+)" data-time="(\d+)"', r.data.decode())
check("feed: first page of 50, newest first", len(rows) == 50 and [int(x[1]) for x in rows] == sorted([int(x[1]) for x in rows], reverse=True))
nxt = re.search(r'data-next="(\d+)"', r.data.decode()).group(1)
check("feed has a next page", nxt != "0")
check("only this customer's events", all(e["key"] == K1 for e in mock.panel_events if str(e["time"]) in {x[1] for x in rows}))
check("times rendered as local-time elements", 'class="feed-time small muted js-time"' in r.data.decode() and "data-ms=" in r.data.decode())
check("heads served from this site", "/heads/" in r.data.decode() and "mc-heads.net" not in r.data.decode())
check("token never in page", "test-token" not in r.data.decode())

j = cu.get(f"/panel/feed?before={nxt}").get_json()
older = [int(x) for x in re.findall(r'data-time="(\d+)"', j["html"])]
check("feed JSON: older page with before=", j["ok"] and older and all(x < int(nxt) for x in older))
j = cu.get("/panel/feed?type=replay").get_json()
check("feed filter type=replay + copy command", j["ok"] and "type-alert" not in j["html"] and "/ac replay play " in j["html"])
r, t = page(cu, "/panel?type=punish&days=30")
check("filters keep working on the page", "type-alert" not in r.data.decode() and "type-punish" in r.data.decode())
r, t = page(cu, "/panel?days=1")
check("1-day switch", 'aria-current="page">1 day' in t)
r, t = page(cu, "/panel?days=999")
check("invalid days -> 7", 'aria-current="page">7 days' in t)
r, t = page(cu, f"/panel?key={K3}")
check("someone else's key refused by the server", "This key is not yours" in t and "Network-1" not in t)

r, t = page(cu, "/panel/players/Notch?days=30")
check("player page: name, uuid copy, checks, history", r.status_code == 200 and "069a79f4-44e9-4726-a5be-fca90e38aaf5" in t and "Copy UUID" in t and "History" in t and 'class="bar-list"' in t)
check("player name validated", cu.get("/panel/players/<script>").status_code == 404)

r, t = page(client(NOBODY), "/panel")
check("empty state for a customer without servers", "No servers yet: start Mythras with your key" in t)

check("admin panel: non-admin 403", cu.get("/admin/panel").status_code == 403)
ad = client(ADMIN)
r, t = page(ad, "/admin/panel")
check("admin panel: all servers + key/owner", r.status_code == 200 and "Survival" in t and "Network-1" in t and "/admin/keys/MYTH-TEST-0001" in t and "Cust" in t)
j = ad.get("/admin/panel/feed").get_json()
check("admin feed JSON has key column", j["ok"] and "/admin/keys/" in j["html"])
r, t = page(ad, "/admin/panel/players/Notch?days=30")
check("admin player page", r.status_code == 200 and "Notch" in t)
r, t = page(ad, "/admin/ai")
check("Cloud AI page: models, chart, keys, left out", r.status_code == 200 and "97.0%" in t and "Neural network" in t and "Accuracy over time" in t and "Left out" in t and "Cust" in t)
check("Cloud AI: non-admin 403", cu.get("/admin/ai").status_code == 403)
check("admin nav has Panel + Cloud AI", 'href="/admin/panel"' in t and 'href="/admin/ai"' in t)

check("heads: bad uuid 404", cu.get("/heads/notauuid.png").status_code == 404)
check("heads need login", m.app.test_client().get("/heads/069a79f4-44e9-4726-a5be-fca90e38aaf5.png").status_code in (302, 303))

r, t = page(client(CUST, "it"), "/panel")
check("Italian panel", "Pannello" in t and "Feed in tempo reale" in t and "Check più frequenti" in t)
r, t = page(cu, "/dashboard")
check("dashboard links to the panel", 'href="/panel"' in t and "Open panel" in t)
check("no-store on panel", cu.get("/panel").headers.get("Cache-Control") == "no-store")
print(f"\n{len(fails)} failed" if fails else "\nALL PASSED"); srv.shutdown()
