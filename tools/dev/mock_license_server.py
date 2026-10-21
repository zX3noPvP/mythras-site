"""In-memory mock of the Mythras license server (follows API-SITO.md). LOCAL DEMO/TESTS ONLY."""
import json
import re
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "test-token"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5055
DAY = 86_400_000
PLANS = {"base": 1, "pro": 3, "enterprise": 0}
RESET_HOURS = 24
now = lambda: int(time.time() * 1000)

keys = {}
events = []
seq = [0]


def new_key_id():
    seq[0] += 1
    return f"MYTH-TEST-{seq[0]:04d}-ABCD-EFGH"


def log(actor, action, label, key, detail=""):
    events.insert(0, {"time": now(), "actor": actor or "website", "action": action, "label": label, "key": key, "detail": detail})


def view(k, admin=True, detail=False):
    status = k["status"]
    if status == "active" and k["expires"] and k["expires"] < now():
        status = "expired"
    max_s = k["custom"] if k["custom"] != -1 else PLANS[k["plan"]]
    out = {
        "key": k["key"], "plan": k["plan"], "status": status, "note": k["note"] if admin else "",
        "created": k["created"], "expires": k["expires"], "owner": k["owner"], "maxServers": max_s,
        "customMaxServers": k["custom"], "activeServers": sum(1 for s in k["servers"] if s["active"]),
        "nextReset": k["nextReset"],
    }
    if detail:
        out["servers"] = [dict(s) if admin else {**{x: s[x] for x in s if x != "ip"}, "server": s["server"][:8] + "…"} for s in k["servers"]]
    return out


def seed():
    for plan, owner, note, servers in [
        ("pro", "100000000000000002", "Order #42", 2),
        ("base", None, "Unsold", 0),
        ("enterprise", "100000000000000001", "Network", 4),
    ]:
        kid = new_key_id()
        keys[kid] = {"key": kid, "plan": plan, "status": "active", "note": note, "created": now() - 20 * DAY,
                     "expires": 0 if plan != "base" else now() + 30 * DAY, "owner": owner, "custom": -1,
                     "nextReset": 0, "servers": [
                         {"server": f"edc5b4e1-{i:04d}-4c2b-9f1a-{kid[-4:]}", "firstSeen": now() - 10 * DAY,
                          "lastSeen": now() - i * 3_600_000, "active": i < 2, "version": "1.0.0", "ip": f"10.0.0.{i + 1}"}
                         for i in range(servers)]}
        log("website: seed", "create", "Key created", kid, plan)
    keys["MYTH-TEST-0001-ABCD-EFGH"]["nextReset"] = now() + 5 * 3_600_000


# ---------- customer panel (servers, alerts, punishments, replays) ----------
panel_servers = []   # Server objects
panel_events = []    # Event objects, newest first
CHECKS = ["Reach", "KillAura", "Speed", "Flight", "Timer", "AutoClicker", "Scaffold", "AI"]
PLAYERS = [("Notch", "069a79f4-44e9-4726-a5be-fca90e38aaf5"), ("jeb_", "853c80ef-3c37-49fd-aa49-938b674adae6"),
           ("Dinnerbone", "61699b2e-d327-4a01-9f1e-0ea8c3f06bc6"), ("Grumm", "e6b5c088-0680-44df-9e1b-9bf11792291b"),
           ("xX_Pvp_Xx", "4566e69f-c907-48ee-8d71-d7ba5aa00d20")]


