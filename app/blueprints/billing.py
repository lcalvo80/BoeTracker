# app/blueprints/billing.py
from __future__ import annotations

import os
import stripe
from typing import Tuple
from functools import wraps
from flask import Blueprint, request, jsonify, current_app, g

bp = Blueprint("billing", __name__)

# ───────────────────────── Utils ─────────────────────────
def _truthy(v) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")

def _conf(key: str, default: str | None = None) -> str | None:
    """
    Lee primero de current_app.config y si falta, de ENV.
    Devuelve default si no hay valor.
    """
    try:
        val = current_app.config.get(key)
        if val is not None and str(val).strip() != "":
            return str(val)
    except Exception:
        pass
    envv = os.getenv(key)
    if envv is not None and str(envv).strip() != "":
        return str(envv)
    return default

def _json_error(detail: str, status: int):
    return jsonify(detail=detail), status

# ───────────────────────── Auth (robusta) ─────────────────────────
_DISABLE_AUTH_ENV = _truthy(os.getenv("DISABLE_AUTH", "0"))

def _noop_decorator(fn):
    @wraps(fn)
    def _w(*a, **k): 
        return fn(*a, **k)
    return _w

def _load_auth_guard():
    disabled = _DISABLE_AUTH_ENV
    try:
        disabled = _truthy(current_app.config.get("DISABLE_AUTH", disabled))
    except Exception:
        pass
    if disabled:
        return _noop_decorator
    try:
        from app.auth import require_clerk_auth as real_guard
        return real_guard
    except Exception as e:
        # Sin bypass y no podemos cargar auth → sube error para ver en logs
        raise

def _require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        guard = _load_auth_guard()
        protected = guard(fn)
        return protected(*args, **kwargs)
    return wrapper

# ───────────────────────── Stripe helpers ─────────────────────────
def _init_stripe() -> tuple[bool, tuple]:
    """Inicializa la API key de Stripe desde config/ENV. Devuelve (ok, error_response?)."""
    key = _conf("STRIPE_SECRET_KEY", "")
    if not key:
        return False, _json_error("Stripe secret key (STRIPE_SECRET_KEY) is missing", 500)
    stripe.api_key = key
    return True, ()

def _clerk_target(is_org: bool) -> tuple[bool, tuple] | tuple[str, str]:
    """
    Extrae (entity_type, entity_id) desde g.clerk.
    - is_org=True → requiere org_id.
    - is_org=False → requiere user_id.
    Responde (False, json_error) si falta.
    """
    data = getattr(g, "clerk", {}) or {}
    entity_type = "org" if is_org else "user"
    entity_id = data.get("org_id") if is_org else data.get("user_id")
    if not entity_id:
        return False, _json_error(
            "Missing org_id in token" if is_org else "Missing user_id in token", 400
        )
    return entity_type, str(entity_id)

def _maybe_contact_from_token() -> dict:
    """
    Intenta sacar email/name desde g.clerk si tu decorador los adjunta.
    No es obligatorio, pero ayuda a que el Customer esté más completo.
    """
    data = getattr(g, "clerk", {}) or {}
    out = {}
    if data.get("email"):
        out["email"] = str(data["email"])
    name = data.get("name") or data.get("full_name")
    if name:
        out["name"] = str(name)
    return out

def _ensure_customer(entity_type: str, entity_id: str, is_org: bool) -> str:
    """
    Busca o crea un Customer en Stripe. Metadata compatible:
      - entity_type/entity_id
      - clerk_user_id/clerk_org_id
    """
    # 1) Buscar por metadata (si tu cuenta tiene Customer Search)
    try:
        query = f"metadata['entity_type']:'{entity_type}' AND metadata['entity_id']:'{entity_id}'"
        res = stripe.Customer.search(query=query, limit=1)
        if res.data:
            return res.data[0].id
    except Exception:
        pass

    # 2) Crear
    contact = _maybe_contact_from_token()
    metadata = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        ("clerk_org_id" if is_org else "clerk_user_id"): entity_id,
    }
    cust = stripe.Customer.create(metadata=metadata, **contact)
    return cust.id

def _frontend_base() -> str:
    """Base URL de frontend para construir success/return URLs."""
    return (_conf("FRONTEND_URL", "http://localhost:5173") or "").rstrip("/")

def _resolve_price_id(is_org: bool, price_id_hint: str | None) -> str:
    """
    Devuelve el price_id efectivo. Si no llega en body se usa el default del servidor:
    - is_org=False → PRICE_PRO_MONTHLY_ID
    - is_org=True  → PRICE_ENTERPRISE_SEAT_ID
    Lee de config o ENV.
    """
    pid = (price_id_hint or "").strip()
    if pid:
        return pid
    if is_org:
        return (_conf("PRICE_ENTERPRISE_SEAT_ID", "") or "").strip()
    return (_conf("PRICE_PRO_MONTHLY_ID", "") or "").strip()

