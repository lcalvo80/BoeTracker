from __future__ import annotations

from typing import Optional, Tuple, Dict, Any

import stripe
from flask import Blueprint, request, jsonify, current_app, abort

bp = Blueprint("webhooks", __name__)

# ===== Servicios opcionales =====
try:
    from app.services import clerk_svc
except Exception:
    clerk_svc = None  # type: ignore

try:
    from app.services.stripe_svc import init_stripe as svc_init_stripe, set_subscription_quantity
except Exception:
    svc_init_stripe = None
    set_subscription_quantity = None  # type: ignore

# ===== Svix (Clerk) opcional =====
try:
    from svix.webhooks import Webhook, WebhookVerificationError
    _svix_available = True
except Exception:
    Webhook = None  # type: ignore
    WebhookVerificationError = Exception  # type: ignore
    _svix_available = False


# ---------- STRIPE HELPERS ----------

def _init_stripe():
    if svc_init_stripe:
        svc_init_stripe()
        return
    key = current_app.config.get("STRIPE_SECRET_KEY")
    if not key:
        abort(500, "STRIPE_SECRET_KEY not configured")
    stripe.api_key = key

def _target_from_metadata(md: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    if not md:
        return None, None
    etype = md.get("entity_type")
    eid = md.get("entity_id")
    if etype and eid:
        return etype, eid
    if md.get("clerk_org_id"):
        return "org", md.get("clerk_org_id")
    if md.get("clerk_user_id"):
        return "user", md.get("clerk_user_id")
    return None, None

def _resolve_target(session: Optional[dict], sub: Optional[dict], customer: Optional[dict]) -> Tuple[Optional[str], Optional[str]]:
    if sub:
        et, ei = _target_from_metadata(sub.get("metadata") or {})
        if et and ei:
            return et, ei
    if session:
        et, ei = _target_from_metadata((session.get("metadata") or {}))
        if et and ei:
            return et, ei
    if customer:
        et, ei = _target_from_metadata((customer.get("metadata") or {}))
        if et and ei:
            return et, ei
    return None, None

def _subscription_item_id(sub: dict) -> Optional[str]:
    try:
        return sub["items"]["data"][0]["id"]
    except Exception:
        return None

def _update_clerk_for_subscription(entity_type: str, entity_id: str, sub: dict, price_id_hint: Optional[str] = None):
    # Sin servicio declarado; no hacemos nada pero confirmamos recepción
    if not clerk_svc:
        return
    status = sub.get("status")
    item = (sub.get("items", {}).get("data") or [{}])[0]
    price = (item.get("price") or {})
    price_id = price.get("id") or price_id_hint

    payload_public = {}
    if status in {"active", "trialing"}:
        payload_public["plan"] = "enterprise" if entity_type == "org" else "pro"
    else:
        payload_public["plan"] = "free"

    payload_private = {
        "billing": {
            "stripeCustomerId": sub.get("customer"),
            "subscriptionId": sub.get("id"),
            "subscriptionItemId": _subscription_item_id(sub),
            "status": status,
            "planPriceId": price_id,
        }
    }

    if entity_type == "org":
        clerk_svc.update_org_metadata(entity_id, public=payload_public, private=payload_private)
    else:
        clerk_svc.update_user_metadata(entity_id, public=payload_public, private=payload_private)


# ---------- STRIPE WEBHOOKS (coherente en /api) + ALIAS legacy ----------

def _handle_stripe_webhook():
    _init_stripe()
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        return jsonify({"error": "STRIPE_WEBHOOK_SECRET not configured"}), 500

    payload = request.get_data()  # raw bytes
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except Exception as e:
        return jsonify({"error": f"invalid_signature: {e}"}), 400

    etype = event["type"]
    obj = event["data"]["object"]

    # checkout.session.completed → obtener sub y target
    if etype == "checkout.session.completed":
        session = obj
        sub_id = session.get("subscription")
        price_id = (session.get("metadata") or {}).get("price_id")
        cust_id = session.get("customer")
        sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"]) if sub_id else None
        customer = stripe.Customer.retrieve(cust_id) if cust_id else None
        entity_type, entity_id = _resolve_target(session=session, sub=sub, customer=customer)
        if entity_type and entity_id and sub:
            _update_clerk_for_subscription(entity_type, entity_id, sub, price_id_hint=price_id)
        return jsonify({"received": True})

    # Cambios de suscripción
    if etype in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
        sub = obj
        cust = stripe.Customer.retrieve(sub.get("customer"))
        entity_type, entity_id = _resolve_target(session=None, sub=sub, customer=cust)
        if entity_type and entity_id:
            _update_clerk_for_subscription(entity_type, entity_id, sub)
        return jsonify({"received": True})

    # Falla de pago → marcar estado
    if etype == "invoice.payment_failed":
        inv = obj
        sub_id = inv.get("subscription")
        if sub_id:
            sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
            cust = stripe.Customer.retrieve(sub.get("customer"))
            entity_type, entity_id = _resolve_target(session=None, sub=sub, customer=cust)
            if entity_type and entity_id and clerk_svc:
                private = {"billing": {"status": sub.get("status"), "lastPaymentFailed": True}}
                if entity_type == "org":
                    clerk_svc.update_org_metadata(entity_id, private=private)
                else:
                    clerk_svc.update_user_metadata(entity_id, private=private)
        return jsonify({"received": True})

    # Otros eventos que no manejamos explícitamente
    return jsonify({"ignored": True})

# Nueva ruta coherente
@bp.post("/api/stripe")
def stripe_webhook_api():
    return _handle_stripe_webhook()

# Alias legacy (retrocompatibilidad)
@bp.post("/stripe")
def stripe_webhook_legacy():
    return _handle_stripe_webhook()


# ---------- CLERK (SVIX) WEBHOOK (coherente en /api) + ALIAS legacy ----------

def _handle_clerk_webhook():
    if not _svix_available:
        return jsonify({"error": "svix not installed"}), 500
    secret = current_app.config.get("CLERK_WEBHOOK_SECRET")
    if not secret:
        return jsonify({"error": "CLERK_WEBHOOK_SECRET not configured"}), 500

    # clerk_svc opcional: si no existe, igualmente respondemos 200 para ack
    payload = request.get_data()  # raw bytes, NO usar get_json() antes
    svix_headers = {
        "svix-id": request.headers.get("svix-id", ""),
        "svix-timestamp": request.headers.get("svix-timestamp", ""),
        "svix-signature": request.headers.get("svix-signature", ""),
    }
    try:
        event = Webhook(secret).verify(payload, svix_headers)  # type: ignore
    except WebhookVerificationError as e:  # type: ignore
        current_app.logger.warning(f"Clerk webhook signature failed: {e}")
        return jsonify({"error": "invalid signature"}), 400
    except Exception as e:
        current_app.logger.exception(f"Clerk webhook error: {e}")
        return jsonify({"error": "bad request"}), 400

    evt_type = event.get("type")
    data = event.get("data", {})
    evt_id = event.get("id")
    current_app.logger.info(f"Clerk evt {evt_type} ({evt_id})")

    # Sincroniza seats con Stripe cuando cambia membership
    if clerk_svc and evt_type in ("organizationMembership.created", "organizationMembership.deleted"):
        try:
            org_id = data["organization"]["id"]
            org = clerk_svc.get_org(org_id)
            members_count = int((org.get("members_count") or 0))
            billing = (org.get("private_metadata") or {}).get("billing", {}) or {}
            item_id = billing.get("subscriptionItemId")
            if item_id and set_subscription_quantity:
                set_subscription_quantity(item_id, members_count)
                clerk_svc.update_org_metadata(org_id, private={"billing": {**billing, "seatCount": members_count}})
        except Exception as e:
            # Logueamos pero devolvemos 200 para que Clerk no reintente en bucle
            current_app.logger.exception(f"handler error for {evt_type}: {e}")

    # ACK rápido
    return jsonify({"ok": True}), 200

# Nueva ruta coherente
@bp.post("/api/clerk")
def clerk_webhook_api():
    return _handle_clerk_webhook()

# Alias legacy (retrocompatibilidad)
@bp.post("/clerk")
def clerk_webhook_legacy():
    return _handle_clerk_webhook()
