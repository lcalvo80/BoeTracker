# app/services/stripe_svc.py
from __future__ import annotations
import os
import stripe
from flask import current_app

def init_stripe():
    key = os.getenv("STRIPE_SECRET_KEY") or current_app.config.get("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("Falta STRIPE_SECRET_KEY")
    stripe.api_key = key

def create_checkout_session(*, customer_id: str, price_id: str, quantity: int, meta: dict, success_url: str, cancel_url: str):
    """
    Crea una Checkout Session de tipo subscription.
    Incluye metadata en subscription_data para que llegue a la Subscription.
    """
    return stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": quantity}],
        allow_promotion_codes=True,
        automatic_tax={"enabled": True},
        tax_id_collection={"enabled": True},
        success_url=success_url,
        cancel_url=cancel_url,
        subscription_data={"metadata": meta},
        metadata=meta,
    )

def create_billing_portal(customer_id: str, return_url: str):
    return stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url
    )

def set_subscription_quantity(subscription_item_id: str, quantity: int):
    """
    Para billing por asientos (org): ajusta la cantidad del item.
    """
    stripe.SubscriptionItem.modify(subscription_item_id, quantity=max(int(quantity), 1), proration_behavior="always_invoice")
