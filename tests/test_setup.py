"""Setup links: single-use invites, plan questions, numeric IP, config sent to the license server, IP lock,
question editor and the Discord bot API."""
import html, json, os, re, sqlite3, sys, tempfile, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools" / "dev")]
import mock_license_server as mock  # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", 5105), mock.Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update({"DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "s.db"), "DOWNLOADS_DIR": tempfile.mkdtemp(),
                   "MYTHRAS_API_URL": "http://127.0.0.1:5105", "MYTHRAS_API_TOKEN": "test-token",
                   "ADMIN_DISCORD_IDS": "100000000000000001", "TOTP_ENC_KEY": Fernet.generate_key().decode(),
                   "BOT_API_TOKEN": "bot-secret-123"})
import app as m, admin_2fa, setup_forms  # noqa: E402

ADMIN, CUST, OTHER = "100000000000000001", "100000000000000002", "100000000000000003"
con = sqlite3.connect(os.environ["DATABASE_PATH"])
for uid, n in ((ADMIN, "Admin"), (CUST, "Cust"), (OTHER, "Other")):
    con.execute("INSERT INTO users VALUES (?,?,NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)", (uid, n))
con.commit(); admin_2fa.enroll(con, ADMIN, admin_2fa.new_secret(), 1, admin_2fa.new_recovery_codes()); con.close()
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
def client(uid, mfa=False, lang="en"):
    c = m.app.test_client(); c.get(f"/lang/{lang}")
    with c.session_transaction() as s:
        s["discord_id"] = uid; s["csrf_token"] = "tok"
        if mfa: s["mfa"] = {"uid": uid, "at": int(time.time())}
    return c
def db():
    c = sqlite3.connect(os.environ["DATABASE_PATH"]); c.row_factory = sqlite3.Row; return c
def text(r): return html.unescape(r.data.decode())
def form_for(plan, addons=(), **over):
    data = {"csrf_token": "tok"}
    for q in setup_forms.questions_for(db(), plan, addons):
        data[q["config_key"]] = q["default_value"]
    data["info.server_ip"] = "123.45.67.89"
    data.update(over)
    return data

K1 = "MYTH-TEST-0001-ABCD-EFGH"   # pro key owned by CUST in the mock
check("default questions seeded", len(setup_forms.all_questions(db())) >= 39)
check("ban wave is not among the questions", not any("ban" in q["config_key"] and "wave" in q["config_key"] for q in setup_forms.all_questions(db())))

# ---------- admin creates a link ----------
ad = client(ADMIN, mfa=True)
check("setup links page is admin-only", client(CUST).get("/admin/invites").status_code == 403)
r = ad.post("/admin/invites", data={"csrf_token": "tok", "plan": "pro", "addons": ["antixray"], "key": K1, "discord_id": CUST, "hours": "48", "note": "Ticket 7"}, follow_redirects=True)
t = text(r)
link = re.search(r'data-key="(http[^"]+/setup/[^"]+)"', t)
check("link created and shown once", link is not None and "Copy it now" in t)
link = link.group(1); path = link.split("localhost", 1)[-1]
check("token stored only as a hash", path.rsplit("/", 1)[-1] not in json.dumps([dict(r) for r in db().execute("SELECT * FROM setup_invites")]))
check("link not shown again", "/setup/" not in text(ad.get("/admin/invites")).split("Answers")[0] or path not in text(ad.get("/admin/invites")))

# ---------- customer opens it ----------
r = m.app.test_client().get(path)
check("not signed in -> login, coming back here", r.status_code in (302, 303) and "/login?next=" in r.headers["Location"] and "setup" in r.headers["Location"])
check("someone else cannot use a personal link", client(OTHER).get(path).status_code == 403)
cu = client(CUST)
t = text(cu.get(path))
check("questions of Free + Pro + the Anti-XRay add-on, not Anti-ESP", "Turn on Anti-XRay by default?" in t and "It can cause a little lag." in t and "Language of the plugin messages" in t and "Turn on Mythras Cloud AI?" in t and "Anti-ESP: hide players" not in t)
check("plan and add-on badges on the setup page", 'badge-addon">+ Anti-XRay' in t)
check("setup link stores the add-ons", db().execute("SELECT addons FROM setup_invites").fetchone()["addons"] == '["antixray"]')
check("IP field says numeric", "Numbers only, e.g. 123.45.67.89" in t and 'inputmode="decimal"' in t)
check("conditional questions marked", 'data-show-if="bedrock.enabled=yes"' in t and 'data-show-if="mitigations.anti-xray.enabled=yes"' in t)

