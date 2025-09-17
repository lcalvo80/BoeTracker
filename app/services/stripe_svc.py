# app/services/stripe_svc.py
from __future__ import annotations
import os
import stripe
from flask import current_app

def _env_flag(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")

def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [x.strip().upper() for x in raw.split(",") if x.strip()]

def init_stripe():
    key = os.getenv("STRIPE_SECRET_KEY") or current_app.config.get("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is empty/missing")
    stripe.api_key = key

def create_checkout_session(
    *,
    customer_id: str,
    price_id: str,
    quantity: int,
    meta: dict,
    success_url: str,
    cancel_url: str,
):
    if not price_id:
        raise ValueError("Missing price_id")
    if not success_url or not cancel_url:
        raise ValueError("Missing success_url/cancel_url")

    # ---- Flags de comportamiento (tuneables por ENV) ----
    # Si no quieres Automatic Tax temporalmente, pon STRIPE_AUTOMATIC_TAX=0
    automatic_tax_enabled = _env_flag("STRIPE_AUTOMATIC_TAX", True)

    # Forzar recogida de billing address (recomendado con Automatic Tax)
    # Si no quieres forzarlo (p.ej. sandbox), pon STRIPE_REQUIRE_BILLING_ADDRESS=0
    require_billing_addr = _env_flag("STRIPE_REQUIRE_BILLING_ADDRESS", True)

    # ¿Recolectamos shipping address? (para bienes físicos)
    # Si lo activas, puedes limitar países con STRIPE_SHIPPING_COUNTRIES=ES,PT,FR
    collect_shipping = _env_flag("STRIPE_COLLECT_SHIPPING", False)
    shipping_countries = _env_list("STRIPE_SHIPPING_COUNTRIES")  # ej: ES,PT,FR

    # ¿Dejar que Stripe guarde automáticamente dirección/ship en el Customer?
    # Esto es lo que arregla exactamente tu error.
    auto_save_address = _env_flag("STRIPE_SAVE_ADDRESS_AUTO", True)
    auto_save_shipping = _env_flag("STRIPE_SAVE_SHIPPING_AUTO", collect_shipping)

    # ¿Queremos que Stripe cree el Customer si faltara? (seguro)
    customer_creation = os.getenv("STRIPE_CUSTOMER_CREATION", "always")  # or 'if_required'

    kwargs = {
        "customer": customer_id,
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": quantity}],
        "allow_promotion_codes": True,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "subscription_data": {"metadata": meta},
        "metadata": meta,
        # Tax IDs (VAT) – útil en EU
        "tax_id_collection": {"enabled": True},
        # Customer creation behavior
        "customer_creation": customer_creation,
    }

    # Automatic tax
    if automatic_tax_enabled:
        kwargs["automatic_tax"] = {"enabled": True}
        # Recolecta dirección de facturación en Checkout
        if require_billing_addr:
            kwargs["billing_address_collection"] = "required"
        # Pide a Checkout que guarde automáticamente la dirección/ship en el Customer
        cu = {}
        if auto_save_address:
            cu["address"] = "auto"
        if collect_shipping and auto_save_shipping:
            cu["shipping"] = "auto"
        if cu:
            kwargs["customer_update"] = cu

    # Shipping address (solo si vendes físico)
    if collect_shipping:
        if shipping_countries:
            kwargs["shipping_address_collection"] = {
                "allowed_countries": shipping_countries
            }
        else:
            kwargs["shipping_address_collection"] = {"allowed_countries": ["US", "ES", "PT", "FR", "DE", "IT"]}

    # Crear la sesión de Checkout
    return stripe.checkout.Session.create(**kwargs)

def create_billing_portal(customer_id: str, return_url: str):
    return stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url
    )

def set_subscription_quantity(subscription_item_id: str, quantity: int):
    stripe.SubscriptionItem.modify(
        subscription_item_id,
        quantity=max(int(quantity), 1),
        proration_behavior="always_invoice",
    )
