# app/blueprints/billing.py
from __future__ import annotations

import os
import stripe
import json
import requests
from typing import Tuple
from functools import wraps
from flask import Blueprint, request, jsonify, current_app, g

bp = Blueprint("billing", __name__)

# ───────────────────────── Utils ─────────────────────────
def _truthy(v) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")

def _conf(key: str, default: str | None = None) -> str | None:
    """Lee primero de current_app.config y si falta, de ENV."""
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
    except Exception:
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
    data = getattr(g, "clerk", {}) or {}
    out = {}
    if data.get("email"):
        out["email"] = str(data["email"])
    name = data.get("name") or data.get("full_name")
    if name:
        out["name"] = str(name)
    return out

def _ensure_customer(entity_type: str, entity_id: str, is_org: bool) -> str:
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
    return (_conf("FRONTEND_URL", "http://localhost:5173") or "").rstrip("/")

def _resolve_price_id(is_org: bool, price_id_hint: str | None) -> str:
    pid = (price_id_hint or "").strip()
    if pid:
        return pid
    if is_org:
        return (_conf("PRICE_ENTERPRISE_SEAT_ID", "") or "").strip()
    return (_conf("PRICE_PRO_MONTHLY_ID", "") or "").strip()

# ───────────────────────── Clerk helpers ─────────────────────────
def _clerk_api_url(resource: str) -> str:
    """
    Construye la base de Clerk API en función de la instancia.
    Si usas .dev, la API también es api.clerk.com con la secret, no .dev.
    Puedes forzar base vía CLERK_API_URL si lo necesitas.
    """
    forced = _conf("CLERK_API_URL")
    if forced:
        return forced.rstrip("/") + resource
    # API pública de Clerk es api.clerk.com independientemente del tenant .dev o prod
    return "https://api.clerk.com" + resource

def _clerk_headers() -> dict:
    sk = _conf("CLERK_SECRET_KEY", "")
    if not sk:
        return {}
    return {
        "Authorization": f"Bearer {sk}",
        "Content-Type": "application/json",
    }

def _update_clerk_public_metadata(entity_type: str, entity_id: str, plan: str) -> tuple[bool, tuple]:
    """
    Actualiza public_metadata.plan en Clerk para user/org.
    plan: "free" | "pro" | "enterprise"
    """
    headers = _clerk_headers()
    if not headers:
        return False, _json_error("CLERK_SECRET_KEY is missing (server cannot update plan in Clerk)", 500)

    payload = {"public_metadata": {"plan": plan}}
    try:
        if entity_type == "user":
            url = _clerk_api_url(f"/v1/users/{entity_id}")
        else:
            url = _clerk_api_url(f"/v1/organizations/{entity_id}")
        r = requests.patch(url, headers=headers, data=json.dumps(payload), timeout=15)
        if r.status_code >= 400:
            return False, _json_error(f"Clerk update failed ({r.status_code}): {r.text}", 500)
        return True, ()
    except requests.RequestException as e:
        return False, _json_error(f"Clerk update error: {str(e)}", 500)

def _plan_from_scope(plan_scope: str | None) -> str:
    """
    Deriva el nombre de plan lógico a partir del scope guardado:
      - "org"  → "enterprise"
      - "user" → "pro"
    """
    if (plan_scope or "").lower() == "org":
        return "enterprise"
    return "pro"