r = cu.post(path, data=form_for("pro", ["antixray"], **{"info.server_ip": "play.myserver.com"}))
check("domain instead of IP refused", r.status_code == 200 and "Enter a numeric IP address" in text(r))
r = cu.post(path, data=form_for("pro", ["antixray"], **{"info.server_ip": ""}))
check("empty IP refused", "Enter the numeric IP address of your server." in text(r))
r = cu.post(path, data=form_for("pro", ["antixray"], **{"discord.webhook-url": "https://evil.example/x"}))
check("bad webhook refused", "Paste a Discord webhook URL" in text(r))
r = cu.post(path, data=form_for("pro", ["antixray"], **{"replay.keep-days": "30"}))
check("number out of range refused", "Out of range." in text(r))
check("invalid answers do not use the link", db().execute("SELECT used_at FROM setup_invites").fetchone()["used_at"] is None)

answers = form_for("pro", ["antixray"], **{"mitigations.anti-xray.enabled": "yes", "mitigations.anti-xray.max-y": "40", "bedrock.enabled": "no",
                             "bedrock.reach-extra": "1.0", "language": "it", "exemptions.worlds": "minigames, event",
                             "info.server_ip": " 2001:db8::1 "})
r = cu.post(path, data=answers)
t = text(r)
check("valid answers -> thank you, configuration ready", r.status_code == 200 and "Thanks, your answers are saved" in t and "applies it the next time" in t)
cfg = mock.keys[K1]["config"]
check("config sent to the license server", cfg.get("language") == "it" and cfg.get("mitigations.anti-xray.enabled") is True and cfg.get("mitigations.anti-xray.max-y") == 40 and cfg.get("exemptions.worlds") == ["minigames", "event"])
check("hidden conditional answer not sent (Bedrock off)", "bedrock.reach-extra" not in cfg and cfg.get("bedrock.enabled") is False)
check("info answers stay on the website", not any(k.startswith("info.") for k in cfg))
check("cooldown choice sent as a number", cfg.get("alerts.cooldown-ms") == 1000)
row = db().execute("SELECT * FROM setup_answers").fetchone()
check("answers stored with normalised IP", row["server_ip"] == "2001:db8::1" and row["applied_at"])
check("IP lock NOT set automatically", mock.keys[K1].get("ips", []) == [])
check("link works only once", cu.get(path).status_code == 410 and cu.post(path, data=answers).status_code == 410)

# ---------- admin: answers, IP lock, key page ----------
t = text(ad.get(f"/admin/setups/{row['id']}"))
check("admin sees the answers", "Turn on Anti-XRay by default?" in t and "2001:db8::1" in t and "only for you" in t)
r = client(ADMIN, mfa=False).post(f"/admin/setups/{row['id']}/lock-ip", data={"csrf_token": "tok"})
check("IP lock needs admin 2FA", r.status_code in (302, 303) and mock.keys[K1].get("ips", []) == [])
ad.post(f"/admin/setups/{row['id']}/lock-ip", data={"csrf_token": "tok"})
check("one click locks the key to the answered IP", mock.keys[K1]["ips"] == ["2001:db8::1"])
t = text(ad.get(f"/admin/keys/{K1}"))
check("key page shows the allowed IP + setup link", "2001:db8::1" in t and "Allowed IP" in t and "Setup answers:" in t)
r = ad.post(f"/admin/keys/{K1}/ips", data={"csrf_token": "tok", "ips": "1.2.3.4\n5.6.7.8"}, follow_redirects=True)
check("IPs set by hand on the key page", mock.keys[K1]["ips"] == ["1.2.3.4", "5.6.7.8"] and "Allowed IP addresses updated." in text(r))
r = ad.post(f"/admin/keys/{K1}/ips", data={"csrf_token": "tok", "ips": "myserver.com"}, follow_redirects=True)
check("domain refused on the key page", "Enter numeric IP addresses only" in text(r) and mock.keys[K1]["ips"] == ["1.2.3.4", "5.6.7.8"])
ad.post(f"/admin/keys/{K1}/ips", data={"csrf_token": "tok", "ips": ""})
check("empty = any IP", mock.keys[K1]["ips"] == [])

