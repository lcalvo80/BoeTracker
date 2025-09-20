# app/blueprints/webhooks.py
from __future__ import annotations
import os
import stripe
from flask import Blueprint, request, jsonify, current_app
from app.services import clerk_svc
from svix.webhooks import Webhook, WebhookVerificationError

bp = Blueprint("webhooks", __name__, url_prefix="/api")

def _cfg(k, default=None):
    v = current_app.config.get(k)
    if v is None or str(v).strip() == "":
        v = os.getenv(k, default)
    return v

def _init_stripe():
    sk = _cfg("STRIPE_SECRET_KEY", "")
    if not sk:
        return None, (jsonify(error="STRIPE_SECRET_KEY missing"), 500)
    stripe.api_key = sk
    return sk, None

def _plan_from_subscription(sub: dict) -> str:
    st = (sub or {}).get("status")
    if st in ("active", "trialing", "past_due"):
        # Puedes derivar por price.nickname si quieres afinar
        return "pro"
    return "free"

# ───────── Stripe Webhook ─────────
def _handle_stripe():
    _, err = _init_stripe()
    if err: return err
    wh_secret = _cfg("STRIPE_WEBHOOK_SECRET", "")
    if not wh_secret:
        return jsonify(error="STRIPE_WEBHOOK_SECRET missing"), 500

    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, wh_secret)
    except Exception as e:
        return jsonify(error=f"invalid signature: {e}"), 400

    etype = event["type"]
    obj = event["data"]["object"]

    try:
        if etype == "checkout.session.completed":
            session = obj
            sub_id = session.get("subscription")
            customer_id = session.get("customer")
            meta = session.get("metadata") or {}
            user_id = meta.get("entity_id") or meta.get("clerk_user_id")

            sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"]) if sub_id else None
            status = (sub or {}).get("status") or "active"
            plan = _plan_from_subscription(sub or {})

            if user_id:
                priv = {"billing": {"stripeCustomerId": customer_id, "subscriptionId": sub.get("id") if sub else None, "status": status}}
                clerk_svc.set_user_plan(user_id, plan=plan, status=status, extra_private=priv)

        elif etype in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
            sub = obj
            status = sub.get("status")
            plan = _plan_from_subscription(sub)
            # Intentamos recuperar user_id desde metadata del customer/subscription
            cust = stripe.Customer.retrieve(sub.get("customer")) if sub.get("customer") else None
            user_id = None
            if cust:
                md = cust.get("metadata") or {}
                user_id = md.get("clerk_user_id") or md.get("entity_id")
            # Fallback: metadata en la sub
            if not user_id:
                md = sub.get("metadata") or {}
                user_id = md.get("clerk_user_id") or md.get("entity_id")

            if user_id:
                priv = {"billing": {"stripeCustomerId": sub.get("customer"), "subscriptionId": sub.get("id"), "status": status}}
                clerk_svc.set_user_plan(user_id, plan=plan, status=status, extra_private=priv)

        # OK siempre
        return jsonify(received=True), 200

    except Exception:
        current_app.logger.exception("stripe webhook handler error")
        return jsonify(error="handler error"), 500

@bp.post("/stripe")
def stripe_webhook_api():
    return _handle_stripe()

# alias legacy
@bp.post("/../stripe")  # no visible; solo por compat al registrar sin url_prefix
def stripe_webhook_legacy_passthrough():
    return _handle_stripe()

# ───────── Clerk Webhook (Svix) ─────────
def _handle_clerk():
    secret = _cfg("CLERK_WEBHOOK_SECRET", "")
    if not secret:
        return jsonify(error="CLERK_WEBHOOK_SECRET missing"), 500

    headers = {
        "svix-id": request.headers.get("svix-id", ""),
        "svix-timestamp": request.headers.get("svix-timestamp", ""),
        "svix-signature": request.headers.get("svix-signature", ""),
    }
    payload = request.get_data()
    try:
        event = Webhook(secret).verify(payload, headers)
    except WebhookVerificationError:
        return jsonify(error="invalid svix signature"), 400
    except Exception:
        current_app.logger.exception("clerk webhook error")
        return jsonify(error="bad request"), 400

    evt_type = event.get("type")
    data = event.get("data") or {}
    try:
        if evt_type == "user.created":
            uid = data.get("id")
            if uid:
                clerk_svc.set_user_plan(uid, plan="free", status="none")
        # otros evt opcionales...
    except Exception:
        current_app.logger.exception("clerk handler error")

    return jsonify(ok=True), 200

@bp.post("/clerk")
def clerk_webhook_api():
    return _handle_clerk()

# alias legacy
@bp.post("/../clerk")  # no visible; compat
def clerk_webhook_legacy_passthrough():
    return _handle_clerk()
