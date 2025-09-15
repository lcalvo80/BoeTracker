# app/routes/billing.py
from flask import Blueprint, request, jsonify, g, current_app
import stripe
from app.services.auth import require_auth
from app.services import clerk_svc, stripe_svc

bp = Blueprint("billing", __name__)

def _resolve_customer_id(is_org: bool, user_id: str, org_id: str | None):
    if is_org and org_id:
        org = clerk_svc.get_org(org_id)
        return (org.get("private_metadata") or {}).get("billing", {}).get("stripeCustomerId")
    else:
        user = clerk_svc.get_user(user_id)
        return (user.get("private_metadata") or {}).get("billing", {}).get("stripeCustomerId")

@bp.post("/checkout")
@require_auth()
def checkout():
    stripe_svc.init_stripe()
    body = request.get_json() or {}
    price_id = body.get("price_id")
    is_org   = bool(body.get("is_org"))
    quantity = int(body.get("quantity") or 1)

    user_id = g.clerk["user_id"]
    org_id  = g.clerk.get("org_id")

    customer_id = _resolve_customer_id(is_org, user_id, org_id)
    if not customer_id:
        customer = stripe.Customer.create(
            metadata={"clerk_user_id": user_id, "clerk_org_id": org_id or ""},
        )
        customer_id = customer["id"]
        if is_org and org_id:
            clerk_svc.update_org_metadata(org_id, private={"billing": {"stripeCustomerId": customer_id}})
        else:
            clerk_svc.update_user_metadata(user_id, private={"billing": {"stripeCustomerId": customer_id}})

    session = stripe_svc.create_checkout_session(
        customer_id=customer_id,
        price_id=price_id,
        quantity=quantity,
        meta={"clerk_user_id": user_id, "clerk_org_id": org_id or "", "plan_scope": "org" if (is_org and org_id) else "user"},
        success_url=f"{current_app.config['FRONTEND_URL']}/settings/billing?status=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{current_app.config['FRONTEND_URL']}/pricing?status=cancel",
    )
    return jsonify({"checkout_url": session.url})

@bp.post("/portal")
@require_auth()
def portal():
    stripe_svc.init_stripe()
    user_id = g.clerk["user_id"]
    org_id  = g.clerk.get("org_id")
    is_org  = bool(org_id)

    customer_id = _resolve_customer_id(is_org, user_id, org_id)
    if not customer_id:
        return jsonify({"error": "customer_not_found"}), 400

    portal = stripe_svc.create_billing_portal(customer_id, f"{current_app.config['FRONTEND_URL']}/settings/billing")
    return jsonify({"portal_url": portal.url})
