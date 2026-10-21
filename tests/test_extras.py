"""Changelog, robots.txt, sitemap.xml, /healthz, Open Graph tags, admin customers page, 500 page, VAT number."""
import html, io, os, sqlite3, sys, tempfile, threading, time, zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import mock_license_server as mock  # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", 5103), mock.Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
dl = tempfile.mkdtemp()
os.environ.update({"DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "e.db"), "DOWNLOADS_DIR": dl,
                   "MYTHRAS_API_URL": "http://127.0.0.1:5103", "MYTHRAS_API_TOKEN": "test-token",
                   "ADMIN_DISCORD_IDS": "100000000000000001", "TOTP_ENC_KEY": Fernet.generate_key().decode(),
                   "LEGAL_CONTROLLER": "Mario Rossi, Roma, Italia", "LEGAL_VAT": "IT01234567890"})
with zipfile.ZipFile(os.path.join(dl, "Mythras-1.16.0-x.jar"), "w") as z:
    z.writestr("plugin.yml", "name: Mythras\nversion: 1.16.0\nmain: a.B\n")
Path(dl, "Mythras-1.16.0-x.json").write_text('{"changelog": "Cloud AI review menu.\\nFaster Anti-ESP."}', encoding="utf-8")
import app as m, admin_2fa  # noqa: E402


@m.app.get("/__boom")
def _boom():
    raise RuntimeError("test")
m.app.config["PROPAGATE_EXCEPTIONS"] = False


ADMIN, CUST = "100000000000000001", "100000000000000002"
con = sqlite3.connect(os.environ["DATABASE_PATH"]); con.row_factory = sqlite3.Row
con.execute("INSERT INTO users VALUES (?,'Admin',NULL,NULL,'2026-09-01T00:00:00+00:00','2026-10-20T00:00:00+00:00')", (ADMIN,))
con.execute("INSERT INTO users VALUES (?,'zX3noPvP',NULL,NULL,'2026-09-02T00:00:00+00:00','2026-10-21T00:00:00+00:00')", (CUST,))
con.commit(); admin_2fa.enroll(con, ADMIN, admin_2fa.new_secret(), 1, admin_2fa.new_recovery_codes()); con.close()
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
anon = m.app.test_client()
def admin_client():
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["discord_id"] = ADMIN; s["csrf_token"] = "tok"; s["mfa"] = {"uid": ADMIN, "at": int(time.time())}
    return c

t = html.unescape(anon.get("/changelog").data.decode())
check("public changelog lists versions and notes", "1.16.0" in t and "Cloud AI review menu." in t and "Latest" in t)
check("changelog has no download link", "/download" not in t.split("<main")[-1] if "<main" in t else "/download\"" not in t)
r = anon.get("/robots.txt")
check("robots.txt blocks private areas + sitemap", r.mimetype == "text/plain" and "Disallow: /admin" in r.text and "Disallow: /panel" in r.text and "Sitemap: http" in r.text)
r = anon.get("/sitemap.xml")
check("sitemap.xml lists public pages only", r.mimetype == "application/xml" and "/changelog" in r.text and "/docs" in r.text and "/admin" not in r.text)
r = anon.get("/healthz")
check("healthz 200 with all checks", r.status_code == 200 and r.get_json() == {"ok": True, "site": True, "database": True, "license_server": True})
os.environ["MYTHRAS_API_URL"] = "http://127.0.0.1:5999"
r = anon.get("/healthz")
check("healthz 503 when the license server is down", r.status_code == 503 and r.get_json()["license_server"] is False)
os.environ["MYTHRAS_API_URL"] = "http://127.0.0.1:5103"
check("healthz does not leak the token", "test-token" not in r.text)

home = anon.get("/").data.decode()
check("Open Graph + Twitter tags with absolute image", 'property="og:image" content="http' in home and "og.png" in home and 'name="twitter:card"' in home and 'rel="canonical"' in home)
check("og.png exists (1200x630)", (ROOT / "static" / "og.png").exists() and (ROOT / "static" / "og.png").read_bytes()[16:24] == (1200).to_bytes(4, "big") + (630).to_bytes(4, "big"))
check("footer: seller + VAT number", "Mario Rossi, Roma, Italia" in home and "IT01234567890" in home)
check("terms: seller + VAT", "IT01234567890" in html.unescape(anon.get("/terms").data.decode()))
check("footer links the changelog", 'href="/changelog"' in home)

ad = admin_client()
t = html.unescape(ad.get("/admin/customers").data.decode())
check("customers page: users, IDs, keys, create-license link", "zX3noPvP" in t and CUST in t and "MYTH-TEST-0001" in t and f"/admin/keys/new?owner={CUST}" in t)
t = html.unescape(ad.get("/admin/customers?q=zx3").data.decode())
check("customers search by name", "zX3noPvP" in t and "Admin</b>" not in t)
check("customers page is admin-only", m.app.test_client().get("/admin/customers").status_code == 403)
t = html.unescape(ad.get(f"/admin/keys/new?owner={CUST}").data.decode())
check("create license: owner prefilled + suggestions", f'value="{CUST}"' in t and 'list="customer-ids"' in t and f'<option value="{CUST}">zX3noPvP</option>' in t)

r = anon.get("/__boom")
check("500 page in the site style", r.status_code == 500 and "Something went wrong on our side" in r.data.decode() and "Mythras" in r.data.decode())

print(f"\n{len(fails)} failed" if fails else "\nALL PASSED"); srv.shutdown()