def _plan_from_subscription(sub: stripe.Subscription) -> str:
    """
    Para suscripciones activas → plan según metadatos/price.
    Si está cancelada o sin items → 'free'.
    """
    if not sub or sub.get("status") not in ("active", "trialing", "past_due"):
        return "free"
    # Si guardaste metadata en subscription_data.metadata:
    md = (sub.get("metadata") or {})
    plan_scope = md.get("plan_scope")
    if plan_scope:
        return _plan_from_scope(plan_scope)
    # Fallback muy básico por si no hay metadata:
    try:
        items = sub.get("items", {}).get("data", [])
        if items:
            price = items[0].get("price", {})
            nick = (price.get("nickname") or "").lower()
            if "enterprise" in nick:
                return "enterprise"
    except Exception:
        pass
    return "pro"

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
        return _json_error("Missing price_id and no default price configured on server", 400)
    if price_id.startswith("prod_"):
        return _json_error("price_id looks like a product id (prod_...). Use a price id (price_...)", 400)

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
    """
    body = request.get_json(silent=True) or {}
    is_org = bool(body.get("is_org", False))

    ok, err = _init_stripe()
    if not ok:
        return err

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

# ───────────────────────── Webhook & Sync ─────────────────────────
@bp.post("/webhook")
def stripe_webhook():
    """
    Webhook de Stripe. Configura en Stripe:
      - endpoint:   https://<backend>/api/billing/webhook
      - secret:     STRIPE_WEBHOOK_SECRET
      - events:     checkout.session.completed, customer.subscription.created,
                    customer.subscription.updated, customer.subscription.deleted
    """
    payload = request.data
    sig = request.headers.get("Stripe-Signature", "")
    wh_secret = _conf("STRIPE_WEBHOOK_SECRET", "")
    if not wh_secret:
        return _json_error("STRIPE_WEBHOOK_SECRET is missing", 500)

    try:
        event = stripe.Webhook.construct_event(payload, sig, wh_secret)
    except ValueError:
        return _json_error("Invalid payload", 400)
    except stripe.error.SignatureVerificationError:
        return _json_error("Invalid signature", 400)

    etype = event["type"]
    data = event["data"]["object"]

    try:
        if etype == "checkout.session.completed":
            # Obtenemos metadata y/o subscription para deducir plan
            md = data.get("metadata", {}) or {}
            entity_type = md.get("entity_type") or ("org" if md.get("plan_scope") == "org" else "user")
            entity_id = md.get("entity_id") or md.get("clerk_org_id") or md.get("clerk_user_id")
            plan = _plan_from_scope(md.get("plan_scope"))
            if not entity_id:
                # Fallback: intentar por customer + buscar subscription
                sub_id = data.get("subscription")
                if sub_id:
                    sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
                    plan = _plan_from_subscription(sub)
                # Sin entity_id no podemos actualizar Clerk
                return jsonify(received=True, note="no entity_id in metadata"), 200

            ok, err = _update_clerk_public_metadata(entity_type, entity_id, plan)
            if not ok:
                return err
            return jsonify(received=True), 200

        elif etype in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
            # En update/delete, volvemos a fijar plan según estado
            sub = data
            # Intentar leer metadata de la subscription
            md = (sub.get("metadata") or {})
            entity_type = md.get("entity_type") or ("org" if md.get("plan_scope") == "org" else "user")
            entity_id = md.get("entity_id") or md.get("clerk_org_id") or md.get("clerk_user_id")
            plan = _plan_from_subscription(sub)
            if not entity_id:
                return jsonify(received=True, note="no entity_id in subscription metadata"), 200
            ok, err = _update_clerk_public_metadata(entity_type, entity_id, plan)
            if not ok:
                return err
            return jsonify(received=True), 200

        # Otros eventos no usados: confirmamos recepción
        return jsonify(received=True, ignored=etype), 200

    except stripe.error.StripeError as e:
        return _json_error(f"Stripe webhook error: {str(e)}", 400)
    except Exception as e:
        return _json_error(f"Unexpected webhook error: {str(e)}", 500)

@bp.post("/sync")
@_require_auth
def sync_after_success():
    """
    Fallback manual desde frontend tras éxito:
    Body: { "session_id": "cs_test_..." }
    Recupera la sesión, deduce entity y plan, y actualiza Clerk igual que el webhook.
    """
    body = request.get_json(silent=True) or {}
    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        return _json_error("session_id is required", 400)

    ok, err = _init_stripe()
    if not ok:
        return err

    try:
        session = stripe.checkout.Session.retrieve(session_id, expand=["subscription", "subscription.items.data.price"])
    except stripe.error.StripeError as e:
        return _json_error(f"Stripe error retrieving session: {str(e)}", 400)

    md = (session.get("metadata") or {})
    entity_type = md.get("entity_type") or ("org" if md.get("plan_scope") == "org" else "user")
    entity_id = md.get("entity_id") or md.get("clerk_org_id") or md.get("clerk_user_id")
    plan = _plan_from_subscription(session.get("subscription") or {})

    if not entity_id:
        return _json_error("Cannot determine entity_id from session metadata", 400)

    ok, err = _update_clerk_public_metadata(entity_type, entity_id, plan)
    if not ok:
        return err

    return jsonify(ok=True, entity_type=entity_type, entity_id=entity_id, plan=plan), 200

# ───────────────────────── Debug helpers (en el mismo blueprint 'billing') ─────────────────────────
import os
from flask import request
from app.auth import require_clerk_auth  # asegura que existe

@bp.get("/debug/auth-config")
def _dbg_auth_config():
    """Ver qué config/ENVs de Clerk ve el servidor (seguro, no incluye secretos)."""
    cfg = {
        "CLERK_ISSUER": os.getenv("CLERK_ISSUER", ""),
        "CLERK_JWKS_URL": os.getenv("CLERK_JWKS_URL", ""),
        "CLERK_AUDIENCE": os.getenv("CLERK_AUDIENCE", ""),
        "CLERK_LEEWAY": os.getenv("CLERK_LEEWAY", ""),
        "CLERK_JWKS_TTL": os.getenv("CLERK_JWKS_TTL", ""),
        "CLERK_JWKS_TIMEOUT": os.getenv("CLERK_JWKS_TIMEOUT", ""),
        "DISABLE_AUTH": os.getenv("DISABLE_AUTH", ""),
    }
    return jsonify(cfg), 200

@bp.get("/debug/claims")
@require_clerk_auth
def _dbg_claims():
    """Muestra lo que dejó auth en g.clerk para esta request."""
    authz = request.headers.get("Authorization", "")
    authz_short = (authz[:20] + "...") if authz else ""
    payload = {
        "g_clerk": {
            "user_id": getattr(g, "clerk", {}).get("user_id"),
            "org_id": getattr(g, "clerk", {}).get("org_id"),
            "email": getattr(g, "clerk", {}).get("email"),
            "name": getattr(g, "clerk", {}).get("name"),
        },
        "auth_header_present": bool(authz),
        "auth_header_prefix_ok": authz.startswith("Bearer "),
        "auth_header_sample": authz_short,
    }
    if (os.getenv("EXPOSE_CLAIMS_DEBUG", "0")).lower() in ("1", "true", "yes"):
        payload["raw_claims"] = getattr(g, "clerk", {}).get("raw_claims")
    return jsonify(payload), 200
