"""Tiny fake of the PayPal REST API, for the tests (never used in production).

Test helpers (not PayPal endpoints):
    POST /_approve/<order or subscription id>   the buyer approves on PayPal (a subscription also pays month 1)
    POST /_charge/<subscription id>             PayPal charges one more month
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

orders, subs, seq = {}, {}, [0]
PLAN_ID = "P-MOCKPRO"
VERIFY = {"result": "SUCCESS"}


def nid(prefix):
    seq[0] += 1
    return f"{prefix}{seq[0]:06d}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj):
        raw = b"" if code == 204 else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}

    def authed(self):
        return self.headers.get("Authorization", "") == "Bearer MOCK-TOKEN"

    def do_GET(self):
        path = self.path.split("?")[0].strip("/").split("/")
        if not self.authed():
            return self.send(401, {"name": "AUTHENTICATION_FAILURE"})
        if path[:3] == ["v2", "checkout", "orders"] and len(path) == 4 and path[3] in orders:
            return self.send(200, orders[path[3]])
        if path[:3] == ["v1", "billing", "subscriptions"] and len(path) == 4 and path[3] in subs:
            return self.send(200, subs[path[3]])
        self.send(404, {"name": "RESOURCE_NOT_FOUND"})

    def do_POST(self):
        path = self.path.split("?")[0].strip("/").split("/")
        body = self.body()
        if path == ["v1", "oauth2", "token"]:
            if self.headers.get("Authorization", "").startswith("Basic "):
                return self.send(200, {"access_token": "MOCK-TOKEN", "expires_in": 32400})
            return self.send(401, {"error": "invalid_client"})
        if path[0] == "_approve":
            oid = path[1]
            if oid in orders:
                orders[oid]["status"] = "APPROVED"
            elif oid in subs:
                subs[oid]["status"] = "ACTIVE"
                subs[oid]["billing_info"]["cycle_executions"][0]["cycles_completed"] = 1
            return self.send(200, {})
        if path[0] == "_charge":
            subs[path[1]]["billing_info"]["cycle_executions"][0]["cycles_completed"] += 1
            return self.send(200, {})
        if not self.authed():
            return self.send(401, {"name": "AUTHENTICATION_FAILURE"})
        if path == ["v2", "checkout", "orders"]:
            oid = nid("ORDER")
            unit = dict(body["purchase_units"][0])
            orders[oid] = {"id": oid, "status": "PAYER_ACTION_REQUIRED", "purchase_units": [unit],
                           "links": [{"rel": "payer-action", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}]}
            return self.send(200, orders[oid])
        if path[:3] == ["v2", "checkout", "orders"] and len(path) == 5 and path[4] == "capture":
            o = orders.get(path[3])
            if not o:
                return self.send(404, {"name": "RESOURCE_NOT_FOUND"})
            if o["status"] == "COMPLETED":
                return self.send(422, {"name": "ORDER_ALREADY_CAPTURED"})
            if o["status"] != "APPROVED":
                return self.send(422, {"name": "ORDER_NOT_APPROVED"})
            o["status"] = "COMPLETED"
            o["purchase_units"][0]["payments"] = {"captures": [
                {"id": "CAP" + o["id"], "status": "COMPLETED", "amount": o["purchase_units"][0]["amount"]}]}
            return self.send(201, o)
        if path == ["v1", "billing", "subscriptions"]:
            sid = nid("I-SUB")
            subs[sid] = {"id": sid, "status": "APPROVAL_PENDING", "plan_id": body.get("plan_id"),
                         "custom_id": body.get("custom_id"),
                         "billing_info": {"cycle_executions": [{"tenure_type": "REGULAR", "cycles_completed": 0}],
                                          "next_billing_time": "2026-11-24T10:00:00Z"},
                         "links": [{"rel": "approve", "href": f"https://www.sandbox.paypal.com/webapps/billing/subscriptions?ba_token={sid}"}]}
            return self.send(201, subs[sid])
        if path[:3] == ["v1", "billing", "subscriptions"] and len(path) == 5 and path[4] == "cancel":
            s = subs.get(path[3])
            if not s:
                return self.send(404, {"name": "RESOURCE_NOT_FOUND"})
            s["status"] = "CANCELLED"
            return self.send(204, {})
        if path == ["v1", "notifications", "verify-webhook-signature"]:
            ok = body.get("webhook_id") and body.get("transmission_sig") == "good-signature"
            return self.send(200, {"verification_status": "SUCCESS" if ok else "FAILURE"})
        self.send(404, {"name": "RESOURCE_NOT_FOUND"})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5066
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
