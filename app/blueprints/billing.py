# app/routes/billing.py
from flask import Blueprint, request, jsonify, current_app, g, abort
from app.auth import require_clerk_auth
from app.integrations.stripe_utils import ensure_customer, init_stripe
import stripe

bp = Blueprint("billing", __name__)


@bp.post("/checkout")
@require_clerk_auth
def create_checkout():
    body = request.get_json(force=True, silent=True) or {}
    price_id = body.get("price_id")
    is_org = bool(body.get("is_org", False))
    quantity = int(body.get("quantity") or 1)
    if not price_id:
        abort(400, "price_id required")

    entity_type = "org" if is_org else "user"
    entity_id = g.clerk.get("org_id") if is_org else g.clerk.get("user_id")
    if is_org and not entity_id:
        abort(400, "Missing org_id in token")

    customer_id = ensure_customer(entity_type, entity_id)

    init_stripe()
    frontend = current_app.config["FRONTEND_URL"].rstrip("/")
    success_url = f"{frontend}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{frontend}/pricing?canceled=1"

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": max(1, quantity)}],
        allow_promotion_codes=True,
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=entity_id,
        metadata={"entity_type": entity_type, "entity_id": entity_id, "price_id": price_id},
        subscription_data={
            "metadata": {"entity_type": entity_type, "entity_id": entity_id, "price_id": price_id}
        },
    )
    return jsonify(checkout_url=session.url)


@bp.post("/portal")
@require_clerk_auth
def create_portal():
    body = request.get_json(silent=True) or {}
    is_org = bool(body.get("is_org", False))
    entity_type = "org" if is_org else "user"
    entity_id = g.clerk.get("org_id") if is_org else g.clerk.get("user_id")
    if is_org and not entity_id:
        abort(400, "Missing org_id in token")

    customer_id = ensure_customer(entity_type, entity_id)
    init_stripe()
    frontend = current_app.config["FRONTEND_URL"].rstrip("/")

    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{frontend}/billing",
    )
    return jsonify(portal_url=portal.url)
