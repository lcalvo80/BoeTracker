# app/services/stripe_svc.py
from __future__ import annotations

import os
import stripe
from flask import current_app
from typing import List, Dict, Any


# ───────────────────────── Helpers ENV ─────────────────────────

def _env_flag(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")

def _env_list(name: str) -> List[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [x.strip().upper() for x in raw.split(",") if x.strip()]

def _env_str(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name)
    if val is None or str(val).strip() == "":
        return default
    return str(val).strip()


# ───────────────────────── Init Stripe ─────────────────────────

def init_stripe() -> None:
    """Inicializa la API key de Stripe desde ENV o config de Flask."""
    key = os.getenv("STRIPE_SECRET_KEY") or current_app.config.get("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is empty/missing")
    stripe.api_key = key


# ─────────────────────── Checkout (subscription) ───────────────────────

def create_checkout_session(
    *,
    customer_id: str,
    price_id: str,
    quantity: int,
    meta: Dict[str, Any],
    success_url: str,
    cancel_url: str,
) -> stripe.checkout.Session:
    """
    Crea una sesión de Stripe Checkout para suscripción.

    - Requiere Price ID (price_...).
    - Automatic Tax (configurable).
    - Recolecta dirección de facturación y la guarda en el Customer.
    - `customer_update[name]='auto'` para permitir Tax ID collection.
    - NO usa customer_creation si ya pasamos 'customer'.
    - `locale='auto'` (o forzado por ENV) para idioma del navegador.
    - Shipping opcional por ENV (por defecto desactivado).
    """
    if not price_id:
        raise ValueError("Missing price_id")
    if not success_url or not cancel_url:
        raise ValueError("Missing success_url/cancel_url")

    # Validación amistosa del price_id
    if price_id.startswith("prod_"):
        raise ValueError("Invalid price_id: received a product id (prod_...). Use a price id (price_...).")

    # Flags desde ENV (tuneables sin tocar código)
    automatic_tax_enabled = _env_flag("STRIPE_AUTOMATIC_TAX", True)
    require_billing_addr  = _env_flag("STRIPE_REQUIRE_BILLING_ADDRESS", True)
    collect_shipping      = _env_flag("STRIPE_COLLECT_SHIPPING", False)
    shipping_countries    = _env_list("STRIPE_SHIPPING_COUNTRIES")  # ej: ES,PT,FR

    auto_save_address     = _env_flag("STRIPE_SAVE_ADDRESS_AUTO", True)
    auto_save_shipping    = _env_flag("STRIPE_SAVE_SHIPPING_AUTO", collect_shipping)
    auto_save_name        = _env_flag("STRIPE_SAVE_NAME_AUTO", True)

    # Idioma: por defecto 'auto' (Stripe detecta navegador). Puedes forzar p.ej. 'es' con ENV.
    locale = _env_str("STRIPE_CHECKOUT_LOCALE", "auto")

    # Construcción base (pasamos customer_id → NO usar customer_creation)
    kwargs: Dict[str, Any] = {
        "customer": customer_id,
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": max(int(quantity), 1)}],
        "allow_promotion_codes": True,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "subscription_data": {"metadata": meta},
        "metadata": meta,
        "tax_id_collection": {"enabled": True},  # VAT/IVA (UE)
        "locale": locale,                        # idioma auto/forzado
        # "customer_creation": "always",  # ❌ NO usar si pasamos 'customer'
    }

    # Automatic Tax + billing address + actualizaciones de Customer
    cu: Dict[str, str] = {}
    if automatic_tax_enabled:
        kwargs["automatic_tax"] = {"enabled": True}
        if require_billing_addr:
            kwargs["billing_address_collection"] = "required"
        if auto_save_address:
            cu["address"] = "auto"
        # Para permitir tax_id_collection, Stripe pide habilitar name=auto
        if auto_save_name:
            cu["name"] = "auto"

    # Shipping (solo si vendes físico)
    if collect_shipping:
        if auto_save_shipping:
            cu["shipping"] = "auto"
        kwargs["shipping_address_collection"] = (
            {"allowed_countries": shipping_countries}
            if shipping_countries
            else {"allowed_countries": ["US", "ES", "PT", "FR", "DE", "IT"]}
        )

    if cu:
        kwargs["customer_update"] = cu

    # Crear la sesión de Checkout
    return stripe.checkout.Session.create(**kwargs)


# ─────────────────────── Billing Portal ───────────────────────

def create_billing_portal(customer_id: str, return_url: str) -> stripe.billing_portal.Session:
    """Crea sesión del Billing Portal de Stripe."""
    return stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url,
    )


# ─────────────────── Seats (modificar cantidad) ───────────────────

def set_subscription_quantity(subscription_item_id: str, quantity: int) -> stripe.SubscriptionItem:
    """
    Ajusta la cantidad de un ítem de suscripción (ej. seats).
    Hace prorrateo inmediato.
    """
    return stripe.SubscriptionItem.modify(
        subscription_item_id,
        quantity=max(int(quantity), 1),
        proration_behavior="always_invoice",
    )