# a link without a key: answers wait, admin applies them later
r = ad.post("/admin/invites", data={"csrf_token": "tok", "plan": "free", "key": "", "hours": "1"}, follow_redirects=True)
check("Free link without add-ons refused", "A Free link needs at least one add-on." in text(r))
r = ad.post("/admin/invites", data={"csrf_token": "tok", "plan": "free", "addons": ["antiesp"], "key": "", "hours": "1"}, follow_redirects=True)
path2 = "/setup/" + re.search(r'data-key="http[^"]+/setup/([^"]+)"', text(r)).group(1)
t = text(client(OTHER).get(path2))
check("Free + Anti-ESP link: Anti-ESP questions, no Pro or Anti-XRay ones", "Anti-ESP: hide players" in t and "Anti-StorageESP" in t and "Turn on Mythras Cloud AI?" not in t and "Turn on Anti-XRay by default?" not in t)
r = client(OTHER).post(path2, data=form_for("free", ["antiesp"]))
check("without a key: saved, not applied yet", "We prepare your configuration" in text(r))
row2 = db().execute("SELECT * FROM setup_answers ORDER BY id DESC").fetchone()
r = ad.post(f"/admin/setups/{row2['id']}/apply", data={"csrf_token": "tok", "key": "MYTH-TEST-0003-ABCD-EFGH"}, follow_redirects=True)
cfg3 = mock.keys["MYTH-TEST-0003-ABCD-EFGH"]["config"]
check("admin applies later to a chosen key", "Configuration sent" in text(r) and cfg3.get("mitigations.anti-esp.enabled") is True and "ai.cloud.enabled" not in cfg3)

# revoke + expiry
r = ad.post("/admin/invites", data={"csrf_token": "tok", "plan": "pro", "hours": "5"}, follow_redirects=True)
path3 = "/setup/" + re.search(r'data-key="http[^"]+/setup/([^"]+)"', text(r)).group(1)
h3 = db().execute("SELECT token_hash FROM setup_invites ORDER BY created_at DESC, rowid DESC").fetchone()["token_hash"]
ad.post(f"/admin/invites/{h3}/revoke", data={"csrf_token": "tok"})
check("revoked link stops working", client(CUST).get(path3).status_code == 410)
con = db(); con.execute("UPDATE setup_invites SET expires_at = '2000-01-01T00:00:00+00:00'"); con.commit()
check("unknown token -> not valid", client(CUST).get("/setup/nope").status_code == 410)

# ---------- question editor ----------
check("questions page", "Turn on Anti-XRay by default?" in text(ad.get("/admin/questions")))
r = ad.post("/admin/questions/new", data={"csrf_token": "tok", "label": "Do you want auto-updates?", "plan": "free", "kind": "yesno",
                                          "config_key": "update-checker.enabled", "default_value": "yes", "position": "5", "enabled": "1"}, follow_redirects=True)
check("new question saved", "Question saved." in text(r) and any(q["config_key"] == "update-checker.enabled" for q in setup_forms.all_questions(db())))
r = ad.post("/admin/questions/new", data={"csrf_token": "tok", "label": "Bad", "plan": "free", "kind": "choice", "config_key": "x.y", "options": "only-one", "enabled": "1"})
check("choice with one option refused", "A choice needs at least 2 options" in text(r))
r = ad.post("/admin/questions/new", data={"csrf_token": "tok", "label": "Dup", "plan": "free", "kind": "yesno", "config_key": "language", "enabled": "1"})
check("duplicate config key refused", "already uses this config key" in text(r))
qid = next(q["id"] for q in setup_forms.all_questions(db()) if q["config_key"] == "update-checker.enabled")
ad.post(f"/admin/questions/{qid}/delete", data={"csrf_token": "tok"})
check("question deleted", not any(q["config_key"] == "update-checker.enabled" for q in setup_forms.all_questions(db())))
ad.post("/admin/questions/reset", data={"csrf_token": "tok"})
check("defaults restored", len(setup_forms.all_questions(db())) == len(setup_forms.DEFAULT_QUESTIONS))

