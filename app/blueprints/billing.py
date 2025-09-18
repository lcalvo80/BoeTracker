# app/blueprints/billing.py
from __future__ import annotations

import os
import stripe
from typing import Tuple
from flask import Blueprint, request, jsonify, current_app, g, abort

from app.auth import require_clerk_auth

bp = Blueprint("billing", __name__)

# ─────────────────── Stripe helpers ───────────────────

def _init_stripe() -> None:
    key = current_app.config.get("STRIPE_SECRET_KEY") or os.getenv("STRIPE_SECRET_KEY", "")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is empty/missing")
    stripe.api_key = key

def _clerk_target(is_org: bool) -> Tuple[str, str]:
    data = getattr(g, "clerk", {}) or {}
    entity_type = "org" if is_org else "user"
    entity_id = data.get("org_id") if is_org else data.get("user_id")
    if not entity_id:
        abort(400, "Missing org_id in token" if is_org else "Missing user_id in token")
    return entity_type, str(entity_id)

def _maybe_contact_from_token() -> dict:
    data = getattr(g, "clerk", {}) or {}
    out = {}
    if data.get("email"): out["email"] = str(data["email"])
    if data.get("name"):  out["name"]  = str(data["name"])
    return out

def _ensure_customer(entity_type: str, entity_id: str, is_org: bool) -> str:
    """
    Busca o crea Customer en Stripe. Escribimos metadata compatible con ambos flujos:
      - entity_type/entity_id
      - clerk_user_id/clerk_org_id
    """
    # Search por metadata (si tu cuenta lo permite)
    try:
        query = f"metadata['entity_type']:'{entity_type}' AND metadata['entity_id']:'{entity_id}'"
        res = stripe.Customer.search(query=query, limit=1)
        if res.data:
            return res.data[0].id
    except Exception:
        pass

    contact = _maybe_contact_from_token()
    metadata = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        ("clerk_org_id" if is_org else "clerk_user_id"): entity_id,
    }
    cust = stripe.Customer.create(metadata=metadata, **contact)
    return cust.id

def _frontend_base() -> str:
    return (current_app.config.get("FRONTEND_URL") or "http://localhost:5173").rstrip("/")

# ─────────────────── Endpoints ───────────────────

@bp.post("/checkout")
@require_clerk_auth
def create_checkout():
    """
    Crea una sesión de Stripe Checkout (suscripción).
    Body JSON:
      - price_id (string, obligatorio → "price_...")
      - is_org (bool, default False)
      - quantity (int, default 1)
    """
    body = request.get_json(force=True, silent=True) or {}
    price_id = (body.get("price_id") or "").strip()
    is_org = bool(body.get("is_org", False))
    quantity = int(body.get("quantity") or 1)

    if not price_id:
        abort(400, "price_id required")
    if price_id.startswith("prod_"):
        abort(400, "price_id looks like a product id (prod_...). Use a price id (price_...).")
    if quantity < 1:
        quantity = 1

    _init_stripe()
    entity_type, entity_id = _clerk_target(is_org=is_org)
    customer_id = _ensure_customer(entity_type, entity_id, is_org=is_org)

    frontend = _frontend_base()
    success_url = f"{frontend}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{frontend}/pricing?canceled=1"

    locale = os.getenv("STRIPE_CHECKOUT_LOCALE", "auto")
    automatic_tax = os.getenv("STRIPE_AUTOMATIC_TAX", "true").lower() in ("1", "true", "yes", "on")
    require_billing_addr = os.getenv("STRIPE_REQUIRE_BILLING_ADDRESS", "true").lower() in ("1", "true", "yes", "on")

    # Metadatos consistentes para el webhook
    session_metadata = {
        "price_id": price_id,
        "plan_scope": "org" if is_org else "user",
        "clerk_user_id": entity_id if not is_org else "",
        "clerk_org_id": entity_id if is_org else "",
        "entity_type": entity_type,
        "entity_id": entity_id,
    }

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": quantity}],
        allow_promotion_codes=True,
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=entity_id,
        metadata=session_metadata,
        subscription_data={
            "metadata": session_metadata,
        },
        tax_id_collection={"enabled": True},
        locale=locale,
        automatic_tax={"enabled": automatic_tax},
        billing_address_collection=("required" if require_billing_addr else "auto"),
        customer_update={"address": "auto", "name": "auto"},
    )

    return jsonify(checkout_url=session.url)

@bp.post("/portal")
@require_clerk_auth
def create_portal():
    """
    Crea sesión del Billing Portal de Stripe para el Customer asociado.
    Body JSON (opcional):
      - is_org (bool, default False)
    """
    body = request.get_json(silent=True) or {}
    is_org = bool(body.get("is_org", False))

    _init_stripe()
    entity_type, entity_id = _clerk_target(is_org=is_org)
    customer_id = _ensure_customer(entity_type, entity_id, is_org=is_org)

    frontend = _frontend_base()
    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{frontend}/billing",
    )
    return jsonify(portal_url=portal.url)
