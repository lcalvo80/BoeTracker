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

# ───────── Idempotencia (plug: DB/Redis) ─────────
def _already_processed(event_id: str) -> bool:
    return False

def _mark_processed(event_id: str):
    pass

def _sum_seats_from_subscription(sub: dict) -> int:
    qty = 0
    for it in (sub.get("items", {}) or {}).get("data", []) or []:
        try:
            qty += int(it.get("quantity") or 0)
        except Exception:
            pass
    return max(qty, 1)

def _ensure_customer_has_entity(customer_id: str, entity_type: str, entity_id: str):
    try:
        cust = stripe.Customer.retrieve(customer_id)
        md = cust.get("metadata") or {}
        if md.get("entity_type") != entity_type or md.get("entity_id") != entity_id:
            md.update({"entity_type": entity_type, "entity_id": entity_id})
            if entity_type == "user":
                md.setdefault("clerk_user_id", entity_id)
            if entity_type == "org":
                md.setdefault("clerk_org_id", entity_id)
            stripe.Customer.modify(customer_id, metadata=md)
    except Exception:
        current_app.logger.warning("[Stripe] no se pudo garantizar metadata de customer")

def _handle_stripe():
    _, err = _init_stripe()
    if err:
        return err

    wh_secret = _cfg("STRIPE_WEBHOOK_SECRET", "")
    if not wh_secret:
        return jsonify(error="STRIPE_WEBHOOK_SECRET missing"), 500

    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, wh_secret)
    except Exception as e:
        current_app.logger.warning(f"[Stripe] invalid signature: {e}")
        return jsonify(error="invalid signature"), 400

    event_id = event.get("id")
    etype = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}

    if event_id and _already_processed(event_id):
        current_app.logger.info(f"[Stripe] duplicate event {event_id} ({etype}) ignored")
        return jsonify(received=True, dedup=True), 200

    try:
        if etype == "checkout.session.completed":
            session = obj
            sub_id = session.get("subscription")
            customer_id = session.get("customer")
            meta = session.get("metadata") or {}
            entity_type = meta.get("entity_type")
            entity_id = meta.get("entity_id") or meta.get("clerk_user_id") or meta.get("user_id") or meta.get("org_id")

            sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"]) if sub_id else None
            status = (sub or {}).get("status") or "active"
            seats = _sum_seats_from_subscription(sub or {})

            if customer_id and entity_type and entity_id:
                _ensure_customer_has_entity(customer_id, entity_type, entity_id)

            if entity_type == "user" and entity_id:
                priv = {"billing": {"stripeCustomerId": customer_id, "subscriptionId": sub.get("id") if sub else None, "status": status}}
                clerk_svc.set_user_plan(entity_id, plan=("pro" if status in ("active","trialing","past_due") else "free"), status=status, extra_private=priv)

            elif entity_type == "org" and entity_id:
                priv = {"billing": {"stripeCustomerId": customer_id, "subscriptionId": sub.get("id") if sub else None, "status": status}}
                clerk_svc.set_org_plan(entity_id, plan="enterprise", status=status,
                                       extra_private=priv,
                                       extra_public={"seats": seats, "subscription": "enterprise"})

            else:
                current_app.logger.warning("[Stripe] checkout.session.completed sin entity_id/entity_type")

        elif etype in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
            sub = obj
            status = sub.get("status") or "canceled"
            seats = _sum_seats_from_subscription(sub)

            cust = None
            try:
                if sub.get("customer"):
                    cust = stripe.Customer.retrieve(sub["customer"])
            except Exception:
                current_app.logger.exception("[Stripe] error retrieving customer")

            md_cust = (cust.get("metadata") if cust else {}) or {}
            md_sub = (sub.get("metadata") or {})

            entity_type = md_cust.get("entity_type") or md_sub.get("entity_type")
            entity_id = (md_cust.get("entity_id") or md_sub.get("entity_id")
                         or md_cust.get("clerk_user_id") or md_sub.get("clerk_user_id")
                         or md_cust.get("user_id") or md_sub.get("user_id")
                         or md_cust.get("org_id") or md_sub.get("org_id"))

            if entity_type == "user" and entity_id:
                priv = {"billing": {"stripeCustomerId": sub.get("customer"), "subscriptionId": sub.get("id"), "status": status}}
                clerk_svc.set_user_plan(entity_id, plan=("pro" if status in ("active","trialing","past_due") else "free"),
                                        status=status, extra_private=priv)

            elif entity_type == "org" and entity_id:
                priv = {"billing": {"stripeCustomerId": sub.get("customer"), "subscriptionId": sub.get("id"), "status": status}}
                clerk_svc.set_org_plan(entity_id, plan=("enterprise" if status in ("active","trialing","past_due") else "free"),
                                       status=status, extra_private=priv,
                                       extra_public={"seats": seats, "subscription": ("enterprise" if status in ("active","trialing","past_due") else None)})

            else:
                current_app.logger.warning("[Stripe] subscription.* sin entity_id/entity_type")

        if event_id:
            _mark_processed(event_id)

        return jsonify(received=True), 200

    except Exception:
        current_app.logger.exception("stripe webhook handler error")
        return jsonify(error="handler error"), 500

@bp.post("/stripe")
def stripe_webhook_api():
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
        # otros evt si necesitas...
    except Exception:
        current_app.logger.exception("clerk handler error")

    return jsonify(ok=True), 200

@bp.post("/clerk")
def clerk_webhook_api():
    return _handle_clerk()
