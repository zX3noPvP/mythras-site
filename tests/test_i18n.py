import os, sys, tempfile, html, sqlite3, urllib.parse
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
os.environ.update({
    "DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "i.db"),
    "MYTHRAS_API_URL": "http://127.0.0.1:1", "MYTHRAS_API_TOKEN": "x",
    "DISCORD_CLIENT_ID": "123", "DISCORD_CLIENT_SECRET": "abc",
    "LEGAL_CONTROLLER": "Mario Rossi", "LEGAL_EMAIL": "privacy@example.com",
})
import app as m

fails = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

def page(c, url, **kw):
    r = c.get(url, **kw); return r, html.unescape(r.data.decode())

c = m.app.test_client()
r, t = page(c, "/")
check("default English + html lang", 'lang="en"' in t and "Stop the cheaters." in t)
check("cookie banner shown on first visit", 'id="cookie-banner"' in t and "No tracking, no ads." in t)
check("no Google Fonts requests", "fonts.googleapis.com" not in t and "fonts.gstatic.com" not in t and "fonts/fonts.css" in t)
check("CSP fonts only from self", "font-src 'self';" in r.headers["Content-Security-Policy"] and "googleapis" not in r.headers["Content-Security-Policy"])
check("language menu lists 5 languages with flags", all(f'href="/lang/{code}?next=' in t for code in ("en", "it", "de", "fr", "es")) and t.count('href="#flag-') >= 6)

r, t = page(m.app.test_client(), "/", headers={"Accept-Language": "it-IT,it;q=0.9,en;q=0.5"})
check("browser language picked (Italian)", 'lang="it"' in t and "Ferma chi bara." in t and "Prezzi" in t)
r, t = page(m.app.test_client(), "/", headers={"Accept-Language": "ja-JP"})
check("unsupported browser language -> English", 'lang="en"' in t)

c = m.app.test_client()
r = c.get("/lang/de?next=/privacy")
setc = r.headers.get("Set-Cookie", "")
check("switch sets lang cookie and redirects back", r.status_code == 302 and r.headers["Location"].endswith("/privacy") and "lang=de" in setc and "HttpOnly" in setc and "SameSite=Lax" in setc)
r, t = page(c, "/privacy")
check("German privacy page with controller info", 'lang="de"' in t and "Datenschutzerklärung" in t and "Mario Rossi" in t and "privacy@example.com" in t)
for code, word in (("fr", "Politique relative aux cookies"), ("es", "Política de cookies"), ("it", "Cookie Policy"), ("en", "Cookie Policy")):
    cc = m.app.test_client(); cc.get(f"/lang/{code}")
    r, t = page(cc, "/cookies")
    check(f"cookie policy in {code}", r.status_code == 200 and f'lang="{code}"' in t and word in t and "__cf_bm" in t)
check("unknown language -> 404", m.app.test_client().get("/lang/xx").status_code == 404)
for bad in ("//evil.example", "https://evil.example", "/\\evil.example"):
    r = m.app.test_client().get("/lang/it?next=" + urllib.parse.quote(bad))
    check(f"open redirect blocked: {bad}", r.headers["Location"] in ("/", "http://localhost/"))

c = m.app.test_client()
r = c.get("/cookies/ok?next=/")
check("cookie notice OK sets cookie", r.status_code == 302 and "cookie_notice=1" in r.headers.get("Set-Cookie", ""))
r, t = page(c, "/")
check("banner hidden after OK", 'id="cookie-banner"' not in t)

r = m.app.test_client().get("/login")
q = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["Location"]).query)
check("Discord asks only for identify (no e-mail)", q.get("scope") == ["identify"])

# translated flash + date format for a signed-in customer
con = sqlite3.connect(os.environ["DATABASE_PATH"])
con.execute("INSERT INTO users VALUES ('100000000000000009','Tester',NULL,'old@example.com','2026-09-01T00:00:00+00:00',NULL)")
con.commit(); con.close()
m.init_db()
con = sqlite3.connect(os.environ["DATABASE_PATH"])
check("stored e-mails wiped (data minimisation)", con.execute("SELECT email FROM users").fetchone()[0] is None)
con.close()
c = m.app.test_client(); c.get("/lang/es")
with c.session_transaction() as s:
    s["discord_id"] = "100000000000000009"; s["csrf_token"] = "tok"
r = c.post("/dashboard/claim", data={"csrf_token": "tok", "key": "../x"}, follow_redirects=True)
t = html.unescape(r.data.decode())
check("customer flash translated (Spanish)", "Introduce una clave de licencia válida" in t)
check("Spanish date format", "Miembro desde el 01/09/2026" in t)
check("API error box label translated", "Servidor de licencias" in t)

print(f"\n{len(fails)} failed" if fails else "\nALL PASSED")
