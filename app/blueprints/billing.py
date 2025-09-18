# app/routes/billing.py
from __future__ import annotations

import stripe
from flask import Blueprint, request, jsonify, current_app, g, abort

# Requiere tu guard de Clerk. Si tu función vive en otro sitio, ajusta el import.
from app.auth import require_clerk_auth  # asumes que ya existe en tu proyecto

bp = Blueprint("billing", __name__)


def _init_stripe() -> None:
    """Inicializa la API key de Stripe desde Flask config o ENV."""
    key = current_app.config.get("STRIPE_SECRET_KEY") or ""
    if not key:
        # Último recurso, por si alguien sólo configuró ENV
        import os
        key = os.getenv("STRIPE_SECRET_KEY", "")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is empty/missing")
    stripe.api_key = key


def _ensure_customer(entity_type: str, entity_id: str) -> str:
    """
    Busca (o crea) un Customer en Stripe para la entidad (user/org).
    Usa la metadata de Stripe para mantener el vínculo.
    """
    # Busca por metadata — requiere que esté activada la búsqueda en tu cuenta de Stripe
    query = f"metadata['entity_type']:'{entity_type}' AND metadata['entity_id']:'{entity_id}'"
    res = stripe.Customer.search(query=query, limit=1)
    if res.data:
        return res.data[0].id

    # Si quieres añadir email/nombre, obténlos de tu propio sistema o de Clerk aquí
    customer = stripe.Customer.create(
        metadata={"entity_type": entity_type, "entity_id": entity_id}
    )
    return customer.id


@bp.post("/checkout")
@require_clerk_auth
def create_checkout():
    """
    Crea una sesión de Checkout para suscripción.
    Espera JSON: { price_id, is_org, quantity }
    Requiere token de Clerk: g.clerk = { user_id?, org_id? }
    """
    body = request.get_json(force=True, silent=True) or {}
    price_id = body.get("price_id")
    is_org = bool(body.get("is_org", False))
    quantity = int(body.get("quantity") or 1)

    if not price_id:
        abort(400, "price_id required")

    entity_type = "org" if is_org else "user"
    entity_id = g.clerk.get("org_id") if is_org else g.clerk.get("user_id")
    if not entity_id:
        # Para org, exigimos org_id; para user, exigimos user_id
        abort(400, "Missing org_id in token" if is_org else "Missing user_id in token")

    _init_stripe()
    customer_id = _ensure_customer(entity_type, entity_id)

    frontend = current_app.config.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")
    success_url = f"{frontend}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{frontend}/pricing?canceled=1"

    # Construcción de la sesión
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": max(1, quantity)}],
        allow_promotion_codes=True,
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=entity_id,
        metadata={
            "entity_type": entity_type,
            "entity_id": entity_id,
            "price_id": price_id,
        },
        subscription_data={
            "metadata": {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "price_id": price_id,
            }
        },
        tax_id_collection={"enabled": True},
        locale="auto",
        automatic_tax={"enabled": True},
        billing_address_collection="required",
        customer_update={"address": "auto", "name": "auto"},
    )

    return jsonify(checkout_url=session.url)


@bp.post("/portal")
@require_clerk_auth
def create_portal():
    """Crea una sesión del Billing Portal de Stripe para el customer vinculado."""
    body = request.get_json(silent=True) or {}
    is_org = bool(body.get("is_org", False))

    entity_type = "org" if is_org else "user"
    entity_id = g.clerk.get("org_id") if is_org else g.clerk.get("user_id")
    if not entity_id:
        abort(400, "Missing org_id in token" if is_org else "Missing user_id in token")

    _init_stripe()
    customer_id = _ensure_customer(entity_type, entity_id)

    frontend = current_app.config.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")
    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{frontend}/billing",
    )
    return jsonify(portal_url=portal.url)
