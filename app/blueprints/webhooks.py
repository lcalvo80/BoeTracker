# app/blueprints/webhooks.py
from __future__ import annotations

import stripe
from typing import Optional, Tuple, Dict, Any
from flask import Blueprint, request, jsonify, current_app, abort

bp = Blueprint("webhooks", __name__)

# Imports de servicios (opcionales pero recomendados)
try:
    from app.services import clerk_svc
except Exception:
    clerk_svc = None  # type: ignore

try:
    from app.services.stripe_svc import init_stripe as svc_init_stripe, set_subscription_quantity
except Exception:
    svc_init_stripe = None
    set_subscription_quantity = None  # type: ignore

def _init_stripe():
    if svc_init_stripe:
        svc_init_stripe()
        return
    key = current_app.config.get("STRIPE_SECRET_KEY")
    if not key:
        abort(500, "STRIPE_SECRET_KEY not configured")
    stripe.api_key = key

# ---------- Utilidades para identificar el target (user/org) ----------

def _target_from_metadata(md: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    if not md:
        return None, None
    # Preferimos entity_type/entity_id; si no, clerk_*_id
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
    # 1) metadata en subscription
    if sub:
        et, ei = _target_from_metadata(sub.get("metadata") or {})
        if et and ei:
            return et, ei
    # 2) metadata en session (checkout.session.completed)
    if session:
        et, ei = _target_from_metadata((session.get("metadata") or {}))
        if et and ei:
            return et, ei
    # 3) metadata en customer
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

# ---------- Patch helpers (Clerk) ----------

def _update_clerk_for_subscription(entity_type: str, entity_id: str, sub: dict, price_id_hint: Optional[str] = None):
    if not clerk_svc:
        # Sin servicio declarado; no hacemos nada pero confirmamos recepción
        return
    status = sub.get("status")
    item = (sub.get("items", {}).get("data") or [{}])[0]
    price = (item.get("price") or {})
    price_id = price.get("id") or price_id_hint
    payload_public = {}
    # Tilde de planes según estado (ajusta a tu lógica)
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

# ---------- Stripe webhook ----------

@bp.post("/stripe")
def stripe_webhook():
    _init_stripe()
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        abort(500, "STRIPE_WEBHOOK_SECRET not configured")

    payload = request.get_data()
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
        # customer sin expand viene como id
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
                # Añadimos flag en private.billing
                private = {"billing": {"status": sub.get("status"), "lastPaymentFailed": True}}
                if entity_type == "org":
                    clerk_svc.update_org_metadata(entity_id, private=private)
                else:
                    clerk_svc.update_user_metadata(entity_id, private=private)
        return jsonify({"received": True})

    return jsonify({"ignored": True})

# ---------- Clerk (Svix) webhook opcional para seats enterprise ----------

try:
    from svix.webhooks import Webhook

    @bp.post("/clerk")
    def clerk_webhook():
        if not current_app.config.get("CLERK_WEBHOOK_SECRET"):
            return jsonify({"error": "CLERK_WEBHOOK_SECRET not configured"}), 500
        if not clerk_svc:
            return jsonify({"error": "clerk_svc not available"}), 500

        payload = request.get_data()
        headers = dict(request.headers)
        wh = Webhook(current_app.config["CLERK_WEBHOOK_SECRET"])
        try:
            event = wh.verify(payload, headers)
        except Exception:
            return jsonify({"error": "invalid_signature"}), 400

        type_ = event["type"]
        data  = event["data"]

        # Sincronizamos seats con Stripe cuando cambia el membership
        if type_ in ("organizationMembership.created", "organizationMembership.deleted"):
            org_id = data["organization"]["id"]
            org = clerk_svc.get_org(org_id)
            members_count = int((org.get("members_count") or 0))
            billing = (org.get("private_metadata") or {}).get("billing", {})
            item_id = billing.get("subscriptionItemId")
            if item_id and set_subscription_quantity:
                set_subscription_quantity(item_id, members_count)
                clerk_svc.update_org_metadata(org_id, private={"billing": {**billing, "seatCount": members_count}})
        return jsonify({"ok": True})
except Exception:
    # svix no instalado; omitimos el endpoint /clerk
    pass
