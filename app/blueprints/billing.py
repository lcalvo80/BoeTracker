from __future__ import annotations

import os
import stripe
from flask import Blueprint, request, jsonify, current_app, g

from app.auth import require_clerk_auth
from app.services import clerk_svc
from app.services.stripe_svc import (
    init_stripe,
    create_checkout_session,
    create_billing_portal,
)

# Exponemos las rutas oficiales bajo /api
bp = Blueprint("billing", __name__, url_prefix="/api")


# ───────────────────── helpers ─────────────────────
def _cfg(k: str, default: str | None = None) -> str:
    v = current_app.config.get(k)
    if v is None or str(v).strip() == "":
        v = os.getenv(k, default)
    return "" if v is None else str(v)


def _frontend_base() -> str:
    return (_cfg("FRONTEND_URL", "http://localhost:5173") or "").rstrip("/")


def _ensure_customer_for_user(user_id: str) -> str:
    """
    Busca/crea Customer en Stripe y lo guarda en Clerk.private_metadata.billing.stripeCustomerId.
    """
    u = clerk_svc.get_user(user_id)
    priv = (u.get("private_metadata") or {})
    existing = (priv.get("billing") or {}).get("stripeCustomerId") or priv.get("stripe_customer_id")
    if existing:
        return existing

    # Email + nombre “amigables”
    email = None
    try:
        emails = u.get("email_addresses") or []
        primary_id = u.get("primary_email_address_id")
        primary = next((e for e in emails if e.get("id") == primary_id), emails[0] if emails else None)
        email = primary.get("email_address") if primary else None
    except Exception:
        pass
    name = " ".join(filter(None, [u.get("first_name"), u.get("last_name")])) or u.get("username") or u.get("id")

    cust = stripe.Customer.create(email=email, name=name, metadata={"clerk_user_id": user_id})
    clerk_svc.update_user_metadata(user_id, private={"billing": {"stripeCustomerId": cust.id}})
    return cust.id


# ───────────────────── Checkout ─────────────────────
@bp.post("/checkout")
@bp.post("/billing/checkout")  # ← compat estable con FE que lo llama así
@require_clerk_auth
def checkout():
    """
    Crea una Checkout Session (suscripción de usuario).
    Body: { price_id?: string, quantity?: number }
    """
    init_stripe()

    body = request.get_json(silent=True) or {}
    price_id = (body.get("price_id") or _cfg("PRICE_PRO_MONTHLY_ID") or "").strip()
    if not price_id:
        return jsonify(error="price_id required or PRICE_PRO_MONTHLY_ID missing"), 400

    try:
        quantity = int(body.get("quantity") or 1)
    except Exception:
        quantity = 1
    if quantity < 1:
        quantity = 1

    user_id = g.clerk["user_id"]
    customer_id = _ensure_customer_for_user(user_id)

    success_url = _cfg("CHECKOUT_SUCCESS_URL") or f"{_frontend_base()}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = _cfg("CHECKOUT_CANCEL_URL")  or f"{_frontend_base()}/pricing?canceled=1"

    meta = {"entity_type": "user", "entity_id": user_id, "plan_scope": "user", "price_id": price_id}
    try:
        session = create_checkout_session(
            customer_id=customer_id,
            price_id=price_id,
            quantity=quantity,
            meta=meta,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return jsonify(checkout_url=session.url), 200
    except stripe.error.StripeError as e:
        return jsonify(error=f"Stripe error: {e}"), 400
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception:
        current_app.logger.exception("checkout failed")
        return jsonify(error="Unexpected error"), 500


# ───────────────────── Billing Portal ─────────────────────
@bp.post("/portal")
@bp.post("/billing/portal")  # ← compat estable
@require_clerk_auth
def portal():
    """Crea sesión del Billing Portal para el usuario actual."""
    init_stripe()
    user_id = g.clerk["user_id"]
    customer_id = _ensure_customer_for_user(user_id)
    try:
        ps = create_billing_portal(customer_id, return_url=f"{_frontend_base()}/account")
        return jsonify(portal_url=ps.url), 200
    except stripe.error.StripeError as e:
        return jsonify(error=f"Stripe error: {e}"), 400
    except Exception:
        current_app.logger.exception("portal failed")
        return jsonify(error="Unexpected error"), 500


# ───────────────────── Sync manual tras success ─────────────────────
@bp.post("/sync")
@bp.post("/billing/sync")  # ← compat estable
@require_clerk_auth
def sync_after_success():
    """
    Fallback manual tras éxito de Checkout.
    Body: { "session_id": "cs_..." }
    """
    init_stripe()
    b = request.get_json(silent=True) or {}
    sid = (b.get("session_id") or "").strip()
    if not sid:
        return jsonify(error="session_id is required"), 400
    try:
        sess = stripe.checkout.Session.retrieve(
            sid,
            expand=["subscription", "subscription.items.data.price"]
        )
        sub = sess.get("subscription") or {}
        status = sub.get("status") or "active"
        plan = "pro" if status in ("active", "trialing", "past_due") else "free"
        user_id = g.clerk["user_id"]
        priv = {"billing": {
            "stripeCustomerId": sess.get("customer"),
            "subscriptionId": sub.get("id"),
            "status": status
        }}
        clerk_svc.set_user_plan(user_id, plan=plan, status=status, extra_private=priv)
        return jsonify(ok=True, plan=plan), 200
    except stripe.error.StripeError as e:
        return jsonify(error=f"Stripe error: {e}"), 400
    except Exception:
        current_app.logger.exception("sync failed")
        return jsonify(error="Unexpected error"), 500