# ---------- Discord bot API ----------
bot = m.app.test_client()
check("bot API needs the token", bot.post("/api/bot/invites", json={"plan": "pro"}).status_code == 401)
check("bot API wrong token", bot.post("/api/bot/invites", json={"plan": "pro"}, headers={"Authorization": "Bearer nope"}).status_code == 401)
r = bot.post("/api/bot/invites", json={"plan": "pro", "discord_id": CUST, "key": K1, "note": "ticket 9"}, headers={"Authorization": "Bearer bot-secret-123"})
check("bot creates a link (no CSRF needed)", r.status_code == 200 and r.get_json()["ok"] and "/setup/" in r.get_json()["url"])
check("bot link works for that user", client(CUST).get("/setup/" + r.get_json()["url"].rsplit("/", 1)[-1]).status_code == 200)
r2 = bot.post("/api/bot/invites", json={"plan": "free", "addons": ["antixray", "antiesp"]}, headers={"Authorization": "Bearer bot-secret-123"})
t2 = text(client(OTHER).get("/setup/" + r2.get_json()["url"].rsplit("/", 1)[-1]))
check("bot link with add-ons", r2.status_code == 200 and "Turn on Anti-XRay by default?" in t2 and "Anti-ESP: hide players" in t2 and "Turn on Mythras Cloud AI?" not in t2)
check("bot bad add-on -> 400", bot.post("/api/bot/invites", json={"plan": "pro", "addons": ["fly"]}, headers={"Authorization": "Bearer bot-secret-123"}).status_code == 400)
check("bot bad plan -> 400", bot.post("/api/bot/invites", json={"plan": "gold"}, headers={"Authorization": "Bearer bot-secret-123"}).status_code == 400)
# ---------- add-ons on keys ----------
r = ad.post("/admin/keys/new", data={"csrf_token": "tok", "plan": "pro", "addons": ["antiesp"], "days": "30"}, follow_redirects=True)
new = [k for k in mock.keys.values() if k.get("addons") == ["antiesp"] and k["plan"] == "pro"]
check("key created with an add-on", len(new) == 1 and "+ Anti-ESP" in text(r))
nk = new[0]["key"]
r = client(ADMIN, mfa=False).post(f"/admin/keys/{nk}/addons", data={"csrf_token": "tok", "addons": ["antixray"]})
check("changing add-ons needs a recent 2FA code", mock.keys[nk]["addons"] == ["antiesp"])
r = ad.post(f"/admin/keys/{nk}/addons", data={"csrf_token": "tok", "addons": ["antixray", "antiesp"]}, follow_redirects=True)
check("add-ons changed on the key page", mock.keys[nk]["addons"] == ["antiesp", "antixray"] and "Add-ons updated." in text(r))
r = ad.post(f"/admin/keys/{nk}/addons", data={"csrf_token": "tok", "addons": ["fly"]}, follow_redirects=True)
check("unknown add-on refused", "Choose valid add-ons." in text(r) and mock.keys[nk]["addons"] == ["antiesp", "antixray"])
ad.post(f"/admin/keys/{nk}/addons", data={"csrf_token": "tok"})
check("all add-ons removed", mock.keys[nk]["addons"] == [])
t = text(ad.get("/admin/keys"))
check("keys list shows add-on badges", "+ Anti-XRay" in t)
t = text(client(CUST).get("/dashboard"))
check("dashboard shows add-on cards", "Add-on for Free or Pro" in t and "€5.99" in t and "€19.99" in t and "Enterprise" not in t)
t = text(m.app.test_client().get("/"))
check("homepage: Free + Pro + 2 add-ons, no old plans", "€19.99" in t and t.count("€5.99") >= 2 and "Enterprise" not in t and "Base" not in t and "lifetime" not in t.lower())
check("add-ons are one-time and forever", t.count("one-time, forever") == 2 and "Pay once, yours forever" in t)
ad.post("/admin/keys/new", data={"csrf_token": "tok", "plan": "free", "addons": ["antixray"], "days": "30"})
fk = [k for k in mock.keys.values() if k["plan"] == "free" and k.get("addons") == ["antixray"] and k["note"] == ""]
check("a Free key for an add-on never expires", len(fk) == 1 and fk[0]["expires"] == 0)

check("robots blocks setup links", "Disallow: /setup/" in m.app.test_client().get("/robots.txt").text)

# Italian
t = text(client(CUST, lang="it").get("/setup/" + r.get_json()["url"].rsplit("/", 1)[-1]) if False else client(CUST, lang="it").get("/setup/" + bot.post("/api/bot/invites", json={"plan": "pro", "addons": ["antixray"]}, headers={"Authorization": "Bearer bot-secret-123"}).get_json()["url"].rsplit("/", 1)[-1]))
check("Italian setup page", "Configura Mythras" in t and "Attivare l'Anti-XRay di base?" in t)
# login comes back to the setup link, never to another site
m.DISCORD_CLIENT_ID, m.DISCORD_CLIENT_SECRET = "cid", "secret"
c = m.app.test_client()
r = c.get("/login?next=/setup/abc123")
with c.session_transaction() as s_:
    back = s_.get("login_next")
check("login remembers the setup link", r.status_code in (302, 303) and "discord.com" in r.headers["Location"] and back == "/setup/abc123")
c.get("/login?next=//evil.example/x")
with c.session_transaction() as s_:
    check("login never returns to another site", s_.get("login_next") == "")

print(f"\n{len(fails)} failed" if fails else "\nALL PASSED"); srv.shutdown()
