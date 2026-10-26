import html, io, os, sqlite3, sys, tempfile, threading, time, zipfile, hashlib
from http.server import ThreadingHTTPServer
from cryptography.fernet import Fernet

from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import mock_license_server as mock
srv = ThreadingHTTPServer(("127.0.0.1", 5071), mock.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

DL = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "d.db"), "DOWNLOADS_DIR": DL,
    "MYTHRAS_API_URL": "http://127.0.0.1:5071", "MYTHRAS_API_TOKEN": "test-token",
    "ADMIN_DISCORD_IDS": "100000000000000001", "TOTP_ENC_KEY": Fernet.generate_key().decode(),
})
import app as m, admin_2fa, downloads

ADMIN, OWNER, NOKEY = "100000000000000001", "100000000000000002", "100000000000000003"
con = sqlite3.connect(os.environ["DATABASE_PATH"]); con.row_factory = sqlite3.Row
for uid, name in ((ADMIN, "Admin"), (OWNER, "Owner"), (NOKEY, "NoKey")):
    con.execute("INSERT INTO users VALUES (?, ?, NULL, NULL, '2026-09-01T00:00:00+00:00', NULL)", (uid, name))
con.commit()
admin_2fa.enroll(con, ADMIN, admin_2fa.new_secret(), 1, admin_2fa.new_recovery_codes())
con.close()

fails = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

def client(uid=None, mfa_age=0):
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["csrf_token"] = "tok"
        if uid:
            s["discord_id"] = uid
            if uid == ADMIN: s["mfa"] = {"uid": uid, "at": int(time.time()) - mfa_age}
    return c

def make_jar(name="Mythras", version="1.2.3", yml=True, pad=0):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if yml: z.writestr("plugin.yml", f"name: {name}\nversion: '{version}'\nmain: eu.mythras.Main\n")
        z.writestr("eu/mythras/Main.class", b"\xca\xfe\xba\xbe" + b"0" * pad)
    return buf.getvalue()

def upload(c, data, filename="mythras.jar", changelog=""):
    return c.post("/admin/downloads/upload", data={"csrf_token": "tok", "changelog": changelog, "jar": (io.BytesIO(data), filename)},
                  content_type="multipart/form-data", follow_redirects=True)

# access control
check("anonymous: admin downloads 403", client().get("/admin/downloads").status_code == 403)
check("customer: admin upload 403", client(OWNER).post("/admin/downloads/upload", data={"csrf_token": "tok"}).status_code == 403)
check("anonymous /download -> login redirect", client().get("/download").status_code == 302)

# nothing published yet
t = html.unescape(client(OWNER).get("/dashboard").data.decode())
check("no build yet -> no download panel", 'id="download"' not in t)

ad = client(ADMIN)
r = upload(ad, b"not a jar", "evil.exe"); check("non-jar extension rejected", "Upload a .jar file." in r.data.decode())
r = upload(ad, b"PK garbage", "x.jar"); check("corrupt jar rejected", "not a valid .jar" in r.data.decode())
r = upload(ad, make_jar(yml=False), "noyml.jar"); check("jar without plugin.yml rejected", "No plugin.yml" in r.data.decode())
check("rejected uploads leave no files", not [f for f in os.listdir(DL) if not f.startswith(".")] and not [f for f in os.listdir(DL) if f.endswith(".part")])

stale = client(ADMIN, mfa_age=16 * 60)
r = stale.post("/admin/downloads/upload", data={"csrf_token": "tok", "jar": (io.BytesIO(make_jar()), "m.jar")}, content_type="multipart/form-data")
check("publishing needs a fresh 2FA code", r.status_code == 302 and "fresh=1" in r.headers["Location"] and not os.listdir(DL))

jar1 = make_jar(version="1.2.3")
r = upload(ad, jar1, "Mythras-loader.jar", changelog="First public build")
t = html.unescape(r.data.decode())
check("valid jar published, version read from plugin.yml", "Published Mythras 1.2.3" in t and "Live" in t)
latest = downloads.latest()
check("sha256 + size computed", latest["sha256"] == hashlib.sha256(jar1).hexdigest() and latest["size"] == len(jar1))

# customer with an active key sees and gets the file
t = html.unescape(client(OWNER).get("/dashboard").data.decode())
check("license holder sees Download button with version + changelog", 'href="/download"' in t and "1.2.3" in t and "First public build" in t)
r = client(OWNER).get("/download")
check("license holder downloads the jar", r.status_code == 200 and r.data == jar1 and "Mythras-1.2.3.jar" in r.headers["Content-Disposition"])
check("download not cacheable + sha header", r.headers.get("Cache-Control") == "no-store" and r.headers.get("X-Content-SHA256") == hashlib.sha256(jar1).hexdigest())
r.close()

# customer without license
t = html.unescape(client(NOKEY).get("/dashboard").data.decode())
check("no license -> locked panel, no link", 'href="/download"' not in t and "unlocks as soon as you have an active license" in t)
r = client(NOKEY).get("/download", follow_redirects=True)
check("no license -> download refused", "You need an active license" in r.data.decode() and r.headers.get("Content-Disposition") is None)

