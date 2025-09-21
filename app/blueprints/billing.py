# app/blueprints/billing.py
from __future__ import annotations

import os
import logging
from functools import wraps
from typing import Any, Optional

import stripe
from flask import Blueprint, request, jsonify, current_app, g, has_app_context

bp = Blueprint("billing", __name__, url_prefix="/api")

# ───────── helpers de config ─────────
def _cfg(k: str, default: Optional[str] = None) -> Optional[str]:
    """
    Lee config de Flask si hay app context; si no, cae a variables de entorno.
    Nunca debe explotar fuera de contexto.
    """
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
    Decorador perezoso (lazy) seguro fuera de contexto:
    - Si DISABLE_AUTH=1 -> bypass.
    - Si existe app.auth.require_clerk_auth -> aplicarlo dinámicamente.
    - Si no, no-op con logging estándar (nunca usa current_app fuera de contexto).
    """
    @wraps(fn)
    def _wrapped(*args, **kwargs):
        if _truthy(_cfg("DISABLE_AUTH", "0") or "0"):
            # Bypass: en dev no exigimos token, pero llenamos g.clerk mínimo para coherencia
            g.clerk = {"user_id": "dev_user", "org_id": None, "email": "dev@example.com", "name": "Dev User", "raw_claims": None}
            return fn(*args, **kwargs)
        try:
            # Import relativo preferido; si no funciona, probar absoluto.
            try:
                from ..auth import require_clerk_auth as real_guard
            except Exception:
                from app.auth import require_clerk_auth as real_guard  # fallback si el paquete se importa como "app"
        except Exception as e:
            logging.getLogger("billing").warning("Auth decorator no disponible: %s", e)
            return fn(*args, **kwargs)
        # Aplicar el decorador real dinámicamente en cada llamada
        return real_guard(fn)(*args, **kwargs)
    return _wrapped


# ───────── Clerk svc (opcional, si no está no rompemos) ─────────
try:
    # Import relativo, más robusto dentro del paquete
    from ..services import clerk_svc  # type: ignore
except Exception:
    try:
        from app.services import clerk_svc  # type: ignore
    except Exception:
        clerk_svc = None  # type: ignore


def _ensure_customer_for_user(user_id: str) -> str:
    # Si tenemos clerk_svc, intentamos reusar/guardar customerId
    if clerk_svc:
        try:
            u = clerk_svc.get_user(user_id)
            priv = (u.get("private_metadata") or {})
            existing = (priv.get("billing") or {}).get("stripeCustomerId") or priv.get("stripe_customer_id")
            if existing:
                return existing
            # crear
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
            try:
                clerk_svc.update_user_metadata(user_id, private={"billing": {"stripeCustomerId": customer.id}})
            except Exception:
                # Estamos dentro de request context; logger seguro
                current_app.logger.warning("No se pudo persistir stripeCustomerId en Clerk (continuamos).")
            return customer.id
        except Exception:
            current_app.logger.exception("ensure_customer_for_user via Clerk falló; creamos Customer sin Clerk")

    # Sin clerk_svc → crear mínimo viable
    customer = stripe.Customer.create(metadata={"clerk_user_id": user_id})
    return customer.id


# ───────── Endpoints ─────────

@bp.post("/checkout", endpoint="billing.create_checkout")
@_require_auth
def create_checkout():
    """
    Crea Stripe Checkout (suscripción)
    Body: { price_id?: string, quantity?: number }
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

    # user_id desde g.clerk si hay auth; si no, “dev_user” (bypass)
    user_id = getattr(g, "clerk", {}).get("user_id") or "dev_user"
    customer_id = _ensure_customer_for_user(user_id)

    success_url = _cfg("CHECKOUT_SUCCESS_URL") or f"{_front_base()}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = _cfg("CHECKOUT_CANCEL_URL") or f"{_front_base()}/pricing?canceled=1"

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": quantity}],
        success_url=success_url,
        cancel_url=cancel_url,
        allow_promotion_codes=True,
        subscription_data={"metadata": {"entity_type": "user", "entity_id": user_id, "plan_scope": "user"}},
        metadata={"entity_type": "user", "entity_id": user_id, "plan_scope": "user", "price_id": price_id},
        tax_id_collection={"enabled": True},
        locale=_cfg("STRIPE_CHECKOUT_LOCALE", "auto") or "auto",
        automatic_tax={"enabled": _truthy(_cfg("STRIPE_AUTOMATIC_TAX", "true") or "true")},
        billing_address_collection=("required" if _truthy(_cfg("STRIPE_REQUIRE_BILLING_ADDRESS", "true") or "true") else "auto"),
        customer_update={
            "address": "auto" if _truthy(_cfg("STRIPE_SAVE_ADDRESS_AUTO", "true") or "true") else "none",
            "name": "auto" if _truthy(_cfg("STRIPE_SAVE_NAME_AUTO", "true") or "true") else "none",
        },
    )
    return jsonify(checkout_url=session.url), 200


@bp.post("/portal", endpoint="billing.create_portal")
@_require_auth
def create_portal():
    _, err = _init_stripe()
    if err:
        return err
    user_id = getattr(g, "clerk", {}).get("user_id") or "dev_user"
    customer_id = _ensure_customer_for_user(user_id)
    ps = stripe.billing_portal.Session.create(customer=customer_id, return_url=f"{_front_base()}/account")
    return jsonify(portal_url=ps.url), 200


@bp.post("/sync", endpoint="billing.sync_after_success")
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

    user_id = getattr(g, "clerk", {}).get("user_id") or "dev_user"
    if clerk_svc:
        try:
            priv = {"billing": {
                "stripeCustomerId": sess.get("customer"),
                "subscriptionId": sub.get("id"),
                "status": status,
                "planPriceId": price
            }}
            plan = "pro" if status in ("active", "trialing", "past_due") else "free"
            # Método helper opcional que sugerimos en tu clerk_svc
            if hasattr(clerk_svc, "set_user_plan"):
                clerk_svc.set_user_plan(user_id, plan=plan, status=status, extra_private=priv)
            else:
                clerk_svc.update_user_metadata(user_id, public={"plan": plan}, private=priv)
        except Exception:
            current_app.logger.exception("No se pudo actualizar Clerk en /billing/sync (continuamos).")

    return jsonify(ok=True), 200
