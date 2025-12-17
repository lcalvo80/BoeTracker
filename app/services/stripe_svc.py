# app/services/stripe_svc.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Tuple

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
        # fallback
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

    metadata = {"entity_type": entity_type, "entity_id": entity_id}
    if extra_metadata:
        for k, v in extra_metadata.items():
            if v is None:
                continue
            metadata[str(k)] = str(v)

    params: Dict[str, Any] = {"metadata": metadata}
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
    c = find_customer_for_entity("user", user_id)
    if c:
        return c.id
    if email:
        stripe = _stripe()
        res = stripe.Customer.list(email=email, limit=1)
        data = getattr(res, "data", []) or []
        if data:
            return data[0].id
    return None


def _customer_for_org(org_id: str) -> Optional[str]:
    c = find_customer_for_entity("org", org_id)
    return c.id if c else None


def _safe_get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _sub_to_dict(sub: Any) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    try:
        # stripe.Subscription.items.data
        raw_items = None
        if isinstance(sub, dict):
            raw_items = (_safe_get(sub, "items", {}) or {}).get("data", []) or []
        else:
            raw_items = getattr(getattr(sub, "items", None), "data", []) or []

        for it in raw_items:
            price = _safe_get(it, "price", {}) or {}
            price_id = _safe_get(price, "id", None)
            quantity = _safe_get(it, "quantity", None)
            items.append({"price_id": price_id, "quantity": quantity})
    except Exception:
        pass

    return {
        "id": _safe_get(sub, "id"),
        "status": _safe_get(sub, "status"),
        "current_period_end": _safe_get(sub, "current_period_end"),
        "cancel_at_period_end": _safe_get(sub, "cancel_at_period_end"),
        "items": items,
    }


def _infer_plan_from_price_id(price_id: Optional[str]) -> str:
    """
    Contrato estable: decidimos el plan en backend.
    Ajusta aquí si en el futuro hay más precios.
    """
    if not price_id:
        return "free"

    ent = _cfg("STRIPE_PRICE_ENTERPRISE")
    pro = _cfg("STRIPE_PRICE_PRO")

    if ent and price_id == ent:
        return "enterprise"
    if pro and price_id == pro:
        return "pro"

    # fallback conservador: si no coincide, no inventamos enterprise
    return "pro"


