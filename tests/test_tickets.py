import os, sys, tempfile, time, json, urllib.error
from cryptography.fernet import Fernet
from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]
os.environ.update({"DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "t.db"), "DOWNLOADS_DIR": tempfile.mkdtemp(),
    "MYTHRAS_API_URL": "http://127.0.0.1:1", "MYTHRAS_API_TOKEN": "test-token",
    "ADMIN_DISCORD_IDS": "100000000000000001", "TOTP_ENC_KEY": Fernet.generate_key().decode(),
    "TICKETS_BOT_TOKEN": "bot-token", "DISCORD_CLIENT_ID": "1", "DISCORD_CLIENT_SECRET": "x", "TICKETS_GUILD_ID": "900000000000000000"})
import app as m, admin_2fa, tickets
ADMIN, OPENER, STAFF, OTHER, BILLING = ("100000000000000001", "100000000000000002", "100000000000000003",
                                        "100000000000000004", "100000000000000005")
TOKEN, OPEN_TOKEN = "A" * 32, "B" * 32
con = tickets.connect()
for uid, name in ((ADMIN, "Admin"), (OPENER, "Cust"), (STAFF, "Helper"), (OTHER, "Random"), (BILLING, "Cashier")):
    con.execute("INSERT INTO users VALUES (?,?,NULL,NULL,'2026-09-01T00:00:00+00:00',NULL)", (uid, name))
admin_2fa.enroll(con, ADMIN, admin_2fa.new_secret(), 1, admin_2fa.new_recovery_codes())
messages = [
    {"id": "1", "author": "Cust", "author_id": OPENER, "avatar": "https://cdn.discordapp.com/a.png", "bot": False,
     "staff": False, "at": "2026-10-29T10:00:00+00:00", "edited": False, "content": "<script>alert(1)</script> hi",
     "embeds": [], "files": [{"name": "shot.png", "size": 10, "type": "image/png", "stored": "1-shot.png"},
                             {"name": "evil.html", "size": 10, "type": "text/html", "stored": "2-evil.html"},
                             {"name": "huge.zip", "size": 99999999, "type": "application/zip"}]},
    {"id": "2", "author": "Helper", "author_id": STAFF, "avatar": "javascript:alert(1)", "bot": False, "staff": True,
     "at": "2026-10-29T10:05:00+00:00", "edited": True, "content": "Here is your key",
     "embeds": [{"title": "Done", "description": "ok", "fields": [["Plan", "Pro"]]}], "files": []},
]
con.execute("INSERT INTO tickets (kind, status, channel_id, opener_id, opener_name, answers, closed_by, closed_name, "
            "close_reason, opened_at, closed_at, token, messages) VALUES ('billing','closed','1',?,'Cust',?,?,'Helper',"
            "'Pro delivered','2026-10-29T10:00:00+00:00','2026-10-29T11:00:00+00:00',?,?)",
            (OPENER, json.dumps([["What is it about?", "Buy Pro"]]), STAFF, TOKEN, json.dumps(messages)))
con.execute("INSERT INTO tickets (kind, status, channel_id, opener_id, opener_name, opened_at, token) "
            "VALUES ('general','open','2',?,'Cust','2026-10-29T10:00:00+00:00',?)", (OPENER, OPEN_TOKEN))
con.commit(); con.close()
folder = tickets.files_dir() / TOKEN
folder.mkdir(parents=True)
(folder / "1-shot.png").write_bytes(b"\x89PNG fake")
(folder / "2-evil.html").write_bytes(b"<script>alert(1)</script>")
(tickets.files_dir() / "secret.txt").write_text("nope")

# fake Discord: STAFF has the Support role, BILLING only a "Cashier" role, OTHER no role, unknown ids are not members
calls = []
def fake_discord(path):
    calls.append(path)
    if path.endswith("/roles"):
        return [{"id": "900000000000000000", "name": "@everyone", "permissions": "0"},
                {"id": "11", "name": "🎫 Support", "permissions": "0"},
                {"id": "12", "name": "Cashier", "permissions": "0"},
                {"id": "13", "name": "Boss", "permissions": "8"}]
    uid = path.rsplit("/", 1)[1]
    roles = {STAFF: ["11"], BILLING: ["12"], OTHER: [], ADMIN: []}
    if uid not in roles:
        raise urllib.error.HTTPError(path, 404, "Unknown Member", {}, None)
    return {"roles": roles[uid]}
