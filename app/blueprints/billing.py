# app/blueprints/billing.py
from __future__ import annotations
import os
import stripe
from flask import Blueprint, request, jsonify, current_app, g
from app.auth import require_clerk_auth
from app.services import clerk_svc

# Todas las rutas bajo /api, como antes
bp = Blueprint("billing", __name__, url_prefix="/api")

def _cfg(k: str, default: str | None = None) -> str | None:
    v = current_app.config.get(k)
    if v is None or str(v).strip() == "":
        v = os.getenv(k, default)
    return None if v is None else str(v)

def _init_stripe():
    sk = _cfg("STRIPE_SECRET_KEY", "")
    if not sk:
        return None, (jsonify(error="STRIPE_SECRET_KEY missing"), 500)
    stripe.api_key = sk
    return sk, None

def _front_base() -> str:
    return (_cfg("FRONTEND_URL", "http://localhost:5173") or "").rstrip("/")

def _ensure_customer_for_user(user_id: str) -> str:
    # 1) ¿el user tiene customer ya?
    u = clerk_svc.get_user(user_id)
    priv = (u.get("private_metadata") or {})
    existing = (priv.get("billing") or {}).get("stripeCustomerId") or priv.get("stripe_customer_id")
    if existing:
        return existing
    # 2) crear uno nuevo
    email = None
    try:
        emails = u.get("email_addresses") or []
        primary_id = u.get("primary_email_address_id")
        primary = next((e for e in emails if e.get("id") == primary_id), emails[0] if emails else None)
        email = primary.get("email_address") if primary else None
    except Exception:
        pass
    name = " ".join(filter(None, [u.get("first_name"), u.get("last_name")])) or u.get("username") or u.get("id")
    customer = stripe.Customer.create(email=email, name=name, metadata={"clerk_user_id": user_id})
    clerk_svc.update_user_metadata(user_id, private={"billing": {"stripeCustomerId": customer.id}})
    return customer.id

# 👇 Recuperamos los nombres de endpoint legacy con endpoint="create_checkout"
@bp.post("/checkout", endpoint="create_checkout")
@require_clerk_auth
def checkout():
    """
    Crea una Checkout Session (suscripción).
    Body: { price_id?: string, quantity?: number }
    """
    _, err = _init_stripe()
    if err: return err

    b = request.get_json(silent=True) or {}
    price_id = (b.get("price_id") or _cfg("STRIPE_PRICE_ID") or "").strip()
    if not price_id:
        return jsonify(error="price_id required or STRIPE_PRICE_ID missing"), 400
    try:
        quantity = int(b.get("quantity") or 1)
    except Exception:
        quantity = 1
    if quantity < 1: quantity = 1

    user_id = g.clerk["user_id"]
    customer_id = _ensure_customer_for_user(user_id)

    success_url = _cfg("CHECKOUT_SUCCESS_URL") or f"{_front_base()}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = _cfg("CHECKOUT_CANCEL_URL")  or f"{_front_base()}/pricing?canceled=1"

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": quantity}],
        success_url=success_url,
        cancel_url=cancel_url,
        allow_promotion_codes=True,
        subscription_data={"metadata": {"entity_type": "user", "entity_id": user_id, "plan_scope": "user"}},
        metadata={"entity_type": "user", "entity_id": user_id, "plan_scope": "user", "price_id": price_id},
    )
    return jsonify(checkout_url=session.url), 200

# 👇 Restauramos nombre legacy con endpoint="create_portal"
@bp.post("/portal", endpoint="create_portal")
@require_clerk_auth
def portal():
    """Crea una Billing Portal session para el usuario actual."""
    _, err = _init_stripe()
    if err: return err
    user_id = g.clerk["user_id"]
    customer_id = _ensure_customer_for_user(user_id)
    ps = stripe.billing_portal.Session.create(customer=customer_id, return_url=f"{_front_base()}/account")
    return jsonify(portal_url=ps.url), 200

# 👇 Restauramos nombre legacy con endpoint="sync_after_success"
@bp.post("/sync", endpoint="sync_after_success")
@require_clerk_auth
def sync_after_success():
    """
    Fallback manual: Body { "session_id": "cs_..." }
    Lee la session y marca plan/status en Clerk como hace el webhook.
    """
    _, err = _init_stripe()
    if err: return err
    b = request.get_json(silent=True) or {}
    sid = (b.get("session_id") or "").strip()
    if not sid:
        return jsonify(error="session_id is required"), 400

    sess = stripe.checkout.Session.retrieve(sid, expand=["subscription", "subscription.items.data.price"])
    sub = sess.get("subscription")
    status = sub.get("status") if sub else "active"
    price = None
    try:
        price = sub["items"]["data"][0]["price"]["id"]
    except Exception:
        pass

    user_id = g.clerk["user_id"]
    priv = {"billing": {"stripeCustomerId": sess.get("customer"), "subscriptionId": sub.get("id") if sub else None, "status": status, "planPriceId": price}}
    clerk_svc.set_user_plan(user_id, plan=("pro" if status in ("active","trialing","past_due") else "free"), status=status, extra_private=priv)
    return jsonify(ok=True), 200
