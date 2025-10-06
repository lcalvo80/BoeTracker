# app/services/stripe_svc.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import stripe
from flask import current_app

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

def _env_str(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(name)
    if val is None or str(val).strip() == "":
        return default
    return str(val).strip()

def _normalize_entity_type(t: Optional[str]) -> Optional[str]:
    if t is None:
        return None
    t2 = str(t).strip().lower()
    if t2 in ("org", "organization", "organisation"):
        return "org"
    if t2 == "user":
        return "user"
    return t2 or None

# ───────────────────────── Init Stripe ─────────────────────────
def init_stripe() -> None:
    """Inicializa la API key de Stripe desde ENV o config de Flask."""
    key = os.getenv("STRIPE_SECRET_KEY") or current_app.config.get("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is empty/missing")
    if stripe.api_key != key:
        stripe.api_key = key

# ─────────────────────── Customers (1 por entidad) ───────────────────────
def _search_customer_by_entity(entity_type: str, entity_id: str) -> Optional[stripe.Customer]:
    """
    Busca un customer por metadata exacta de entidad (requiere Stripe Search).
    """
    init_stripe()
    q = f'metadata["entity_type"]:"{entity_type}" AND metadata["entity_id"]:"{entity_id}"'
    try:
        res = stripe.Customer.search(query=q, limit=1)
        if res and getattr(res, "data", None):
            return res.data[0]
    except Exception:
        current_app.logger.exception("[Stripe] Customer.search failed (query=%s)", q)
    return None

def get_or_create_customer_for_entity(
    *,
    entity_type: str,
    entity_id: str,
    email: Optional[str] = None,
    name: Optional[str] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> stripe.Customer:
    """
    Devuelve el customer asociado a una entidad (user/org). Si no existe, lo crea.
    NUNCA reutiliza ni gira un customer de otra entidad.
    """
    init_stripe()
    etype = _normalize_entity_type(entity_type)
    if etype not in ("user", "org"):
        raise ValueError(f"Invalid entity_type '{entity_type}' (expected 'user' or 'org')")
    eid = str(entity_id).strip()
    if not eid:
        raise ValueError("entity_id required")

    found = _search_customer_by_entity(etype, eid)
    if found:
        return found

    md: Dict[str, Any] = {
        "entity_type": etype,
        "entity_id": eid,
        "entity_email": (email or ""),
    }
    if etype == "user":
        md["clerk_user_id"] = eid
    else:
        md["clerk_org_id"] = eid

    if extra_metadata:
        # Solo añadimos claves que no colisionen con las "de sistema"
        for k, v in extra_metadata.items():
            if k not in md:
                md[k] = v

    cust = stripe.Customer.create(
        email=email or None,
        name=name or None,
        metadata=md,
    )
    return cust

def ensure_customer_metadata(
    *,
    customer_id: str,
    entity_type: str,
    entity_id: str,
    entity_email: Optional[str] = None,
    strict: bool = True,
) -> stripe.Customer:
    """
    Garantiza (si procede) los metadatos de entidad en el customer.
    - strict=True: si el customer ya tiene otra entidad, NO sobreescribe (evita “girar”).
    - strict=False: intentará escribir metadata aunque existan valores previos.
    """
    init_stripe()
    etype = _normalize_entity_type(entity_type)
    if etype not in ("user", "org"):
        raise ValueError("entity_type must be 'user' or 'org'")
    eid = str(entity_id).strip()
    if not eid:
        raise ValueError("entity_id required")

    cust = stripe.Customer.retrieve(customer_id)
    md = (cust.get("metadata") or {}) if isinstance(cust, dict) else {}
    cur_t = _normalize_entity_type(md.get("entity_type"))
    cur_id = (md.get("entity_id") or "").strip()

    if cur_t and cur_id:
        if cur_t == etype and cur_id == eid:
            # Ya correcto
            return cust
        if strict:
            current_app.logger.warning(
                "[Stripe] customer %s already bound to %s:%s; NOT flipping to %s:%s",
                customer_id, cur_t, cur_id, etype, eid
            )
            return cust

    changed = False
    if md.get("entity_type") != etype: md["entity_type"] = etype; changed = True
    if md.get("entity_id") != eid: md["entity_id"] = eid; changed = True
    if etype == "user" and md.get("clerk_user_id") != eid: md["clerk_user_id"] = eid; changed = True
    if etype == "org" and md.get("clerk_org_id") != eid: md["clerk_org_id"] = eid; changed = True
    if entity_email is not None and md.get("entity_email") != entity_email: md["entity_email"] = entity_email; changed = True

    if changed:
        cust = stripe.Customer.modify(customer_id, metadata=md)
    return cust

def assert_customer_entity(
    *,
    customer_id: str,
    expected_entity_type: str,
    expected_entity_id: str,
) -> None:
    """
    Lanza ValueError si el customer está ligado a otra entidad diferente de la esperada.
    Úsalo antes de crear sesiones de checkout.
    """
    init_stripe()
    etype = _normalize_entity_type(expected_entity_type)
    if etype not in ("user", "org"):
        raise ValueError("expected_entity_type must be 'user' or 'org'")
    eid = str(expected_entity_id).strip()
    if not eid:
        raise ValueError("expected_entity_id required")

    cust = stripe.Customer.retrieve(customer_id)
    md = (cust.get("metadata") or {}) if isinstance(cust, dict) else {}
    cur_t = _normalize_entity_type(md.get("entity_type"))
    cur_id = (md.get("entity_id") or "").strip()

    if cur_t and cur_id and (cur_t != etype or cur_id != eid):
        raise ValueError(
            f"customer {customer_id} belongs to {cur_t}:{cur_id}, not {etype}:{eid}. "
            "Refuse to use same customer for different entity."
        )

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
    Crea una Checkout Session de suscripción.
    - NO modifica metadatos del customer (evita flips). Verifica usar el customer correcto
      con assert_customer_entity(...) o crea el customer adecuado con get_or_create_customer_for_entity(...).
    """
    init_stripe()

    if not price_id:
        raise ValueError("Missing price_id")
    if not success_url or not cancel_url:
        raise ValueError("Missing success_url/cancel_url")
    if price_id.startswith("prod_"):
        raise ValueError("Invalid price_id: received a product id (prod_...). Use a price id (price_...).")

    automatic_tax_enabled = _env_flag("STRIPE_AUTOMATIC_TAX", True)
    require_billing_addr  = _env_flag("STRIPE_REQUIRE_BILLING_ADDRESS", True)
    collect_shipping      = _env_flag("STRIPE_COLLECT_SHIPPING", False)
    shipping_countries    = _env_list("STRIPE_SHIPPING_COUNTRIES")
    auto_save_address     = _env_flag("STRIPE_SAVE_ADDRESS_AUTO", True)
    auto_save_shipping    = _env_flag("STRIPE_SAVE_SHIPPING_AUTO", collect_shipping)
    auto_save_name        = _env_flag("STRIPE_SAVE_NAME_AUTO", True)
    locale                = _env_str("STRIPE_CHECKOUT_LOCALE", "auto")

    # Opcional: si el caller nos pasa entity_type/entity_id en meta, validamos coherencia
    _etype = _normalize_entity_type(meta.get("entity_type"))
    _eid   = (meta.get("entity_id") or "").strip()
    if _etype in ("user", "org") and _eid:
        try:
            assert_customer_entity(customer_id=customer_id, expected_entity_type=_etype, expected_entity_id=_eid)
        except ValueError as ve:
            # Deja traza clara y relanza para que el endpoint pueda reaccionar (p.ej. crear customer correcto)
            current_app.logger.error("[Stripe] %s", ve)
            raise

    kwargs: Dict[str, Any] = {
        "customer": customer_id,
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": max(int(quantity), 1)}],
        "allow_promotion_codes": True,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "subscription_data": {"metadata": meta},
        "metadata": meta,
        "tax_id_collection": {"enabled": True},
        "locale": locale,
    }

    cu: Dict[str, str] = {}
    if automatic_tax_enabled:
        kwargs["automatic_tax"] = {"enabled": True}
        if require_billing_addr:
            kwargs["billing_address_collection"] = "required"
        if auto_save_address:
            cu["address"] = "auto"
        if auto_save_name:
            cu["name"] = "auto"

    if collect_shipping:
        if auto_save_shipping:
            cu["shipping"] = "auto"
        kwargs["shipping_address_collection"] = (
            {"allowed_countries": shipping_countries}
            if shipping_countries else {"allowed_countries": ["US", "ES", "PT", "FR", "DE", "IT"]}
        )

    if cu:
        kwargs["customer_update"] = cu

    return stripe.checkout.Session.create(**kwargs)

# ─────────────────────── Billing Portal ───────────────────────
def create_billing_portal(customer_id: str, return_url: str) -> stripe.billing_portal.Session:
    init_stripe()
    return stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url,
    )

# ─────────────────── Seats (modificar cantidad) ───────────────────
def set_subscription_quantity(subscription_item_id: str, quantity: int) -> stripe.SubscriptionItem:
    init_stripe()
    return stripe.SubscriptionItem.modify(
        subscription_item_id,
        quantity=max(int(quantity), 1),
        proration_behavior="always_invoice",
    )

# ─────────────────── Utilidades varias ───────────────────
def build_enterprise_meta(
    *,
    org_id: str,
    seats: int,
    price_id: Optional[str] = None,
    plan: str = "enterprise",
    plan_scope: str = "org",
    entity_email: Optional[str] = "",
    entity_name: Optional[str] = "",
) -> Dict[str, Any]:
    """
    Crea un bloque de metadata coherente para Enterprise (org-level).
    """
    m: Dict[str, Any] = {
        "entity_type": "org",
        "entity_id": str(org_id),
        "plan": plan,
        "plan_scope": plan_scope,
        "seats": str(int(seats)),
        "entity_email": entity_email or "",
        "entity_name": entity_name or "",
    }
    if price_id:
        m["price_id"] = price_id
    return m

def build_pro_meta(
    *,
    user_id: str,
    price_id: Optional[str] = None,
    plan: str = "pro",
    plan_scope: str = "user",
    entity_email: Optional[str] = "",
    entity_name: Optional[str] = "",
) -> Dict[str, Any]:
    """
    Crea un bloque de metadata coherente para Pro (user-level).
    """
    m: Dict[str, Any] = {
        "entity_type": "user",
        "entity_id": str(user_id),
        "plan": plan,
        "plan_scope": plan_scope,
        "entity_email": entity_email or "",
        "entity_name": entity_name or "",
    }
    if price_id:
        m["price_id"] = price_id
    return m
