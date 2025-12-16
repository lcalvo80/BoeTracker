# app/services/stripe_svc.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List

from flask import current_app


def _cfg(k: str, default: str = "") -> str:
    return current_app.config.get(k, default) or default


def _bool_cfg(k: str, default: bool = False) -> bool:
    v = str(current_app.config.get(k, "1" if default else "0")).strip().lower()
    return v in ("1", "true", "yes", "on")


def _stripe():
    """
    Import diferido para que el módulo cargue aunque Stripe no esté inicializado
    hasta runtime en Railway.
    """
    import stripe  # type: ignore

    api_key = _cfg("STRIPE_SECRET_KEY") or _cfg("STRIPE_API_KEY")
    if not api_key:
        raise RuntimeError("Falta STRIPE_SECRET_KEY (o STRIPE_API_KEY) en configuración.")
    stripe.api_key = api_key
    return stripe


@dataclass(frozen=True)
class EntityCustomer:
    id: str
    email: Optional[str] = None
    name: Optional[str] = None


# ───────────────── Customers ─────────────────

def _customer_search_query(entity_type: str, entity_id: str) -> str:
    # Stripe Customer Search soporta query sobre metadata:
    # https://stripe.com/docs/search#query-language
    # Nota: entity_id se guarda como string.
    return f"metadata['entity_type']:'{entity_type}' AND metadata['entity_id']:'{entity_id}'"


def find_customer_for_entity(entity_type: str, entity_id: str) -> Optional[EntityCustomer]:
    stripe = _stripe()
    entity_type = (entity_type or "").strip().lower()
    entity_id = (entity_id or "").strip()

    if not entity_type or not entity_id:
        return None

    try:
        res = stripe.Customer.search(query=_customer_search_query(entity_type, entity_id), limit=1)
        data = getattr(res, "data", []) or []
        if not data:
            return None
        c = data[0]
        return EntityCustomer(id=c.id, email=getattr(c, "email", None), name=getattr(c, "name", None))
    except Exception:
        # Si el Search no está disponible o falla, fallback a list (menos fiable pero evita bloquear).
        try:
            res = stripe.Customer.list(limit=100)
            for c in getattr(res, "data", []) or []:
                md = getattr(c, "metadata", None) or {}
                if (md.get("entity_type") or "").lower() == entity_type and (md.get("entity_id") or "") == entity_id:
                    return EntityCustomer(id=c.id, email=getattr(c, "email", None), name=getattr(c, "name", None))
        except Exception:
            return None
        return None


def get_or_create_customer_for_entity(
    entity_type: str,
    entity_id: str,
    email: Optional[str],
    name: Optional[str],
    extra_metadata: Optional[Dict[str, str]] = None,
):
    """
    Customer idempotente por entity_type/entity_id usando metadata.
    entity_type: 'user' o 'org'
    entity_id: g.user_id o g.org_id
    """
    stripe = _stripe()
    entity_type = (entity_type or "").strip().lower()
    entity_id = (entity_id or "").strip()

    if entity_type not in ("user", "org"):
        raise ValueError("entity_type debe ser 'user' o 'org'.")
    if not entity_id:
        raise ValueError("entity_id requerido.")

    found = find_customer_for_entity(entity_type=entity_type, entity_id=entity_id)
    if found:
        return stripe.Customer.retrieve(found.id)

    metadata = {
        "entity_type": entity_type,
        "entity_id": entity_id,
    }
    if extra_metadata:
        for k, v in extra_metadata.items():
            if v is None:
                continue
            metadata[str(k)] = str(v)

    params: Dict[str, Any] = {"metadata": metadata}
    # Para org solemos no tener email; para user sí.
    if email:
        params["email"] = email
    if name:
        params["name"] = name

    return stripe.Customer.create(**params)


# ───────────────── Metadata builders ─────────────────

def build_pro_meta(
    user_id: str,
    price_id: str,
    entity_email: str,
    entity_name: str,
) -> Dict[str, str]:
    return {
        "plan": "pro",
        "scope": "user",
        "user_id": str(user_id or ""),
        "price_id": str(price_id or ""),
        "entity_email": str(entity_email or ""),
        "entity_name": str(entity_name or ""),
        "created_by": "boetracker",
    }


