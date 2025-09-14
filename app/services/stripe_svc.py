# app/services/stripe_svc.py
import stripe
from flask import current_app

def init_stripe():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

def create_checkout_session(customer_id: str, price_id: str, quantity: int, meta: dict, success_url: str, cancel_url: str):
    return stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": quantity}],
        allow_promotion_codes=True,
        automatic_tax={"enabled": True},
        tax_id_collection={"enabled": True},
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=meta,
    )

def create_billing_portal(customer_id: str, return_url: str):
    return stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)

def set_subscription_quantity(subscription_item_id: str, seats: int):
    return stripe.SubscriptionItem.modify(
        subscription_item_id,
        quantity=seats,
        proration_behavior="create_prorations",
    )
