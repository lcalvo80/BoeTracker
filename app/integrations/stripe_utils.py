# app/integrations/stripe_utils.py
import stripe
from flask import current_app
from app.integrations import clerk_admin as clerk


def init_stripe():
    stripe.api_key = current_app.config.get("STRIPE_SECRET_KEY")
    if not stripe.api_key:
        raise RuntimeError("Missing STRIPE_SECRET_KEY")


def _ensure_user_customer(user_id: str):
    init_stripe()
    user = clerk.get_user(user_id)
    pm = (user.get("public_metadata") or {})
    customer_id = pm.get("stripe_customer_id")
    if customer_id:
        return customer_id

    email = None
    primary_email = next((e for e in user.get("email_addresses", []) if e.get("id") == user.get("primary_email_address_id")), None)
    if primary_email:
        email = primary_email.get("email_address")

    customer = stripe.Customer.create(
        email=email,
        metadata={"entity_type": "user", "user_id": user_id},
    )
    pm.update({"stripe_customer_id": customer.id})
    clerk.patch_user_public_metadata(user_id, pm)
    return customer.id


def _ensure_org_customer(org_id: str):
    init_stripe()
    org = clerk.get_org(org_id)
    pm = (org.get("public_metadata") or {})
    customer_id = pm.get("stripe_customer_id")
    if customer_id:
        return customer_id

    customer = stripe.Customer.create(
        name=org.get("name"),
        metadata={"entity_type": "org", "org_id": org_id},
    )
    pm.update({"stripe_customer_id": customer.id})
    clerk.patch_org_public_metadata(org_id, pm)
    return customer.id


def ensure_customer(entity_type: str, entity_id: str) -> str:
    if entity_type == "user":
        return _ensure_user_customer(entity_id)
    elif entity_type == "org":
        return _ensure_org_customer(entity_id)
    raise ValueError("entity_type must be 'user' or 'org'")
