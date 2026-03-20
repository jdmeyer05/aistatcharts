"""Stripe Webhook Server — runs alongside the Streamlit app.

Handles:
- checkout.session.completed → activate subscription tier or add tokens
- customer.subscription.updated → tier changes (upgrade/downgrade)
- customer.subscription.deleted → revert to free tier
- invoice.payment_failed → flag account, send notification

Run: python webhook_server.py
Listens on port 5000 by default.

Stripe webhook setup:
1. Go to dashboard.stripe.com/webhooks
2. Add endpoint: https://your-domain.com/stripe/webhook
3. Select events: checkout.session.completed, customer.subscription.updated,
   customer.subscription.deleted, invoice.payment_failed
4. Copy the webhook signing secret → add as STRIPE_WEBHOOK_SECRET in secrets
"""

import os
import json
import logging
from datetime import datetime
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook")

app = Flask(__name__)


def _get_secret(name):
    """Read from environment or .streamlit/secrets.toml."""
    val = os.environ.get(name)
    if val:
        return val
    # Parse secrets.toml
    try:
        secrets_path = os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml")
        with open(secrets_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(name):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


# Initialize Stripe
import stripe
stripe.api_key = _get_secret("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = _get_secret("STRIPE_WEBHOOK_SECRET")

# Initialize Supabase
SUPABASE_URL = _get_secret("SUPABASE_URL")
SUPABASE_KEY = _get_secret("SUPABASE_KEY")
_supabase = None

def get_supabase():
    global _supabase
    if _supabase is None and SUPABASE_URL and SUPABASE_KEY:
        from supabase import create_client
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


# Tier mapping from price metadata/lookup_key
TIER_MAP = {
    "pro": "pro", "pro_monthly": "pro", "pro_yearly": "pro",
    "premium": "premium", "premium_monthly": "premium", "premium_yearly": "premium",
    "platinum": "platinum", "platinum_monthly": "platinum", "platinum_yearly": "platinum",
}

# Token pack mapping — product name keywords → token count
TOKEN_PACKS = {
    "starter": 50,
    "power": 200,
    "elite": 500,
}


def _get_tier_from_price(price_id):
    """Determine tier from a Stripe price ID."""
    try:
        price = stripe.Price.retrieve(price_id, expand=["product"])
        metadata = price.get("metadata", {})

        # Check metadata first
        if metadata.get("tier"):
            return TIER_MAP.get(metadata["tier"], metadata["tier"])

        # Check lookup_key
        if price.get("lookup_key"):
            return TIER_MAP.get(price["lookup_key"], "pro")

        # Check product name
        product = price.get("product", {})
        if isinstance(product, dict):
            pname = product.get("name", "").lower()
        else:
            prod = stripe.Product.retrieve(product)
            pname = prod.get("name", "").lower()

        if "platinum" in pname:
            return "platinum"
        elif "premium" in pname:
            return "premium"
        elif "pro" in pname:
            return "pro"
    except Exception as e:
        logger.error(f"Error determining tier from price {price_id}: {e}")

    return "pro"


def _get_tokens_from_product(product_name):
    """Determine token count from product name."""
    name_lower = product_name.lower()
    for keyword, count in TOKEN_PACKS.items():
        if keyword in name_lower:
            return count
    return 0


def _update_user_tier(email, tier, stripe_customer_id=None, price_id=None):
    """Update user's subscription tier in Supabase."""
    supabase = get_supabase()
    if not supabase:
        logger.warning("Supabase not configured — cannot update tier")
        return

    try:
        data = {
            "email": email,
            "plan_type": tier,
            "status": "active",
            "updated_at": datetime.utcnow().isoformat(),
        }
        if stripe_customer_id:
            data["stripe_customer_id"] = stripe_customer_id
        if price_id:
            data["stripe_price_id"] = price_id

        supabase.table("subscriptions").upsert(data, on_conflict="email").execute()
        logger.info(f"Updated tier for {email} → {tier}")
    except Exception as e:
        logger.error(f"Failed to update tier for {email}: {e}")


def _add_user_tokens(email, amount):
    """Add tokens to a user's balance in Supabase."""
    supabase = get_supabase()
    if not supabase:
        logger.warning("Supabase not configured — cannot add tokens")
        return

    try:
        # Get current balance
        result = supabase.table("user_tokens").select("balance").eq("email", email).execute()
        current = result.data[0]["balance"] if result.data else 0

        # Upsert new balance
        supabase.table("user_tokens").upsert({
            "email": email,
            "balance": current + amount,
            "updated_at": datetime.utcnow().isoformat(),
        }, on_conflict="email").execute()
        logger.info(f"Added {amount} tokens for {email} (new balance: {current + amount})")
    except Exception as e:
        logger.error(f"Failed to add tokens for {email}: {e}")


def _flag_payment_failed(email, invoice_id):
    """Record a failed payment in Supabase."""
    supabase = get_supabase()
    if not supabase:
        return

    try:
        supabase.table("payment_failures").insert({
            "email": email,
            "invoice_id": invoice_id,
            "failed_at": datetime.utcnow().isoformat(),
            "resolved": False,
        }).execute()
        logger.warning(f"Payment failed for {email} (invoice: {invoice_id})")
    except Exception as e:
        logger.error(f"Failed to record payment failure: {e}")


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    """Handle Stripe webhook events."""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")

    # Verify webhook signature
    if WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
        except stripe.error.SignatureVerificationError:
            logger.error("Invalid webhook signature")
            return jsonify({"error": "Invalid signature"}), 400
    else:
        # No secret configured — parse directly (dev only)
        event = json.loads(payload)
        logger.warning("No STRIPE_WEBHOOK_SECRET — skipping signature verification")

    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    logger.info(f"Webhook received: {event_type}")

    # ── Checkout completed (new subscription or token purchase) ──
    if event_type == "checkout.session.completed":
        email = data.get("customer_email") or data.get("customer_details", {}).get("email", "")
        customer_id = data.get("customer", "")
        mode = data.get("mode", "")

        if mode == "subscription":
            # New subscription — determine tier from the subscription
            sub_id = data.get("subscription", "")
            if sub_id:
                try:
                    sub = stripe.Subscription.retrieve(sub_id)
                    items = sub.get("items", {}).get("data", [])
                    if items:
                        price_id = items[0].get("price", {}).get("id", "")
                        tier = _get_tier_from_price(price_id)
                        _update_user_tier(email, tier, customer_id, price_id)
                except Exception as e:
                    logger.error(f"Error processing subscription checkout: {e}")

        elif mode == "payment":
            # One-time payment — likely token purchase
            line_items = stripe.checkout.Session.list_line_items(data["id"])
            for item in line_items.get("data", []):
                product_name = item.get("description", "")
                tokens = _get_tokens_from_product(product_name)
                if tokens > 0 and email:
                    _add_user_tokens(email, tokens)

    # ── Subscription updated (upgrade/downgrade) ──
    elif event_type == "customer.subscription.updated":
        customer_id = data.get("customer", "")
        items = data.get("items", {}).get("data", [])
        if items and customer_id:
            price_id = items[0].get("price", {}).get("id", "")
            tier = _get_tier_from_price(price_id)
            # Get email from customer
            try:
                customer = stripe.Customer.retrieve(customer_id)
                email = customer.get("email", "")
                if email:
                    _update_user_tier(email, tier, customer_id, price_id)
            except Exception as e:
                logger.error(f"Error processing subscription update: {e}")

    # ── Subscription cancelled ──
    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer", "")
        try:
            customer = stripe.Customer.retrieve(customer_id)
            email = customer.get("email", "")
            if email:
                _update_user_tier(email, "free", customer_id)
        except Exception as e:
            logger.error(f"Error processing subscription deletion: {e}")

    # ── Payment failed ──
    elif event_type == "invoice.payment_failed":
        customer_id = data.get("customer", "")
        invoice_id = data.get("id", "")
        try:
            customer = stripe.Customer.retrieve(customer_id)
            email = customer.get("email", "")
            if email:
                _flag_payment_failed(email, invoice_id)
        except Exception as e:
            logger.error(f"Error processing payment failure: {e}")

    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})


if __name__ == "__main__":
    port = int(os.environ.get("WEBHOOK_PORT", 5000))
    logger.info(f"Starting webhook server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
