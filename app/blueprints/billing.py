# app/blueprints/billing.py
from __future__ import annotations

import os
import logging
from functools import wraps
from typing import Any, Optional

import stripe
from flask import Blueprint, request, jsonify, current_app, g, has_app_context
from app.integrations.stripe_utils import ensure_customer  # <-- importante

bp = Blueprint("billing", __name__, url_prefix="/api")

# ───────── helpers de config ─────────
def _cfg(k: str, default: Optional[str] = None) -> Optional[str]:
    v: Optional[Any] = None
    if has_app_context():
        try:
            v = current_app.config.get(k)
        except Exception:
            v = None
    if v is None or str(v).strip() == "":
        v = os.getenv(k, default)
    return None if v is None else str(v)

def _truthy(v: Any) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")

def _init_stripe():
    sk = _cfg("STRIPE_SECRET_KEY", "")
    if not sk:
        return None, (jsonify(error="STRIPE_SECRET_KEY missing"), 500)
    stripe.api_key = sk
    return sk, None

def _front_base() -> str:
    return (_cfg("FRONTEND_URL", "http://localhost:5173") or "").rstrip("/")

def _require_auth(fn):
    """
    Decorador perezoso:
    - DISABLE_AUTH=1 => bypass.
    - Si existe app.auth.require_clerk_auth -> aplícalo dinámicamente.
    - Si no, no-op con logging estándar.
    """
    @wraps(fn)
    def _wrapped(*args, **kwargs):
        if _truthy(_cfg("DISABLE_AUTH", "0") or "0"):
            g.clerk = {"user_id": "dev_user", "org_id": None, "email": "dev@example.com", "name": "Dev User", "raw_claims": None}
            return fn(*args, **kwargs)
        try:
            try:
                from ..auth import require_clerk_auth as real_guard
            except Exception:
                from app.auth import require_clerk_auth as real_guard
        except Exception as e:
            logging.getLogger("billing").warning("Auth decorator no disponible: %s", e)
            return fn(*args, **kwargs)
        return real_guard(fn)(*args, **kwargs)
    return _wrapped

# ───────── Endpoints ─────────

@bp.post("/checkout", endpoint="create_checkout")
@_require_auth
def create_checkout():
    """
    Crea una Stripe Checkout Session (suscripción).
    Body opcional:
      - price_id: str (si no, STRIPE_PRICE_ID)
      - quantity: int (>=1, por defecto 1)
      - entity_type: "user" | "org" (por defecto "user")
      - entity_id: id explícito (si no, se infiere del guard de Clerk)
      - plan_scope: "user" | "org" (alias del entity_type; por defecto = entity_type)
    """
    _, err = _init_stripe()
    if err:
        return err

    body = request.get_json(silent=True) or {}

    price_id = (body.get("price_id") or _cfg("STRIPE_PRICE_ID") or "").strip()
    if not price_id:
        return jsonify(error="price_id required or STRIPE_PRICE_ID missing"), 400

    try:
        quantity = int(body.get("quantity") or 1)
    except Exception:
        quantity = 1
    if quantity < 1:
        quantity = 1

    # ---- Identidad (user/org) ----
    entity_type = (body.get("entity_type") or "user").strip().lower()
    if entity_type not in ("user", "org"):
        entity_type = "user"

    clerk_ctx = getattr(g, "clerk", {}) or {}
    inferred_user_id = clerk_ctx.get("user_id")
    inferred_org_id = clerk_ctx.get("org_id")
    entity_id = (body.get("entity_id")
                 or (inferred_user_id if entity_type == "user" else inferred_org_id)
                 or "dev_user")

    plan_scope = (body.get("plan_scope") or entity_type).strip().lower()

    # ---- Customer coherente con entity ----
    try:
        customer_id = ensure_customer(entity_type, entity_id)
    except Exception:
        current_app.logger.exception("ensure_customer falló")
        return jsonify(error="cannot ensure stripe customer"), 500

    # ---- URLs ----
    success_url = _cfg("CHECKOUT_SUCCESS_URL") or f"{_front_base()}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = _cfg("CHECKOUT_CANCEL_URL") or f"{_front_base()}/pricing?canceled=1"

    # ---- Crear Session con metadatos NORMALIZADOS ----
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": quantity}],
            success_url=success_url,
            cancel_url=cancel_url,
            allow_promotion_codes=True,
            client_reference_id=f"{entity_type}:{entity_id}",
            metadata={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "plan_scope": plan_scope,
                "price_id": price_id,
            },
            subscription_data={
                "metadata": {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "plan_scope": plan_scope,
                }
            },
            tax_id_collection={"enabled": True},
            locale=_cfg("STRIPE_CHECKOUT_LOCALE", "auto") or "auto",
            automatic_tax={"enabled": _truthy(_cfg("STRIPE_AUTOMATIC_TAX", "true") or "true")},
            billing_address_collection=("required" if _truthy(_cfg("STRIPE_REQUIRE_BILLING_ADDRESS", "true") or "true") else "auto"),
            customer_update={
                "address": "auto" if _truthy(_cfg("STRIPE_SAVE_ADDRESS_AUTO", "true") or "true") else "none",
                "name": "auto" if _truthy(_cfg("STRIPE_SAVE_NAME_AUTO", "true") or "true") else "none",
            },
        )
    except Exception as e:
        current_app.logger.exception("Stripe Checkout Session.create error")
        return jsonify(error="stripe checkout error", detail=str(e)), 400

    return jsonify(checkout_url=session.url), 200


