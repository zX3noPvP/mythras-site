"""Rate limits (per user / per IP, shared through SQLite) and HSTS."""
import os, sqlite3, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
os.environ.update({"DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "r.db"), "DOWNLOADS_DIR": tempfile.mkdtemp(),
                   "MYTHRAS_API_URL": "http://127.0.0.1:1", "MYTHRAS_API_TOKEN": "x", "TRUST_PROXY": "1"})
import app as m, ratelimit  # noqa: E402
# one-day windows here: the counts must not restart if the test crosses a window boundary
ratelimit.RULES.update({rule: (limit, 86400) for rule, (limit, _s) in ratelimit.RULES.items()})
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
UID = "100000000000000009"
con = sqlite3.connect(os.environ["DATABASE_PATH"])
con.execute("INSERT INTO users VALUES (?,'U',NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)", (UID,)); con.commit(); con.close()
def client(uid=None):
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["csrf_token"] = "tok"
        if uid: s["discord_id"] = uid
    return c

# "Link a key": 10 tries per 10 minutes per account (stops guessing keys)
c = client(UID)
codes = [c.post("/dashboard/claim", data={"csrf_token": "tok", "key": f"MYTH-AAAA-BBBB-CCCC-{i:04d}"}).status_code for i in range(12)]
check("claim: 10 allowed, then 429", codes[:10].count(429) == 0 and codes[10] == 429 and codes[11] == 429)
r = c.post("/dashboard/claim", data={"csrf_token": "tok", "key": "MYTH-AAAA-BBBB-CCCC-9999"})
check("429 page says how long to wait + Retry-After", r.status_code == 429 and int(r.headers["Retry-After"]) > 0 and "Too many requests" in r.data.decode())
check("another account is not blocked", client("100000000000000008").post("/dashboard/claim", data={"csrf_token": "tok", "key": "MYTH-X"}).status_code != 429)

# login: per IP (Cloudflare header), not per account
for i in range(20):
    m.app.test_client().get("/login", headers={"CF-Connecting-IP": "203.0.113.5"})
check("login: 21st request from the same IP -> 429", m.app.test_client().get("/login", headers={"CF-Connecting-IP": "203.0.113.5"}).status_code == 429)
check("login from another IP still works", m.app.test_client().get("/login", headers={"CF-Connecting-IP": "203.0.113.6"}).status_code != 429)

# JSON endpoints answer JSON
c2 = client("100000000000000007")
for i in range(40):
    c2.post("/shop/done/check", data={"csrf_token": "tok", "id": "ORDMOCK00001"})
r = c2.post("/shop/done/check", data={"csrf_token": "tok", "id": "ORDMOCK00001"})
check("polling over the limit -> JSON 429", r.status_code == 429 and r.get_json() == {"ok": False, "error": "too many requests"})

# static files and the health check are never limited
old = ratelimit.RULES["page"]; ratelimit.RULES["page"] = (2, 86400)
for i in range(5):
    m.app.test_client().get("/static/style.css", headers={"CF-Connecting-IP": "198.51.100.1"})
check("static files not limited", m.app.test_client().get("/static/style.css", headers={"CF-Connecting-IP": "198.51.100.1"}).status_code == 200)
for i in range(2):
    m.app.test_client().get("/terms", headers={"CF-Connecting-IP": "198.51.100.2"})
check("pages limited per IP (flood)", m.app.test_client().get("/terms", headers={"CF-Connecting-IP": "198.51.100.2"}).status_code == 429)
ratelimit.RULES["page"] = old

# HSTS only in production
check("no HSTS in development", "Strict-Transport-Security" not in m.app.test_client().get("/terms").headers)
m.PRODUCTION = True
h = m.app.test_client().get("/terms", headers={"CF-Connecting-IP": "198.51.100.3"}).headers.get("Strict-Transport-Security", "")
m.PRODUCTION = False
check("HSTS in production: 1 year, subdomains", h == "max-age=31536000; includeSubDomains")
print(f"\n{len(fails)} failed" if fails else "\nALL PASSED")
