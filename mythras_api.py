"""Server-side client for the Mythras license server.

Only the Flask backend imports this module. The API token is read from the
environment and is never rendered into a page or sent to the browser.

Env:
    MYTHRAS_API_URL    http://127.0.0.1:8080 when the site runs on the license server's VPS
                       (api-local-only=true, the default); the HTTPS address otherwise
    MYTHRAS_API_TOKEN  api-token from license.properties on the license server
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 10


class MythrasError(Exception):
    """An action refused or failed. `message` is safe to show to the user."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.message = message
        self.status = status


def _config():
    url = os.getenv("MYTHRAS_API_URL", "").rstrip("/")
    token = os.getenv("MYTHRAS_API_TOKEN", "")
    if not url or not token:
        raise MythrasError("The license server is not configured on this website.")
    return url, token


def _seg(value):
    """Encode one path segment so a key or ID can never change the API path."""
    return urllib.parse.quote(str(value), safe="")


def call(path, method="GET", body=None, query=None):
    url, token = _config()
    full = url + path
    if query:
        clean = {k: v for k, v in query.items() if v not in (None, "")}
        if clean:
            full += "?" + urllib.parse.urlencode(clean)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        full,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "MythrasWebsite/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as err:
        try:
            payload = json.load(err)
        except ValueError:
            raise MythrasError(f"License server error (HTTP {err.code}).", err.code) from None
        if err.code == 401:
            # never echo token details; this is a website configuration problem
            raise MythrasError("The website could not authenticate with the license server.", 401) from None
        if err.code == 403:
            raise MythrasError(
                "The license server only accepts calls from its own machine (api-local-only). "
                "Set MYTHRAS_API_URL to http://127.0.0.1:8080 on the same VPS.",
                403,
            ) from None
        raise MythrasError(payload.get("error") or f"License server error (HTTP {err.code}).", err.code) from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise MythrasError("The license server is unreachable right now. Please try again shortly.") from None

    if not payload.get("ok"):
        raise MythrasError(payload.get("error") or "The license server refused the request.")
    return payload


# ---------- admin ----------

def stats():
    return call("/api/stats")


def plans():
    return call("/api/plans")


def list_keys(q=None, plan=None, status=None):
    return call("/api/keys", query={"q": q, "plan": plan, "status": status})["keys"]


def create_key(plan, days=None, servers=None, note=None, owner=None, actor=None):
    body = {"plan": plan, "actor": actor}
    if days is not None:
        body["days"] = days
    if servers is not None:
        body["servers"] = servers
    if note:
        body["note"] = note
    if owner:
        body["owner"] = owner
    return call("/api/keys", method="POST", body=body)["key"]


def get_key(key):
    return call(f"/api/keys/{_seg(key)}")["key"]


KEY_ACTIONS = {"revoke", "unrevoke", "reset", "plan", "renew", "extend", "servers", "note", "owner"}


def key_action(key, action, actor, **fields):
    if action not in KEY_ACTIONS:
        raise MythrasError("Unknown action.")
    body = dict(fields, actor=actor)
    return call(f"/api/keys/{_seg(key)}/{action}", method="POST", body=body)["key"]


def delete_key(key, actor):
    """Delete one key for good."""
    return call(f"/api/keys/{_seg(key)}/delete", method="POST", body={"actor": actor})


def delete_owner_keys(discord_id, actor):
    """Delete every key of one customer."""
    return call(f"/api/owners/{_seg(discord_id)}/delete", method="POST", body={"actor": actor})


def log(limit=200, key=None):
    return call("/api/log", query={"limit": limit, "key": key})["events"]


# ---------- customer (discord_id always comes from the website session) ----------

def user_keys(discord_id):
    return call(f"/api/users/{_seg(discord_id)}/keys")


def user_claim(discord_id, key, name=None):
    return call(f"/api/users/{_seg(discord_id)}/claim", method="POST", body={"key": key, "name": name})["key"]


def user_reset(discord_id, key, name=None):
    return call(
        f"/api/users/{_seg(discord_id)}/keys/{_seg(key)}/reset", method="POST", body={"name": name}
    )["key"]


# ---------- panel: servers, alerts, punishments, replays ----------
# discord_id = the logged-in customer (from the session); None = admin view over all keys.

def _panel(discord_id):
    return f"/api/users/{_seg(discord_id)}/panel" if discord_id else "/api/panel"


def panel_servers(discord_id=None, key=None):
    return call(_panel(discord_id) + "/servers", query={"key": key})["servers"]


def panel_stats(discord_id=None, key=None, days=7):
    return call(_panel(discord_id) + "/stats", query={"key": key, "days": days})


def panel_events(discord_id=None, key=None, server=None, type=None, player=None, check=None, days=None,
                 before=None, limit=50):
    return call(_panel(discord_id) + "/events", query={
        "key": key, "server": server, "type": type, "player": player, "check": check,
        "days": days, "before": before, "limit": limit,
    })


def panel_player(name, discord_id=None, key=None, days=None):
    return call(_panel(discord_id) + f"/players/{_seg(name)}", query={"key": key, "days": days})


def ai_stats(runs=50):
    return call("/api/ai/stats", query={"runs": runs})
