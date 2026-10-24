"""One-time PayPal setup: creates the Pro product + monthly plan and the webhook, prints the env lines to add.

On the VPS (reads PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET / PAYPAL_MODE from the env file, never prints them):
    set -a; . /etc/mythras-site.env; set +a
    /opt/mythras-site/.venv/bin/python /opt/mythras-site/tools/paypal_setup.py https://www.mythras.eu

Run it once in sandbox and once in live (plans and webhooks are separate in the two modes).
It does not create a second plan or webhook if PAYPAL_PRO_PLAN_ID / a webhook for the same URL already exist.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import payments  # noqa: E402

PRO_PRICE = "19.99"


def main():
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        sys.exit("Usage: paypal_setup.py https://www.mythras.eu")
    site = sys.argv[1].rstrip("/")
    if not payments.enabled():
        sys.exit("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are not set (load /etc/mythras-site.env first).")
    print(f"PayPal mode: {payments.mode()}")

    plan_id = os.getenv("PAYPAL_PRO_PLAN_ID", "")
    if plan_id:
        print(f"Pro plan already set: {plan_id}")
    else:
        product = payments.call("/v1/catalogs/products", method="POST", body={
            "name": "Mythras AntiCheat Pro", "type": "SERVICE", "category": "SOFTWARE",
            "description": "Mythras AntiCheat Pro license, renewed every month", "home_url": site})
        plan = payments.call("/v1/billing/plans", method="POST", body={
            "product_id": product["id"], "name": "Mythras AntiCheat Pro - monthly", "status": "ACTIVE",
            "description": "Pro license for Mythras AntiCheat, renewed every month until cancelled",
            "billing_cycles": [{"frequency": {"interval_unit": "MONTH", "interval_count": 1}, "tenure_type": "REGULAR",
                                "sequence": 1, "total_cycles": 0,
                                "pricing_scheme": {"fixed_price": {"value": PRO_PRICE, "currency_code": "EUR"}}}],
            "payment_preferences": {"auto_bill_outstanding": True, "setup_fee_failure_action": "CANCEL",
                                    "payment_failure_threshold": 2}})
        plan_id = plan["id"]
        print(f"Created product {product['id']} and plan {plan_id} ({PRO_PRICE} EUR / month)")

    url = f"{site}/webhooks/paypal"
    hooks = payments.call("/v1/notifications/webhooks").get("webhooks", [])
    hook = next((h for h in hooks if h.get("url") == url), None)
    if hook:
        print(f"Webhook already exists for {url}")
    else:
        hook = payments.call("/v1/notifications/webhooks", method="POST", body={
            "url": url, "event_types": [{"name": e} for e in payments.WEBHOOK_EVENTS]})
        print(f"Created webhook for {url}")

    print("\nAdd these lines to /etc/mythras-site.env, then: systemctl restart mythras-site")
    print(f"PAYPAL_PRO_PLAN_ID={plan_id}")
    print(f"PAYPAL_WEBHOOK_ID={hook['id']}")


if __name__ == "__main__":
    try:
        main()
    except payments.PayPalError as err:
        sys.exit(err.message)