# revoked key -> no download
mock.keys["MYTH-TEST-0001-ABCD-EFGH"]["status"] = "revoked"
r = client(OWNER).get("/download")
check("revoked license -> refused", r.status_code == 302)
mock.keys["MYTH-TEST-0001-ABCD-EFGH"]["status"] = "active"

# newer upload replaces it automatically
time.sleep(1.1)
jar2 = make_jar(version="1.3.0")
upload(ad, jar2, "loader.jar")
r = client(OWNER).get("/download")
check("new build served automatically", r.data == jar2 and "Mythras-1.3.0.jar" in r.headers["Content-Disposition"])
r.close()

# scp-style: copy a jar straight into the folder
time.sleep(1.1)
jar3 = make_jar(version="1.4.0")
open(os.path.join(DL, "Mythras-1.4.0-manual.jar"), "wb").write(jar3)
open(os.path.join(DL, "half-copied.jar.part"), "wb").write(b"PK")
r = client(OWNER).get("/download")
check("jar copied into the folder is picked up (partial files ignored)", r.data == jar3)
r.close()

# keep only 5 builds
for v in ("2.0", "2.1", "2.2", "2.3", "2.4"):
    time.sleep(1.05); upload(ad, make_jar(version=v), "x.jar")
jars = [f for f in os.listdir(DL) if f.endswith(".jar")]
check("only the last 5 builds kept", len(jars) == 5 and downloads.latest()["version"] == "2.4")

# MythrasProxy: its own builds, for active Pro keys only
def make_proxy(version="1.0.0", kind="velocity"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if kind == "velocity":
            z.writestr("velocity-plugin.json", '{"id": "mythras-proxy", "name": "MythrasProxy", "version": "%s"}' % version)
        elif kind == "bungee":
            z.writestr("bungee.yml", f"name: MythrasProxy\nversion: {version}\nmain: eu.mythras.proxy.Bungee\n")
        z.writestr("eu/mythras/proxy/Main.class", b"\xca\xfe\xba\xbe")
    return buf.getvalue()
def upload_proxy(c, data, changelog=""):
    return c.post("/admin/downloads/upload", data={"csrf_token": "tok", "product": "proxy", "changelog": changelog,
                  "jar": (io.BytesIO(data), "proxy.jar")}, content_type="multipart/form-data", follow_redirects=True)
plugin_before = downloads.latest()["version"]
t = html.unescape(client(OWNER).get("/dashboard").data.decode())
check("no proxy build -> no proxy button", "/download/proxy" not in t)
check("proxy jar without a version refused", "No velocity-plugin.json or bungee.yml" in html.unescape(upload_proxy(ad, make_proxy(kind="none")).data.decode()))
t = html.unescape(upload_proxy(ad, make_proxy("1.0.0"), "Network bans").data.decode())
check("proxy published (version from velocity-plugin.json), listed apart", "Published MythrasProxy 1.0.0" in t and "Builds · MythrasProxy" in t)
check("proxy upload does not change the plugin build", downloads.latest()["version"] == plugin_before and downloads.latest("proxy")["version"] == "1.0.0")
time.sleep(1.05)
upload_proxy(ad, make_proxy("1.1.0", kind="bungee"))
check("bungee.yml version read too", downloads.latest("proxy")["version"] == "1.1.0")
t = html.unescape(client(OWNER).get("/dashboard").data.decode())
check("Pro customer sees Download MythrasProxy", 'href="/download/proxy"' in t and "1.1.0" in t)
r = client(OWNER).get("/download/proxy")
check("Pro customer downloads the proxy jar", r.status_code == 200 and "MythrasProxy-1.1.0.jar" in r.headers["Content-Disposition"]
      and r.headers.get("X-Content-SHA256") == downloads.latest("proxy")["sha256"])
r.close()
mock.keys["MYTH-TEST-0001-ABCD-EFGH"]["plan"] = "free"
check("Free key -> no proxy button, download refused", 'href="/download/proxy"' not in html.unescape(client(OWNER).get("/dashboard").data.decode())
      and "MythrasProxy needs an active Pro license" in client(OWNER).get("/download/proxy", follow_redirects=True).data.decode())
mock.keys["MYTH-TEST-0001-ABCD-EFGH"]["plan"] = "pro"
check("no license -> proxy refused", client(NOKEY).get("/download/proxy").status_code == 302 and client().get("/download/proxy").status_code == 302)
r = ad.post("/admin/downloads/upload", data={"csrf_token": "tok", "product": "nope", "jar": (io.BytesIO(make_proxy()), "p.jar")},
            content_type="multipart/form-data", follow_redirects=True)
check("unknown product refused", "Choose what you are publishing." in html.unescape(r.data.decode()))

# size limits
old = downloads.MAX_UPLOAD
downloads.MAX_UPLOAD = 2000
r = ad.post("/admin/downloads/upload", data={"csrf_token": "tok", "jar": (io.BytesIO(make_jar(pad=5000)), "big.jar")}, content_type="multipart/form-data")
check("upload over the limit -> 413", r.status_code == 413)
downloads.MAX_UPLOAD = old
r = client(OWNER).post("/dashboard/claim", data={"csrf_token": "tok", "key": "A" * 70000})
check("other routes keep the 64 KB limit", r.status_code == 413)

print(f"\n{len(fails)} failed" if fails else "\nALL PASSED")
srv.shutdown()