def build_enterprise_meta(
    org_id: str,
    seats: int,
    price_id: str,
    plan: str,
    plan_scope: str,
    entity_email: str,
    entity_name: str,
) -> Dict[str, str]:
    return {
        "plan": str(plan or "enterprise"),
        "scope": str(plan_scope or "org"),
        "org_id": str(org_id or ""),
        "seats": str(int(seats)),
        "price_id": str(price_id or ""),
        "entity_email": str(entity_email or ""),
        "entity_name": str(entity_name or ""),
        "created_by": "boetracker",
    }


# ───────────────── Checkout / Portal ─────────────────

def create_checkout_session(
    customer_id: str,
    price_id: str,
    quantity: int,
    meta: Dict[str, Any],
    success_url: str,
    cancel_url: str,
):
    """
    Crea Stripe Checkout Session.
    FIX PERMANENTE: si automatic_tax está activo, obliga a pedir dirección y guardarla en el Customer
    mediante customer_update[address]='auto'. Sin eso Stripe rechaza la sesión y nunca se abre Checkout.
    """
    stripe = _stripe()

    if not customer_id:
        raise ValueError("customer_id requerido.")
    if not price_id:
        raise ValueError("price_id requerido.")
    if not isinstance(quantity, int) or quantity < 1:
        raise ValueError("quantity debe ser entero >= 1.")
    if not success_url or not cancel_url:
        raise ValueError("success_url y cancel_url son requeridos.")

    automatic_tax_enabled = _bool_cfg("STRIPE_AUTOMATIC_TAX_ENABLED", True)
    tax_id_collection_enabled = _bool_cfg("STRIPE_TAX_ID_COLLECTION_ENABLED", False)

    params: Dict[str, Any] = dict(
        mode="subscription",
        customer=customer_id,
        success_url=success_url,
        cancel_url=cancel_url,
        line_items=[{"price": price_id, "quantity": quantity}],
        metadata=meta or {},
    )

    if automatic_tax_enabled:
        params["automatic_tax"] = {"enabled": True}
        # ✅ CRÍTICO: sin esto Stripe falla con "requires a valid address on the Customer"
        params["billing_address_collection"] = "required"
        params["customer_update"] = {"address": "auto", "name": "auto"}

    if tax_id_collection_enabled:
        params["tax_id_collection"] = {"enabled": True}

    return stripe.checkout.Session.create(**params)


def create_billing_portal(customer_id: str, return_url: str):
    stripe = _stripe()
    if not customer_id:
        raise ValueError("customer_id requerido.")
    if not return_url:
        raise ValueError("return_url requerido.")
    return stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)


# ───────────────── Summary / Invoices ─────────────────

def _customer_for_user(user_id: str, email: Optional[str]) -> Optional[str]:
    # preferimos buscar por metadata; si no existe, intentamos por email.
    c = find_customer_for_entity("user", user_id)
    if c:
        return c.id
    if email:
        stripe = _stripe()
        # buscar por email (puede devolver varios; cogemos el más reciente)
        res = stripe.Customer.list(email=email, limit=1)
        data = getattr(res, "data", []) or []
        if data:
            return data[0].id
    return None


def _customer_for_org(org_id: str) -> Optional[str]:
    c = find_customer_for_entity("org", org_id)
    return c.id if c else None


def _sub_to_dict(sub: Any) -> Dict[str, Any]:
    items = []
    try:
        for it in (sub.get("items", {}) or {}).get("data", []) if isinstance(sub, dict) else getattr(getattr(sub, "items", None), "data", []) or []:
            price = (it.get("price") if isinstance(it, dict) else getattr(it, "price", None)) or {}
            items.append(
                {
                    "price_id": price.get("id") if isinstance(price, dict) else getattr(price, "id", None),
                    "quantity": it.get("quantity") if isinstance(it, dict) else getattr(it, "quantity", None),
                }
            )
    except Exception:
        pass

    return {
        "id": sub.get("id") if isinstance(sub, dict) else getattr(sub, "id", None),
        "status": sub.get("status") if isinstance(sub, dict) else getattr(sub, "status", None),
        "current_period_end": sub.get("current_period_end") if isinstance(sub, dict) else getattr(sub, "current_period_end", None),
        "cancel_at_period_end": sub.get("cancel_at_period_end") if isinstance(sub, dict) else getattr(sub, "cancel_at_period_end", None),
        "items": items,
    }


