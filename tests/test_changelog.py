"""Official changelog from the license server: page, badges, cache (also on file), home/shop box, customer banner, RSS,
and the license server 1.13 key changes (machines limit, keys without IP)."""
import html, os, sqlite3, sys, tempfile, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import mock_license_server as mock  # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", 5111), mock.Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
DBDIR = tempfile.mkdtemp()
os.environ.update({"DATABASE_PATH": os.path.join(DBDIR, "c.db"), "DOWNLOADS_DIR": tempfile.mkdtemp(),
                   "MYTHRAS_API_URL": "http://127.0.0.1:5111", "MYTHRAS_API_TOKEN": "test-token",
                   "ADMIN_DISCORD_IDS": "100000000000000001", "TOTP_ENC_KEY": Fernet.generate_key().decode()})
import app as m, changelog  # noqa: E402

CUST = "100000000000000002"
con = sqlite3.connect(os.environ["DATABASE_PATH"])
con.execute("INSERT INTO users VALUES (?,'Cust',NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)", (CUST,)); con.commit(); con.close()
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
def client(uid=None, lang="en"):
    c = m.app.test_client(); c.get(f"/lang/{lang}")
    if uid:
        with c.session_transaction() as s: s["discord_id"] = uid; s["csrf_token"] = "tok"
    return c
def text(r): return html.unescape(r.data.decode())

anon = client()
r = anon.get("/changelog"); raw = r.data.decode(); t = html.unescape(raw)
check("one card per version, title + Latest + anchor", "Mythras <span class=\"mono\">1.27.2</span>" in raw and 'id="v1.27.2"' in raw and t.count("Latest") >= 1)
check("date formatted for the language (en)", "October 28, 2026" in t)
check("prefix becomes a badge", '<span class="cl-badge cl-new">New</span>' in raw and 'cl-fixed">Fixed</span>' in raw
      and 'cl-improved">Improved</span>' in raw and 'cl-licenses">Licenses</span>' in raw and "Fixed: no more" not in t)
check("backticks -> <code>, everything else escaped (no HTML from the server)", "<code>/ac discord test</code>" in raw
      and "&lt;b&gt;open&lt;/b&gt;" in raw and "<b>open</b>" not in raw)
check("line without prefix kept as is", "Plain line without a prefix." in t)
check("last 10 shown, older behind Show older versions", "Show older versions" in t and raw.index("Show older versions") < raw.index('id="v1.20.1"'))
check("Mythras updates itself + Download button + RSS link", "Mythras updates itself" in t and 'rel="alternate" type="application/rss+xml"' in raw)
check("no notes from admin uploads any more", "Improvements and fixes." not in t)
it = client(lang="it"); ti = text(it.get("/changelog"))
check("Italian page: date and badges translated, lines stay English", "28 ottobre 2026" in ti and "Novità" in ti and "Corretto" in ti
      and "Anti-ESP uses less CPU." in ti and "Mostra le versioni precedenti" in ti)
check("German date", "28. Oktober 2026" in text(client(lang="de").get("/changelog")))

r = anon.get("/changelog.xml")
check("RSS: one item per version with the anchor link", r.mimetype == "application/rss+xml" and r.text.count("<item>") == len(mock.CHANGELOG)
      and "https://mythras.eu/changelog#v1.27.2" in r.text and "&lt;b&gt;open&lt;/b&gt;" in r.text)

home = text(anon.get("/"))
check("home: What's new box with 3 lines + Full changelog", "What's new in 1.27.2" in home and "Intave14Fast" in home and "Licenses" not in home.split("What's new in 1.27.2")[1].split("Full changelog")[0]
      and "Full changelog" in home)
check("shop: What's new box", "What's new in 1.27.2" in text(anon.get("/shop")))

# customer banner: only for a version from the last 7 days
from datetime import date, timedelta
mock.CHANGELOG[0]["date"] = date.today().isoformat(); changelog.reset_cache()
d = client(CUST).get("/dashboard").data.decode()
check("customer banner for a new version (closable, per version)", 'data-news-banner data-version="1.27.2"' in d and "installs itself at the next restart" in html.unescape(d))
mock.CHANGELOG[0]["date"] = (date.today() - timedelta(days=9)).isoformat(); changelog.reset_cache()
check("no banner for an older version", "data-news-banner" not in client(CUST).get("/dashboard").data.decode())

# cache: license server down -> last good answer, also after a restart (file)
changelog.reset_cache(); anon.get("/changelog")
check("cache saved to a file", (Path(DBDIR) / "changelog-cache.json").exists())
mock.CHANGELOG_UP[0] = False
changelog._mem["at"] = 0  # expired in memory
check("server down: last good answer from memory", "1.27.2" in text(anon.get("/changelog")))
changelog.reset_cache()  # like a website restart
check("server down after a restart: answer from the file", "1.27.2" in text(anon.get("/changelog")))
(Path(DBDIR) / "changelog-cache.json").unlink(); changelog.reset_cache()
t = text(anon.get("/changelog"))
check("nothing at all: friendly message", "The changelog is not available right now" in t)
check("home still works without a changelog", anon.get("/").status_code == 200 and "What's new in" not in text(anon.get("/")))
mock.CHANGELOG_UP[0] = True; changelog.reset_cache()

# admin upload has no release-notes field any more
with client("100000000000000001").session_transaction() as s: pass
ad = m.app.test_client()
with ad.session_transaction() as s:
    s["discord_id"] = "100000000000000001"; s["csrf_token"] = "tok"; s["mfa"] = {"uid": "100000000000000001", "at": int(time.time())}
con = sqlite3.connect(os.environ["DATABASE_PATH"])
con.execute("INSERT INTO users VALUES ('100000000000000001','Admin',NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)"); con.commit()
import admin_2fa
admin_2fa.enroll(con, "100000000000000001", admin_2fa.new_secret(), 1, admin_2fa.new_recovery_codes()); con.close()
t = text(ad.get("/admin/downloads"))
check("admin downloads: no changelog field", 'name="changelog"' not in t and "official CHANGELOG.md" in t)

# license server 1.13: machines limit, keys without IP work nowhere
d = text(client(CUST).get("/dashboard"))
check("customer sees machines + servers", "machines" in d)
check("key without IP: warning for the customer", "has no server IP yet" in d)
mock.keys["MYTH-TEST-0001-ABCD-EFGH"]["ips"] = ["1.2.3.4"]
d = text(client(CUST).get("/dashboard"))
check("allowed IP shown, warning gone for that key", "1.2.3.4" in d)
print(f"\n{len(fails)} failed" if fails else "\nALL PASSED"); srv.shutdown()
