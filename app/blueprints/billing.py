# app/routes/billing.py
from __future__ import annotations
from flask import Blueprint, request, jsonify, g, current_app
from app.services.auth import require_auth
from app.services import clerk_svc, stripe_svc
import stripe

bp = Blueprint("billing", __name__)

def _get_or_create_customer(*, is_org: bool, user_id: str, org_id: str | None) -> str:
    """Devuelve el Stripe Customer ID para usuario/org; lo crea y persiste si falta."""
    stripe_svc.init_stripe()

    if is_org and org_id:
        org = clerk_svc.get_org(org_id)
        billing = (org.get("private_metadata") or {}).get("billing", {}) or {}
        customer_id = billing.get("stripeCustomerId")
        if customer_id:
            return customer_id

        # Crear customer para la organización
        name = org.get("name") or f"Org {org_id}"
        customer = stripe.Customer.create(
            name=name,
            metadata={"clerk_org_id": org_id},
        )
        clerk_svc.update_org_metadata(
            org_id, private={"billing": {**billing, "stripeCustomerId": customer.id}}
        )
        return customer.id

    # scope usuario
    user = clerk_svc.get_user(user_id)
    billing = (user.get("private_metadata") or {}).get("billing", {}) or {}
    customer_id = billing.get("stripeCustomerId")
    if customer_id:
        return customer_id

    # Best-effort email/name
    email = None
    try:
        email = user.get("email_address") or user.get("email")
        if not email:
            peid = user.get("primary_email_address_id")
            if peid:
                for e in (user.get("email_addresses") or []):
                    if e.get("id") == peid:
                        email = e.get("email_address")
                        break
    except Exception:
        pass

    name = (user.get("first_name") or "") + " " + (user.get("last_name") or "")
    name = name.strip() or user_id

    customer = stripe.Customer.create(
        email=email,
        name=name,
        metadata={"clerk_user_id": user_id},
    )
    clerk_svc.update_user_metadata(
        user_id, private={"billing": {**billing, "stripeCustomerId": customer.id}}
    )
    return customer.id

@bp.route("/checkout", methods=["POST", "OPTIONS"])
@require_auth()
def checkout():
    if request.method == "OPTIONS":
        return ("", 204)

    stripe_svc.init_stripe()

    data = request.get_json(silent=True) or {}
    price_id: str | None = data.get("price_id") or None
    is_org: bool = bool(data.get("is_org"))
    quantity: int = int(data.get("quantity") or 1)

    user_id: str = g.clerk["user_id"]
    org_id: str | None = g.clerk.get("org_id") if is_org else None

    # Precios por defecto desde la config si no llegan del frontend
    if not price_id:
        price_id = current_app.config.get(
            "PRICE_ENTERPRISE_SEAT_ID" if is_org else "PRICE_PRO_MONTHLY_ID"
        )
    if not price_id:
        return jsonify({"error": "missing_price_id"}), 400

    customer_id = _get_or_create_customer(is_org=is_org, user_id=user_id, org_id=org_id)

    success_url = f"{current_app.config['FRONTEND_URL']}/pricing?status=success"
    cancel_url  = f"{current_app.config['FRONTEND_URL']}/pricing?status=cancel"

    meta = {
        "plan_scope": "org" if is_org else "user",
        "clerk_user_id": user_id,
        "clerk_org_id": org_id or "",
    }

    session = stripe_svc.create_checkout_session(
        customer_id=customer_id,
        price_id=price_id,
        quantity=quantity if quantity > 0 else 1,
        meta=meta,
        success_url=success_url,
        cancel_url=cancel_url,
    )

    return jsonify({"checkout_url": session.url})

@bp.route("/portal", methods=["POST", "OPTIONS"])
@require_auth()
def portal():
    if request.method == "OPTIONS":
        return ("", 204)

    stripe_svc.init_stripe()
    user_id: str = g.clerk["user_id"]
    org_id: str | None = g.clerk.get("org_id")
    is_org: bool = bool(org_id)

    customer_id = _get_or_create_customer(is_org=is_org, user_id=user_id, org_id=org_id)
    portal = stripe_svc.create_billing_portal(
        customer_id, f"{current_app.config['FRONTEND_URL']}/settings/billing"
    )
    return jsonify({"portal_url": portal.url})