def seed_panel():
    import random
    rnd = random.Random(7)
    t0 = now()
    for kid, name, players, online in [("MYTH-TEST-0001-ABCD-EFGH", "Survival", 37, True),
                                       ("MYTH-TEST-0001-ABCD-EFGH", "Lobby", 12, False),
                                       ("MYTH-TEST-0003-ABCD-EFGH", "Network-1", 210, True)]:
        sid = f"srv-{len(panel_servers) + 1:04d}-{kid[-4:]}"
        panel_servers.append({"key": kid, "server": sid, "name": name, "online": online, "players": players if online else 0,
                              "maxPlayers": 100 if players < 100 else 500, "tps": 19.8 if online else -1,
                              "mythras": "1.16.0", "minecraft": "1.21.4", "software": "Paper",
                              "lastSeen": t0 - (5_000 if online else 3 * 3_600_000)})
        for i in range(160):
            pl, uuid = rnd.choice(PLAYERS)
            kind = rnd.choices(["alert", "punish", "replay"], [0.8, 0.12, 0.08])[0]
            check = rnd.choice(CHECKS)
            ev = {"key": kid, "server": sid, "time": t0 - rnd.randint(30_000, 25 * DAY), "type": kind, "player": pl,
                  "uuid": uuid, "check": check, "vl": round(rnd.uniform(1, 40), 1),
                  "info": f"reach={rnd.uniform(3.1, 5):.2f} max=3.25 ping={rnd.randint(0, 120)}"}
            if kind == "punish":
                ev["info"] = "ban wave" if rnd.random() < 0.3 else "[kick] Unfair advantage"
            if kind == "replay":
                ev["info"] = f"#{rnd.randint(1, 300)}"
                ev["check"] = check + ", Speed"
                ev["vl"] = rnd.randint(10, 35)
            panel_events.append(ev)
    panel_events.sort(key=lambda e: e["time"], reverse=True)