@bp.post("/portal", endpoint="create_portal")
@_require_auth
def create_portal():
    _, err = _init_stripe()
    if err:
        return err

    body = request.get_json(silent=True) or {}
    # Permite abrir portal para user u org si lo soportas en UI
    entity_type = (body.get("entity_type") or "user").strip().lower()
    if entity_type not in ("user", "org"):
        entity_type = "user"

    clerk_ctx = getattr(g, "clerk", {}) or {}
    inferred_user_id = clerk_ctx.get("user_id")
    inferred_org_id = clerk_ctx.get("org_id")
    entity_id = (body.get("entity_id")
                 or (inferred_user_id if entity_type == "user" else inferred_org_id)
                 or "dev_user")

    try:
        customer_id = ensure_customer(entity_type, entity_id)
    except Exception:
        current_app.logger.exception("ensure_customer falló en portal")
        return jsonify(error="cannot ensure stripe customer"), 500

    ps = stripe.billing_portal.Session.create(customer=customer_id, return_url=f"{_front_base()}/account")
    return jsonify(portal_url=ps.url), 200


@bp.post("/sync", endpoint="sync_after_success")
@_require_auth
def sync_after_success():
    _, err = _init_stripe()
    if err:
        return err
    b = request.get_json(silent=True) or {}
    sid = (b.get("session_id") or "").strip()
    if not sid:
        return jsonify(error="session_id is required"), 400

    sess = stripe.checkout.Session.retrieve(sid, expand=["subscription", "subscription.items.data.price"])
    sub = sess.get("subscription") or {}
    status = sub.get("status") or "active"
    price = None
    try:
        price = sub["items"]["data"][0]["price"]["id"]
    except Exception:
        pass

    # Inferimos usuario por el guard
    clerk_ctx = getattr(g, "clerk", {}) or {}
    user_id = clerk_ctx.get("user_id") or "dev_user"

    # Actualiza Clerk
    try:
        from app.services import clerk_svc
        priv = {"billing": {
            "stripeCustomerId": sess.get("customer"),
            "subscriptionId": sub.get("id"),
            "status": status,
            "planPriceId": price
        }}
        plan = "pro" if status in ("active", "trialing", "past_due") else "free"
        if hasattr(clerk_svc, "set_user_plan"):
            clerk_svc.set_user_plan(user_id, plan=plan, status=status, extra_private=priv)
        else:
            clerk_svc.update_user_metadata(user_id, public={"plan": plan}, private=priv)
    except Exception:
        current_app.logger.exception("No se pudo actualizar Clerk en /billing/sync (continuamos).")

    return jsonify(ok=True), 200