def _pick_effective_subscription(subs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Preferimos active/trialing; si no, la primera disponible.
    """
    if not subs:
        return None
    for st in ("active", "trialing"):
        for s in subs:
            if (s.get("status") or "").lower() == st:
                return s
    return subs[0]


def _ensure_period_end(sub: Dict[str, Any]) -> Dict[str, Any]:
    """
    Si por cualquier razón current_period_end viene None, intentamos recuperar
    el subscription fresh de Stripe (solo 1 retrieve, coste bajo).
    """
    if sub.get("current_period_end") is not None:
        return sub
    sub_id = sub.get("id")
    if not sub_id:
        return sub
    try:
        stripe = _stripe()
        fresh = stripe.Subscription.retrieve(sub_id)
        sub["current_period_end"] = _safe_get(fresh, "current_period_end")
        sub["cancel_at_period_end"] = _safe_get(fresh, "cancel_at_period_end")
        # items (por si quantity no vino)
        try:
            sub["items"] = _sub_to_dict(fresh).get("items", []) or sub.get("items", [])
        except Exception:
            pass
    except Exception:
        # no bloqueamos la respuesta por esto
        return sub
    return sub


def _flatten_summary(
    scope: str,
    customer_id: Optional[str],
    org_id: Optional[str],
    subs_data: List[Dict[str, Any]],
) -> Dict[str, Any]:
    eff = _pick_effective_subscription(subs_data)
    eff = _ensure_period_end(eff) if eff else None

    plan = "free"
    status = None
    subscription_id = None
    current_period_end = None
    cancel_at_period_end = None
    seats = 0
    price_id = None

    if eff:
        subscription_id = eff.get("id")
        status = (eff.get("status") or "").lower() or None
        current_period_end = eff.get("current_period_end")
        cancel_at_period_end = eff.get("cancel_at_period_end")

        # primera línea (modelo actual: 1 price por sub)
        items = eff.get("items") or []
        if items and isinstance(items, list):
            price_id = (items[0] or {}).get("price_id")
            q = (items[0] or {}).get("quantity")
            try:
                seats = int(q) if q is not None else 0
            except Exception:
                seats = 0

        plan = _infer_plan_from_price_id(price_id)
        if plan == "pro" and seats <= 0:
            seats = 1  # pro = 1 seat implícito

    active = any((s.get("status") in ("active", "trialing")) for s in subs_data)

    return {
        "schema_version": 1,
        "scope": scope,
        "org_id": org_id,
        "customer_id": customer_id,
        "active": bool(active),

        # aplanado estable
        "plan": plan,
        "status": status,
        "subscription_id": subscription_id,
        "current_period_end": current_period_end,
        "cancel_at_period_end": cancel_at_period_end,
        "seats": seats,
        "price_id": price_id,

        # mantenemos para debug/compatibilidad (pero FE ya no depende)
        "subscriptions": subs_data,
    }


def get_billing_summary_v1_for_user(user_id: str, email: Optional[str]) -> Dict[str, Any]:
    stripe = _stripe()
    cust_id = _customer_for_user(user_id=user_id, email=email)
    if not cust_id:
        return _flatten_summary(scope="user", customer_id=None, org_id=None, subs_data=[])

    subs = stripe.Subscription.list(customer=cust_id, status="all", limit=10)
    subs_data = [_sub_to_dict(s) for s in (getattr(subs, "data", []) or [])]
    return _flatten_summary(scope="user", customer_id=cust_id, org_id=None, subs_data=subs_data)


def get_billing_summary_v1_for_org(org_id: str) -> Dict[str, Any]:
    stripe = _stripe()
    cust_id = _customer_for_org(org_id=org_id)
    if not cust_id:
        return _flatten_summary(scope="org", customer_id=None, org_id=org_id, subs_data=[])

    subs = stripe.Subscription.list(customer=cust_id, status="all", limit=10)
    subs_data = [_sub_to_dict(s) for s in (getattr(subs, "data", []) or [])]
    return _flatten_summary(scope="org", customer_id=cust_id, org_id=org_id, subs_data=subs_data)


def list_invoices_for_user(user_id: str, email: Optional[str], limit: int = 20) -> Dict[str, Any]:
    stripe = _stripe()
    cust_id = _customer_for_user(user_id=user_id, email=email)
    if not cust_id:
        return {"schema_version": 1, "scope": "user", "customer_id": None, "invoices": []}

    inv = stripe.Invoice.list(customer=cust_id, limit=max(1, min(100, int(limit))))
    invoices = []
    for x in (getattr(inv, "data", []) or []):
        invoices.append(
            {
                "id": getattr(x, "id", None),
                "number": getattr(x, "number", None),
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
    return {"schema_version": 1, "scope": "user", "customer_id": cust_id, "invoices": invoices}


def list_invoices_for_org(org_id: str, limit: int = 20) -> Dict[str, Any]:
    stripe = _stripe()
    cust_id = _customer_for_org(org_id=org_id)
    if not cust_id:
        return {"schema_version": 1, "scope": "org", "org_id": org_id, "customer_id": None, "invoices": []}

    inv = stripe.Invoice.list(customer=cust_id, limit=max(1, min(100, int(limit))))
    invoices = []
    for x in (getattr(inv, "data", []) or []):
        invoices.append(
            {
                "id": getattr(x, "id", None),
                "number": getattr(x, "number", None),
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
    return {"schema_version": 1, "scope": "org", "org_id": org_id, "customer_id": cust_id, "invoices": invoices}
    