def panel_answer(self, path, q, owner=None):
    """path = ['servers'] | ['stats'] | ['events'] | ['players', name]; owner None = admin (all keys)."""
    allowed = {kid for kid, k in keys.items() if owner is None or k["owner"] == owner}
    if q.get("key"):
        if q["key"] not in allowed:
            return self.fail("This key is not yours" if owner else "Unknown key")
        allowed = {q["key"]}
    days = int(q.get("days") or 7)
    since = now() - days * DAY
    evs = [e for e in panel_events if e["key"] in allowed]
    if path == ["servers"]:
        return self.ok(servers=[s for s in panel_servers if s["key"] in allowed])
    if path == ["stats"]:
        evs = [e for e in evs if e["time"] >= since]
        checks, daily, top = {}, {}, {}
        for e in evs:
            if e["type"] != "replay":
                c = checks.setdefault((e["type"], e["check"]), {"type": e["type"], "check": e["check"], "count": 0, "players": set()})
                c["count"] += 1
                c["players"].add(e["player"])
                day = e["time"] - e["time"] % DAY
                daily[(day, e["type"])] = daily.get((day, e["type"]), 0) + 1
            if e["type"] == "alert":
                t = top.setdefault(e["player"], {"player": e["player"], "alerts": 0, "last": 0})
                t["alerts"] += 1
                t["last"] = max(t["last"], e["time"])
        return self.ok(days=days, alerts=sum(e["type"] == "alert" for e in evs),
                       punishments=sum(e["type"] == "punish" for e in evs), replays=sum(e["type"] == "replay" for e in evs),
                       checks=sorted([{**c, "players": len(c["players"])} for c in checks.values()], key=lambda c: -c["count"]),
                       daily=[{"day": d, "type": t, "count": n} for (d, t), n in sorted(daily.items())],
                       topPlayers=sorted(top.values(), key=lambda t: -t["alerts"])[:10])
    if path == ["events"]:
        if q.get("days"):
            evs = [e for e in evs if e["time"] >= since]
        for f in ("server", "type", "check"):
            if q.get(f):
                evs = [e for e in evs if e[f] == q[f]]
        if q.get("player"):
            evs = [e for e in evs if q["player"].lower() in e["player"].lower()]
        if q.get("before"):
            evs = [e for e in evs if e["time"] < int(q["before"])]
        limit = min(int(q.get("limit") or 100), 500)
        page = evs[:limit]
        return self.ok(events=page, next=page[-1]["time"] if len(evs) > limit else 0)
    if len(path) == 2 and path[0] == "players":
        evs = [e for e in evs if e["player"].lower() == path[1].lower() and e["time"] >= since]
        checks = {}
        for e in evs:
            c = checks.setdefault((e["type"], e["check"]), {"type": e["type"], "check": e["check"], "count": 0, "last": 0})
            c["count"] += 1
            c["last"] = max(c["last"], e["time"])
        return self.ok(player=evs[0]["player"] if evs else path[1], uuid=evs[0]["uuid"] if evs else "", days=days,
                       checks=sorted(checks.values(), key=lambda c: -c["count"]), events=evs[:100])
    return self.fail("Unknown endpoint", 404)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def ok(self, **kw):
        self.send(200, {"ok": True, **kw})

    def fail(self, msg, code=400):
        self.send(code, {"ok": False, "error": msg})

    def handle_any(self, method):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self.fail("Bad token", 401)
        parsed = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        path = [urllib.parse.unquote(p) for p in parsed.path.strip("/").split("/")]
        body = {}
        if method == "POST":
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.loads(raw or b"{}")
        actor = body.get("actor") or q.get("actor")

        if len(path) >= 3 and path[:2] == ["api", "panel"] and method == "GET":
            return panel_answer(self, path[2:], q)
        if len(path) >= 5 and path[:2] == ["api", "users"] and path[3] == "panel" and method == "GET":
            return panel_answer(self, path[4:], q, owner=path[2])
        if path == ["api", "ai", "stats"]:
            t = now()
            return self.ok(enabled=True,
                           models=[{"features": 20, "created": t - 3_600_000, "servers": 3, "samples": 1800, "cheatLabels": 900,
                                    "legitLabels": 900, "accuracy": 0.97, "kind": "network"},
                                   {"features": 12, "created": t - 9 * DAY, "servers": 2, "samples": 700, "cheatLabels": 300,
                                    "legitLabels": 400, "accuracy": 0.91, "kind": "logistic"}],
                           history=[{"time": t - i * 3_600_000, "features": 20, "published": i % 7 != 3, "kind": "network",
                                     "accuracy": round(0.9 + (0.07 * (24 - i) / 24), 3), "labels": 1800 - i * 20,
                                     "cheat": 900, "legit": 900, "keys": 3, "windows": 2400,
                                     "excluded": "MYTH-TEST-0002-ABCD-EFGH" if i == 3 else "",
                                     "message": "published" if i % 7 != 3 else "accuracy below the current model"}
                                    for i in range(min(int(q.get("runs") or 50), 24))],
                           keys=[{"key": "MYTH-TEST-0001-ABCD-EFGH", "windows": 800, "labelled": 600, "cheat": 300, "legit": 300,
                                  "lastUpload": t - 600_000, "falsePositives": 2, "falsePositiveRate": 0.0067,
                                  "missedCheaters": 5, "missedRate": 0.0167, "excluded": False},
                                 {"key": "MYTH-TEST-0002-ABCD-EFGH", "windows": 120, "labelled": 80, "cheat": 70, "legit": 10,
                                  "lastUpload": t - 2 * DAY, "falsePositives": 9, "falsePositiveRate": 0.9,
                                  "missedCheaters": 1, "missedRate": 0.0143, "excluded": True}])
        if path == ["api", "stats"]:
            vs = [view(k) for k in keys.values()]
            return self.ok(total=len(vs), active=sum(v["status"] == "active" for v in vs),
                           revoked=sum(v["status"] == "revoked" for v in vs), expired=sum(v["status"] == "expired" for v in vs),
                           servers=sum(v["activeServers"] for v in vs),
                           plans={p: sum(v["status"] == "active" and v["plan"] == p for v in vs) for p in PLANS})
        if path == ["api", "plans"]:
            return self.ok(plans=[{"plan": p, "maxServers": m} for p, m in PLANS.items()], offlineHours=6, activeHours=24)
        if path == ["api", "log"]:
            ev = [e for e in events if not q.get("key") or e["key"] == q["key"]]
            return self.ok(events=ev[: min(int(q.get("limit", 200)), 1000)])
        if path == ["api", "keys"] and method == "GET":
            out = []
            for k in keys.values():
                v = view(k)
                text = q.get("q", "").lower()
                if text and text not in (k["key"] + k["note"] + (k["owner"] or "")).lower():
                    continue
                if q.get("plan") and v["plan"] != q["plan"]:
                    continue
                if q.get("status") and v["status"] != q["status"]:
                    continue
                out.append(v)
            return self.ok(keys=out)
        if path == ["api", "keys"] and method == "POST":
            if body.get("plan") not in PLANS:
                return self.fail("Unknown plan")
            kid = new_key_id()
            days = 30 if body.get("days") in (None, "") else body["days"]
            keys[kid] = {"key": kid, "plan": body["plan"], "status": "active", "note": body.get("note", ""), "created": now(),
                         "expires": now() + days * DAY if days else 0, "owner": body.get("owner") or None,
                         "custom": -1 if body.get("servers") in (None, "") else body["servers"], "nextReset": 0, "servers": []}
            log(actor, "create", "Key created", kid, f"{body['plan']}, {days or 'no'} days")
            return self.ok(key=view(keys[kid], detail=True))
        if len(path) == 4 and path[:2] == ["api", "owners"] and path[3] == "delete" and method == "POST":
            gone = [kid for kid, k in keys.items() if k["owner"] == path[2]]
            for kid in gone:
                del keys[kid]
                log(actor, "delete", "Key deleted", kid)
            return self.ok(deleted=gone)
        if len(path) == 4 and path[:2] == ["api", "keys"] and path[3] == "delete" and method == "POST":
            if path[2] not in keys:
                return self.fail("Unknown key")
            del keys[path[2]]
            log(actor, "delete", "Key deleted", path[2])
            return self.ok(deleted=path[2])
        if len(path) >= 3 and path[:2] == ["api", "keys"]:
            k = keys.get(path[2])
            if not k:
                return self.fail("Unknown key")
            if len(path) == 3:
                v = view(k, detail=True)
                v["events"] = [e for e in events if e["key"] == k["key"]]
                return self.ok(key=v)
            action = path[3]
            if action == "revoke":
                if k["status"] == "revoked":
                    return self.fail("This key is already revoked")
                k["status"] = "revoked"
                log(actor, "revoke", "Key revoked", k["key"])
            elif action == "unrevoke":
                k["status"] = "active"
                log(actor, "unrevoke", "Key restored", k["key"])
            elif action == "reset":
                k["servers"] = []
                log(actor, "reset", "Servers reset", k["key"])
            elif action == "plan":
                if body.get("plan") not in PLANS:
                    return self.fail("Unknown plan")
                k["plan"] = body["plan"]
                log(actor, "plan", "Plan changed", k["key"], body["plan"])
            elif action == "renew":
                months = int(body.get("months") or 1)
                k["expires"] = max(now(), k["expires"] or now()) + months * 30 * DAY
                log(actor, "expires", "Key renewed", k["key"], f"+{months} month(s)")
            elif action == "extend":
                d = int(body.get("days", 0))
                k["expires"] = 0 if d == 0 else max(now(), k["expires"] or now()) + d * DAY
                log(actor, "expires", "Expiry changed", k["key"], f"+{d} days")
            elif action == "servers":
                k["custom"] = int(body.get("servers"))
                log(actor, "servers", "Server limit changed", k["key"], str(k["custom"]))
            elif action == "note":
                k["note"] = body.get("note", "")
                log(actor, "note", "Note changed", k["key"])
            elif action == "owner":
                k["owner"] = body.get("owner") or None
                log(actor, "owner", "Owner changed", k["key"], k["owner"] or "removed")
            else:
                return self.fail("Unknown endpoint", 404)
            return self.ok(key=view(k, detail=True))
        if len(path) >= 4 and path[:2] == ["api", "users"]:
            uid = path[2]
            if path[3] == "keys" and len(path) == 4:
                return self.ok(keys=[view(k, admin=False, detail=True) for k in keys.values() if k["owner"] == uid], resetHours=RESET_HOURS)
            if path[3] == "claim":
                k = keys.get(str(body.get("key", "")).upper())
                if not k:
                    return self.fail("This key does not exist")
                if k["owner"] and k["owner"] != uid:
                    return self.fail("This key belongs to someone else")
                k["owner"] = uid
                log(f"website: {body.get('name')}", "claim", "Key claimed", k["key"])
                return self.ok(key=view(k, admin=False, detail=True))
            if path[3] == "keys" and len(path) == 6 and path[5] == "reset":
                k = keys.get(path[4])
                if not k or k["owner"] != uid:
                    return self.fail("This key is not yours")
                if k["nextReset"] > now():
                    hours = -(-(k["nextReset"] - now()) // 3_600_000)
                    return self.fail(f"You can reset again in {hours} hours")
                k["servers"] = []
                k["nextReset"] = now() + RESET_HOURS * 3_600_000
                log(f"website: {body.get('name')}", "reset", "Servers reset", k["key"])
                return self.ok(key=view(k, admin=False, detail=True))
        return self.fail("Unknown endpoint", 404)

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")


seed()
seed_panel()
if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