m._discord_bot_get = fake_discord

fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)
def client(uid=None, mfa=False):
    c = m.app.test_client()
    if uid:
        with c.session_transaction() as s:
            s["discord_id"] = uid; s["csrf_token"] = "tok"
            if mfa: s["mfa"] = {"uid": uid, "at": int(time.time())}
    return c
url = f"/transcripts/{TOKEN}"

r = client().get(url)
check("logged out: sent to the Discord login, back to the transcript after", r.status_code == 302
      and "/login?next=/transcripts/" in r.headers["Location"])
check("logged out: files refused", client().get(f"{url}/files/1-shot.png").status_code == 403)
r = client(OPENER).get(url); page = r.data.decode()
check("opener can read their transcript", r.status_code == 200 and "Here is your key" in page and "Buy Pro" in page)
check("message content is escaped", "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page)
check("avatar only from Discord's CDN", "javascript:alert" not in page and "https://cdn.discordapp.com/a.png" in page)
check("not saved file shown as such", "huge.zip" in page and "not saved" in page)
check("no cache, no index", r.headers.get("Cache-Control") == "no-store" and "noindex" in r.headers.get("X-Robots-Tag", ""))
check("other member: 403", client(OTHER).get(url).status_code == 403)
check("Support role, billing ticket, TICKETS_BILLING_ROLES unset: allowed", client(STAFF).get(url).status_code == 200)
check("admin of the website: allowed", client(ADMIN).get(url).status_code == 200)
os.environ["TICKETS_BILLING_ROLES"] = "Cashier"; m._staff_cache.clear()
check("TICKETS_BILLING_ROLES set: Support can't read billing", client(STAFF).get(url).status_code == 403)
check("TICKETS_BILLING_ROLES set: Cashier can", client(BILLING).get(url).status_code == 200)
del os.environ["TICKETS_BILLING_ROLES"]; m._staff_cache.clear()
check("open ticket has no transcript yet", client(OPENER).get(f"/transcripts/{OPEN_TOKEN}").status_code == 404)
check("bad token: 404", client(OPENER).get("/transcripts/short").status_code == 404
      and client(OPENER).get("/transcripts/" + "C" * 32).status_code == 404)
client(STAFF).get(url); n = len(calls); client(STAFF).get(url); client(STAFF).get(url)
check("staff check cached", len(calls) == n)

c = client(OPENER)
r = c.get(f"{url}/files/1-shot.png")
check("image shown inline", r.status_code == 200 and r.mimetype == "image/png" and "attachment" not in r.headers.get("Content-Disposition", ""))
r = c.get(f"{url}/files/2-evil.html")
check("html only as a download", r.status_code == 200 and r.mimetype == "application/octet-stream"
      and "attachment" in r.headers.get("Content-Disposition", "") and r.headers.get("X-Content-Type-Options") == "nosniff")
check("unknown file: 404", c.get(f"{url}/files/3-other.png").status_code == 404)
check("path tricks: 404", c.get(f"{url}/files/..%2Fsecret.txt").status_code == 404
      and c.get(f"{url}/files/%2e%2e").status_code == 404)
check("other member can't download", client(OTHER).get(f"{url}/files/1-shot.png").status_code == 403)
check("attachment_path rejects traversal", tickets.attachment_path(TOKEN, "../secret.txt") is None
      and tickets.attachment_path("../x", "1-shot.png") is None)

check("admin list needs 2FA", client(ADMIN).get("/admin/tickets").status_code == 302)
check("admin list: staff of Discord is not an admin", client(STAFF, mfa=True).get("/admin/tickets").status_code == 403)
page = client(ADMIN, mfa=True).get("/admin/tickets").data.decode()
check("admin list shows tickets and link", "Pro delivered" in page and url in page and "Open" in page)
page = client(ADMIN, mfa=True).get("/admin/tickets?q=Random").data.decode()
check("admin search", "Pro delivered" not in page)
check("robots.txt hides transcripts", "Disallow: /transcripts/" in client().get("/robots.txt").data.decode())

c = client(); c.get("/login?next=/transcripts/" + TOKEN)
with c.session_transaction() as s:
    check("login remembers a local next page", s.get("login_next") == "/transcripts/" + TOKEN)
c = client(); c.get("/login?next=//evil.example")
with c.session_transaction() as s:
    check("login ignores an external next page", not s.get("login_next"))
print(f"\n{len(fails)} failed" if fails else "\nALL PASSED")