# ───────────────────────── Endpoints ─────────────────────────
@bp.post("/checkout")
@_require_auth
def create_checkout():
    """
    Crea una sesión de Stripe Checkout (modo suscripción).
    Body JSON:
      - price_id (string, opcional si hay default en servidor → "price_...")
      - is_org (bool, default False)
      - quantity (int, default 1)
    Respuestas JSON (nunca HTML).
    """
    body = request.get_json(force=True, silent=True) or {}
    is_org = bool(body.get("is_org", False))

    # Normaliza cantidad
    try:
        quantity = int(body.get("quantity") or 1)
    except Exception:
        quantity = 1
    if quantity < 1:
        quantity = 1

    price_id = _resolve_price_id(is_org=is_org, price_id_hint=body.get("price_id"))

    if not price_id:
        # Mismo mensaje que estáis viendo, pero ahora en JSON:
        return _json_error("Missing price_id and no default price configured on server", 400)
    if price_id.startswith("prod_"):
        return _json_error("price_id looks like a product id (prod_...). Use a price id (price_...)", 400)

    ok, err = _init_stripe()
    if not ok:
        return err  # (json, 500)

    clerk_target = _clerk_target(is_org=is_org)
    if isinstance(clerk_target, tuple) and clerk_target and clerk_target[0] is False:
        # Error JSON ya preparado
        return clerk_target[1]
    entity_type, entity_id = clerk_target  # type: ignore

    try:
        customer_id = _ensure_customer(entity_type, entity_id, is_org=is_org)
    except stripe.error.StripeError as e:
        return _json_error(f"Stripe error creating/retrieving customer: {str(e)}", 400)
    except Exception as e:
        return _json_error(f"Error creating/retrieving customer: {str(e)}", 500)

    frontend = _frontend_base()
    if not frontend:
        return _json_error("FRONTEND_URL is not configured", 500)

    success_url = f"{frontend}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{frontend}/pricing?canceled=1"

    # Ajustes de idioma / impuestos (tuneables por ENV/config):
    locale = _conf("STRIPE_CHECKOUT_LOCALE", "auto") or "auto"
    automatic_tax = _truthy(_conf("STRIPE_AUTOMATIC_TAX", "true") or "true")
    require_billing_addr = _truthy(_conf("STRIPE_REQUIRE_BILLING_ADDRESS", "true") or "true")
    save_addr_auto = _truthy(_conf("STRIPE_SAVE_ADDRESS_AUTO", "true") or "true")
    save_name_auto = _truthy(_conf("STRIPE_SAVE_NAME_AUTO", "true") or "true")

    session_metadata = {
        "price_id": price_id,
        "plan_scope": "org" if is_org else "user",
        "clerk_user_id": entity_id if not is_org else "",
        "clerk_org_id": entity_id if is_org else "",
        "entity_type": entity_type,
        "entity_id": entity_id,
    }

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": quantity}],
            allow_promotion_codes=True,
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=entity_id,
            metadata=session_metadata,
            subscription_data={"metadata": session_metadata},
            # UX/Tax
            tax_id_collection={"enabled": True},
            locale=locale,
            automatic_tax={"enabled": automatic_tax},
            billing_address_collection=("required" if require_billing_addr else "auto"),
            customer_update={
                "address": "auto" if save_addr_auto else "none",
                "name": "auto" if save_name_auto else "none",
            },
        )
        return jsonify(checkout_url=session.url), 200
    except stripe.error.StripeError as e:
        return _json_error(f"Stripe error: {str(e)}", 400)
    except Exception as e:
        return _json_error(f"Unexpected error creating checkout session: {str(e)}", 500)

@bp.post("/portal")
@_require_auth
def create_portal():
    """
    Crea una sesión del Billing Portal de Stripe para el Customer asociado.
    Body JSON (opcional):
      - is_org (bool, default False)
    Respuestas JSON (nunca HTML).
    """
    body = request.get_json(silent=True) or {}
    is_org = bool(body.get("is_org", False))

    ok, err = _init_stripe()
    if not ok:
        return err  # (json, 500)

    clerk_target = _clerk_target(is_org=is_org)
    if isinstance(clerk_target, tuple) and clerk_target and clerk_target[0] is False:
        return clerk_target[1]
    entity_type, entity_id = clerk_target  # type: ignore

    try:
        customer_id = _ensure_customer(entity_type, entity_id, is_org=is_org)
    except stripe.error.StripeError as e:
        return _json_error(f"Stripe error creating/retrieving customer: {str(e)}", 400)
    except Exception as e:
        return _json_error(f"Error creating/retrieving customer: {str(e)}", 500)

    frontend = _frontend_base()
    if not frontend:
        return _json_error("FRONTEND_URL is not configured", 500)

    try:
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{frontend}/billing",
        )
        return jsonify(portal_url=portal.url), 200
    except stripe.error.StripeError as e:
        return _json_error(f"Stripe error: {str(e)}", 400)
    except Exception as e:
        return _json_error(f"Unexpected error creating portal session: {str(e)}", 500)
