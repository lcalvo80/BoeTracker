# app/routes/webhooks.py
import json
import stripe
from flask import Blueprint, request, jsonify, current_app, abort
from app.integrations import clerk_admin as clerk

bp = Blueprint("webhooks", __name__)


def _patch_target(entity_type: str, entity_id: str, patch: dict):
    if entity_type == "user":
        user = clerk.get_user(entity_id)
        pm = (user.get("public_metadata") or {})
        pm.update(patch)
        clerk.patch_user_public_metadata(entity_id, pm)
    elif entity_type == "org":
        org = clerk.get_org(entity_id)
        pm = (org.get("public_metadata") or {})
        pm.update(patch)
        clerk.patch_org_public_metadata(entity_id, pm)
    else:
        raise ValueError("entity_type must be user/org")


def _extract_target_from_subscription(sub):
    md = sub.get("metadata") or {}
    entity_type = md.get("entity_type")
    entity_id = md.get("entity_id")
    return entity_type, entity_id


def _subscription_payload(sub, price_id_hint=None):
    item = (sub.get("items", {}).get("data") or [{}])[0]
    price = item.get("price") or {}
    price_id = price.get("id") or price_id_hint
    return {
        "subscription": {
            "status": sub.get("status"),
            "plan_price_id": price_id,
            "subscription_id": sub.get("id"),
            "cancel_at_period_end": sub.get("cancel_at_period_end"),
            "current_period_end": sub.get("current_period_end"),
            "customer": sub.get("customer"),
        }
    }


@bp.post("/stripe")
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature", "")
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        abort(500, "STRIPE_WEBHOOK_SECRET not configured")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as e:
        abort(400, f"Webhook signature invalid: {e}")

    type_ = event["type"]

    # checkout.session.completed → tomar la subscription y marcar activa
    if type_ == "checkout.session.completed":
        session = event["data"]["object"]
        sub_id = session.get("subscription")
        price_id = (session.get("metadata") or {}).get("price_id")
        if sub_id:
            sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
            entity_type, entity_id = _extract_target_from_subscription(sub)
            if entity_type and entity_id:
                _patch_target(entity_type, entity_id, _subscription_payload(sub, price_id_hint=price_id))
        return jsonify(received=True)

    # Actualizaciones de suscripción (upgrade/downgrade/cancel)
    if type_ in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        sub = event["data"]["object"]
        entity_type, entity_id = _extract_target_from_subscription(sub)
        if entity_type and entity_id:
            _patch_target(entity_type, entity_id, _subscription_payload(sub))
        return jsonify(received=True)

    # (Opcional) falla de pago → marcar estado para UI
    if type_ == "invoice.payment_failed":
        inv = event["data"]["object"]
        sub_id = inv.get("subscription")
        if sub_id:
            sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
            entity_type, entity_id = _extract_target_from_subscription(sub)
            if entity_type and entity_id:
                patch = _subscription_payload(sub)
                patch["subscription"]["last_payment_failed"] = True
                _patch_target(entity_type, entity_id, patch)
        return jsonify(received=True)

    return jsonify(ignored=True)