def get_billing_summary_for_user(user_id: str, email: Optional[str]) -> Dict[str, Any]:
    stripe = _stripe()
    cust_id = _customer_for_user(user_id=user_id, email=email)
    if not cust_id:
        return {"scope": "user", "customer_id": None, "subscriptions": [], "active": False}

    subs = stripe.Subscription.list(customer=cust_id, status="all", limit=10)
    subs_data = [ _sub_to_dict(s) for s in (getattr(subs, "data", []) or []) ]

    active = any((s.get("status") in ("active", "trialing")) for s in subs_data)
    return {"scope": "user", "customer_id": cust_id, "subscriptions": subs_data, "active": active}


def get_billing_summary_for_org(org_id: str) -> Dict[str, Any]:
    stripe = _stripe()
    cust_id = _customer_for_org(org_id=org_id)
    if not cust_id:
        return {"scope": "org", "org_id": org_id, "customer_id": None, "subscriptions": [], "active": False}

    subs = stripe.Subscription.list(customer=cust_id, status="all", limit=10)
    subs_data = [ _sub_to_dict(s) for s in (getattr(subs, "data", []) or []) ]

    active = any((s.get("status") in ("active", "trialing")) for s in subs_data)
    return {"scope": "org", "org_id": org_id, "customer_id": cust_id, "subscriptions": subs_data, "active": active}


def list_invoices_for_user(user_id: str, email: Optional[str], limit: int = 20) -> Dict[str, Any]:
    stripe = _stripe()
    cust_id = _customer_for_user(user_id=user_id, email=email)
    if not cust_id:
        return {"scope": "user", "customer_id": None, "invoices": []}

    inv = stripe.Invoice.list(customer=cust_id, limit=max(1, min(100, int(limit))))
    invoices = []
    for x in (getattr(inv, "data", []) or []):
        invoices.append(
            {
                "id": getattr(x, "id", None),
                "status": getattr(x, "status", None),
                "paid": getattr(x, "paid", None),
                "currency": getattr(x, "currency", None),
                "amount_due": getattr(x, "amount_due", None),
                "amount_paid": getattr(x, "amount_paid", None),
                "created": getattr(x, "created", None),
                "hosted_invoice_url": getattr(x, "hosted_invoice_url", None),
                "invoice_pdf": getattr(x, "invoice_pdf", None),
            }
        )
    return {"scope": "user", "customer_id": cust_id, "invoices": invoices}


def list_invoices_for_org(org_id: str, limit: int = 20) -> Dict[str, Any]:
    stripe = _stripe()
    cust_id = _customer_for_org(org_id=org_id)
    if not cust_id:
        return {"scope": "org", "org_id": org_id, "customer_id": None, "invoices": []}

    inv = stripe.Invoice.list(customer=cust_id, limit=max(1, min(100, int(limit))))
    invoices = []
    for x in (getattr(inv, "data", []) or []):
        invoices.append(
            {
                "id": getattr(x, "id", None),
                "status": getattr(x, "status", None),
                "paid": getattr(x, "paid", None),
                "currency": getattr(x, "currency", None),
                "amount_due": getattr(x, "amount_due", None),
                "amount_paid": getattr(x, "amount_paid", None),
                "created": getattr(x, "created", None),
                "hosted_invoice_url": getattr(x, "hosted_invoice_url", None),
                "invoice_pdf": getattr(x, "invoice_pdf", None),
            }
        )
    return {"scope": "org", "org_id": org_id, "customer_id": cust_id, "invoices": invoices}
