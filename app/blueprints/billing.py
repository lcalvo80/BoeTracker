from __future__ import annotations
import os
import stripe
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from flask import Blueprint, request, jsonify, current_app, g

from app.services import clerk_svc  # <<< usamos helpers unificados

bp = Blueprint("billing", __name__, url_prefix="/api")

# ───────────────── helpers de config ─────────────────
def _cfg(k: str, default: str | None = None) -> str | None:
    try:
        v = current_app.config.get(k)
    except Exception:
        v = None
    if v is None or str(v).strip() == "":
        v = os.getenv(k, default)
    return None if v is None else str(v)

def _truthy(v) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")

def _init_stripe():
    sk = _cfg("STRIPE_SECRET_KEY", "")
    if not sk:
        current_app.logger.error("[billing] STRIPE_SECRET_KEY missing")
        return None, (jsonify(error="STRIPE_SECRET_KEY missing"), 500)
    if stripe.api_key != sk:
        stripe.api_key = sk
    return sk, None

def _front_base() -> str:
    return (_cfg("FRONTEND_URL", "http://localhost:5173") or "").rstrip("/")

# Solo valores válidos para Stripe: 'auto' | 'never'
def _update_mode(v: str | None, default: str = "auto") -> str:
    s = (v or default).strip().lower()
    return "auto" if s in ("auto", "1", "true", "yes", "on") else "never"

# ───────────────── auth guard ─────────────────
def _load_auth_guard():
    if _truthy(_cfg("DISABLE_AUTH", "0") or "0"):
        def _noop(fn):
            def w(*a, **k): return fn(*a, **k)
            return w
        return _noop
    try:
        from app.auth import require_clerk_auth as real_guard
        return real_guard
    except Exception as e:
        current_app.logger.warning(f"[billing] auth guard fallback: {e}")
        def _noop(fn):
            def w(*a, **k): return fn(*a, **k)
            return w
        return _noop

_require_auth = _load_auth_guard()

# ───────────────── helpers Clerk ─────────────────
def _clerk_headers():
    sk = _cfg("CLERK_SECRET_KEY", "")
    if not sk:
        raise RuntimeError("Missing CLERK_SECRET_KEY")
    return {"Authorization": f"Bearer {sk}", "Content-Type": "application/json"}

def _clerk_base() -> str:
    return "https://api.clerk.com/v1"

def _clerk_get_org(org_id: str) -> Dict[str, Any]:
    r = requests.get(f"{_clerk_base()}/organizations/{org_id}", headers=_clerk_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def _is_org_admin(user_id: str, org_id: str) -> bool:
    try:
        r = requests.get(f"{_clerk_base()}/organizations/{org_id}/memberships?limit=1&user_id={user_id}",
                         headers=_clerk_headers(), timeout=10)
        r.raise_for_status()
        data = r.json()
        arr = data if isinstance(data, list) else data.get("data") or []
        if not arr: return False
        return (arr[0].get("role") or "").lower() == "admin"
    except Exception as e:
        current_app.logger.warning(f"[billing] org admin check skipped: {e}")
        return False

# ───────────────── helpers de identidad ─────────────────
def _derive_identity():
    """(user_id, email, name, org_id, org_role)"""
    c = getattr(g, "clerk", {}) or {}
    return (
        c.get("user_id"),
        c.get("email") or None,
        c.get("name") or None,
        c.get("org_id") or None,
        (c.get("raw_claims") or {}).get("org_role") if isinstance(c.get("raw_claims"), dict) else None,
    )

# ───────────────── Stripe helpers (customer y estado) ─────────────────
def _search_customer_by_email(email: str) -> Optional[str]:
    _, err = _init_stripe()
    if err: return None
    try:
        res = stripe.Customer.search(query=f"email:'{email}'", limit=1)
        for c in res.auto_paging_iter():
            return c.id
    except Exception:
        pass
    return None

def _ensure_customer_for_user(user_id: str) -> str:
    _, err = _init_stripe()
    if err: raise RuntimeError("stripe init failed")

    # Intentar recuperar desde Clerk metadata
    try:
        u = clerk_svc.get_user(user_id)
        priv = (u.get("private_metadata") or {})
        existing = (priv.get("billing") or {}).get("stripeCustomerId")
        if existing:
            return existing
        email = ((u.get("email_addresses") or [{}])[0].get("email_address") or "").strip().lower()
        name = ((u.get("first_name") or "") + " " + (u.get("last_name") or "")).strip() or u.get("username") or user_id
    except Exception:
        email, name = "", user_id

    # Buscar por email si existe
    cid = _search_customer_by_email(email)
    if cid:
        try:
            clerk_svc.update_user_metadata(user_id, private={"billing": {"stripeCustomerId": cid}})
        except Exception:
            pass
        return cid

    customer = stripe.Customer.create(
        email=email,
        name=name or user_id,
        metadata={"entity_type": "user", "entity_id": user_id, "clerk_user_id": user_id},
    )
    try:
        clerk_svc.update_user_metadata(user_id, private={"billing": {"stripeCustomerId": customer.id}})
    except Exception:
        pass
    return customer.id

def _ensure_customer_for_org(org_id: str) -> str:
    org = clerk_svc.get_org(org_id)
    priv = org.get("private_metadata") or {}
    existing = (priv.get("billing") or {}).get("stripeCustomerId")
    if existing:
        return existing

    name = org.get("name") or org_id
    _init_stripe()
    customer = stripe.Customer.create(
        name=name,
        metadata={"entity_type": "org", "entity_id": org_id, "clerk_org_id": org_id},
    )
    try:
        clerk_svc.update_org_metadata(org_id, private={"billing": {"stripeCustomerId": customer.id}})
    except Exception:
        pass
    return customer.id

def _subscription_summary(customer_id: str) -> Dict[str, Any]:
    subs = stripe.Subscription.list(customer=customer_id, limit=10, status="all")
    items: List[Dict[str, Any]] = []
    active = None
    for s in subs.auto_paging_iter():
        it = s["items"]["data"][0] if s["items"]["data"] else None
        seat_qty = it["quantity"] if it else None
        cur = {
            "id": s["id"],
            "status": s["status"],
            "current_period_end": s["current_period_end"],
            "created": s["created"],
            "price_id": (it["price"]["id"] if it and it.get("price") else None),
            "product": (it["price"]["product"] if it and it.get("price") else None),
            "quantity": seat_qty,
        }
        if s["status"] in ("active", "trialing", "past_due"):
            active = cur
        items.append(cur)
    return {"active": active, "all": items}

# ───────────────── endpoints existentes (compat) ─────────────────

# POST /api/portal (compat). Devuelve {"portal_url": ...}
@bp.post("/portal")
@_require_auth
def create_portal_compat():
    _, err = _init_stripe()
    if err: return err
    user_id, _, _, org_id, _ = _derive_identity()
    try:
        if org_id and _is_org_admin(user_id, org_id):
            customer_id = _ensure_customer_for_org(org_id)
        else:
            customer_id = _ensure_customer_for_user(user_id)
        ps = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{_front_base()}/account"
        )
        return jsonify(portal_url=ps.url), 200
    except Exception as e:
        current_app.logger.exception("Error creando portal")
        return jsonify(error="portal creation failed", detail=str(e)), 502


# Nuevo (GET): /api/billing/portal -> { url }
@bp.get("/billing/portal")
@_require_auth
def portal_get():
    _, err = _init_stripe()
    if err: return err
    user_id, _, _, org_id, _ = _derive_identity()
    try:
        if org_id and _is_org_admin(user_id, org_id):
            customer_id = _ensure_customer_for_org(org_id)
        else:
            customer_id = _ensure_customer_for_user(user_id)
        return_url = _cfg("STRIPE_BILLING_PORTAL_RETURN_URL") or f"{_front_base()}/account"
        ps = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
        return jsonify(url=ps.url), 200
    except Exception as e:
        current_app.logger.exception("Error creando portal (GET)")
        return jsonify(error="portal creation failed", detail=str(e)), 502


# GET /api/billing/summary  (user por defecto; admite ?scope=org)
@bp.get("/billing/summary")
@_require_auth
def summary_get():
    _, err = _init_stripe()
    if err: return err
    scope = (request.args.get("scope") or "user").lower()
    user_id, _, _, org_id, _ = _derive_identity()
    try:
        if scope == "org" and org_id and _is_org_admin(user_id, org_id):
            customer_id = _ensure_customer_for_org(org_id)
        else:
            customer_id = _ensure_customer_for_user(user_id)
        data = _subscription_summary(customer_id)

        # Reflejar plan/seats en Clerk (best-effort)
        sub = data.get("active") or {}
        status = (sub.get("status") or "").lower()
        qty = sub.get("quantity") or 0
        # Si hay org y admin, escribir seats; si no, actualiza plan usuario
        if scope == "org" and org_id and _is_org_admin(user_id, org_id):
            try:
                clerk_svc.update_org_metadata(org_id,
                    public={"subscription": ("enterprise" if status in ("active","trialing","past_due") else None),
                            "seats": qty})
            except Exception as e:
                current_app.logger.warning(f"[billing] No se pudo actualizar metadata de la org: {e}")
        else:
            try:
                plan = "pro" if status in ("active","trialing","past_due") else "free"
                clerk_svc.set_user_plan(user_id, plan=plan, status=status)
            except Exception as e:
                current_app.logger.warning(f"[billing] No se pudo actualizar metadata de usuario: {e}")

        return jsonify(data), 200
    except Exception as e:
        current_app.logger.exception("summary_get error")
        return jsonify(error="summary failed", detail=str(e)), 502


# GET /api/billing/invoices (user por defecto; admite ?scope=org)
@bp.get("/billing/invoices")
@_require_auth
def invoices_get():
    _, err = _init_stripe()
    if err: return err
    scope = (request.args.get("scope") or "user").lower()
    user_id, _, _, org_id, _ = _derive_identity()
    try:
        if scope == "org" and org_id and _is_org_admin(user_id, org_id):
            customer_id = _ensure_customer_for_org(org_id)
        else:
            customer_id = _ensure_customer_for_user(user_id)
        invs = stripe.Invoice.list(customer=customer_id, limit=24)
        data = [{
            "id": inv.id,
            "number": inv.number,
            "status": inv.status,
            "created": inv.created,
            "total": inv.total,
            "currency": inv.currency,
            "invoice_pdf": inv.invoice_pdf,
        } for inv in invs.auto_paging_iter()]
        return jsonify({"data": data}), 200
    except Exception as e:
        current_app.logger.exception("invoices_get error")
        return jsonify(error="invoices failed", detail=str(e)), 502


# POST /api/billing/sync  (sync tras success_url; opcional)
@bp.post("/billing/sync")
@_require_auth
def sync_after_success():
    """
    Opcional: al volver del éxito de Checkout, llama a este endpoint para
    sincronizar plan en Clerk. También se puede confiar 100% en Webhooks.
    """
    _, err = _init_stripe()
    if err: return err
    body = request.get_json(silent=True) or {}
    scope = (body.get("scope") or "user").lower()
    try:
        if scope == "org":
            user_id, _, _, org_id, _ = _derive_identity()
            if not org_id or not _is_org_admin(user_id, org_id):
                return jsonify(error="forbidden"), 403
            customer_id = _ensure_customer_for_org(org_id)
        else:
            user_id, _, _, _, _ = _derive_identity()
            customer_id = _ensure_customer_for_user(user_id)

        subs = stripe.Subscription.list(customer=customer_id, limit=1, status="all")
        sub = None
        for s in subs.auto_paging_iter():
            sub = s; break

        status = (sub.get("status") or "canceled") if sub else "canceled"
        if scope == "org":
            org_id = _derive_identity()[3]
            qty = (sub["items"]["data"][0]["quantity"] if sub and sub["items"]["data"] else 0)
            try:
                clerk_svc.update_org_metadata(
                    org_id,
                    public={"subscription": ("enterprise" if status in ("active","trialing","past_due") else None),
                            "seats": qty},
                    private={"billing": {
                        "stripeCustomerId": customer_id,
                        "subscriptionId": sub.get("id") if sub else None,
                        "status": status,
                    }},
                )
            except Exception as e:
                current_app.logger.warning(f"[billing] No se pudo actualizar metadata de la org: {e}")
        else:
            user_id, _, _, _, _ = _derive_identity()
            plan = "pro" if status in ("active", "trialing", "past_due") else "free"
            try:
                clerk_svc.set_user_plan(
                    user_id,
                    plan=plan,
                    status=status,
                    extra_private={
                        "billing": {
                            "stripeCustomerId": customer_id,
                            "subscriptionId": sub.get("id"),
                            "status": status,
                        }
                    },
                )
            except Exception as e:
                current_app.logger.warning(f"[billing] No se pudo actualizar metadata de usuario: {e}")

        return jsonify(ok=True), 200

    except Exception as e:
        current_app.logger.exception("sync_after_success error")
        return jsonify(error="sync failed", detail=str(e)), 502


# POST /api/checkout (compat genérico)
@bp.post("/checkout")
@_require_auth
def create_checkout():
    """
    Crea Stripe Checkout para suscripción.
    Body: {
      price_id: string,
      quantity?: number,
      is_org?: bool,
      org_id?: string   # opcional; si falta y is_org=True, creamos org
    }
    """
    _, err = _init_stripe()
    if err: return err

    body = request.get_json(silent=True) or {}
    price_id = (body.get("price_id") or _cfg("STRIPE_PRICE_ID") or "").strip()
    if not price_id:
        return jsonify(error="price_id required or STRIPE_PRICE_ID missing"), 400

    try:
        quantity = int(body.get("quantity") or 1)
    except Exception:
        quantity = 1
    if quantity < 1: quantity = 1

    user_id, user_email, user_name, ctx_org_id, _ = _derive_identity()
    is_org = bool(body.get("is_org"))
    org_id = (body.get("org_id") or ctx_org_id)

    # Si es compra para organización y no hay org, crearla y poner al comprador como owner
    if is_org and not org_id:
        try:
            name_guess = (user_name or user_email or f"org-{user_id}").split("@")[0]
            org = clerk_svc.create_org_for_user(
                user_id=user_id,
                name=name_guess,
                public={"plan": "enterprise", "seats": quantity, "subscription": "enterprise"},
                private={},
            )
            org_id = org.get("id")
        except Exception as e:
            current_app.logger.exception("[checkout] cannot create org")
            return jsonify(error="cannot create organization", detail=str(e)), 502

    entity_type = "org" if (is_org or org_id) else "user"
    entity_id = (org_id if entity_type == "org" else user_id)
    plan_scope = ("org" if entity_type == "org" else "user")

    # Asegurar customer
    try:
        if entity_type == "org":
            customer_id = _ensure_customer_for_org(entity_id)
        else:
            customer_id = _ensure_customer_for_user(entity_id)
    except Exception as e:
        current_app.logger.exception("ensure customer failed")
        return jsonify(error="cannot ensure stripe customer", detail=str(e)), 502

    success_url = _cfg("CHECKOUT_SUCCESS_URL") or f"{_front_base()}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = _cfg("CHECKOUT_CANCEL_URL")  or f"{_front_base()}/pricing?canceled=1"

    address_mode = _update_mode(_cfg("STRIPE_SAVE_ADDRESS_AUTO"), "auto")
    name_mode    = _update_mode(_cfg("STRIPE_SAVE_NAME_AUTO"), "auto")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": quantity}],
            success_url=success_url,
            cancel_url=cancel_url,
            allow_promotion_codes=True,
            subscription_data={
                "metadata": {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "plan_scope": plan_scope,
                }
            },
            metadata={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "plan_scope": plan_scope,
                "price_id": price_id,
            },
            tax_id_collection={"enabled": True},
            automatic_tax={"enabled": True},
            customer_update={"address": address_mode, "name": name_mode},
            locale=_cfg("STRIPE_CHECKOUT_LOCALE", "auto"),
        )
        return jsonify(url=session.url), 200
    except Exception as e:
        current_app.logger.exception("create checkout failed")
        return jsonify(error="checkout creation failed", detail=str(e)), 502


# POST /api/public/enterprise-checkout  (permite pre-checkout enterprise con email, sin login)
@bp.post("/public/enterprise-checkout")
def public_enterprise_checkout():
    _, err = _init_stripe()
    if err: return err

    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    if not email:
        return jsonify(error="email is required"), 400

    try:
        quantity = int(body.get("quantity") or 1)
    except Exception:
        quantity = 1
    if quantity < 1:
        quantity = 1

    price_id = (body.get("price_id") or _cfg("STRIPE_PRICE_ENTERPRISE_SEAT") or "").strip()
    if not price_id:
        return jsonify(error="price_id or STRIPE_PRICE_ENTERPRISE_SEAT required"), 400

    # Creamos customer "guest" asociado a org (se conectará en webhook al crear/ligar la org en Clerk)
    try:
        customer = stripe.Customer.create(
            email=email,
            metadata={"entity_type": "org", "entity_id": "", "entity_email": email, "plan_scope": "org"},
        )
    except Exception as e:
        current_app.logger.exception("[public_enterprise_checkout] Customer.create failed")
        return jsonify(error="cannot create stripe customer", detail=str(e)), 502

    success_url = _cfg("CHECKOUT_SUCCESS_URL") or f"{_front_base()}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = _cfg("CHECKOUT_CANCEL_URL")  or f"{_front_base()}/pricing?canceled=1"

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer.id,
            line_items=[{"price": price_id, "quantity": quantity}],
            success_url=success_url,
            cancel_url=cancel_url,
            allow_promotion_codes=True,
            subscription_data={"metadata": {"entity_type": "org", "entity_id": "", "plan_scope": "org", "plan": "enterprise"}},
            metadata={"entity_type": "org", "entity_id": "", "plan_scope": "org", "plan": "enterprise", "price_id": price_id, "seats": str(quantity)},
            tax_id_collection={"enabled": True},
            automatic_tax={"enabled": True},
            customer_update={"address": _update_mode(_cfg("STRIPE_SAVE_ADDRESS_AUTO"), "auto"),
                             "name": _update_mode(_cfg("STRIPE_SAVE_NAME_AUTO"), "auto")},
            locale=_cfg("STRIPE_CHECKOUT_LOCALE", "auto"),
        )
        return jsonify(url=session.url), 200
    except Exception as e:
        current_app.logger.exception("create public enterprise checkout failed")
        return jsonify(error="checkout creation failed", detail=str(e)), 502


# Diagnóstico
@bp.get("/_int/stripe-ping")
def stripe_ping():
    sk, err = _init_stripe()
    if err: return err
    try:
        acct = stripe.Account.retrieve()
        return jsonify(ok=True, account_id=acct.get("id"), email=acct.get("email"), country=acct.get("country")), 200
    except Exception as e:
        current_app.logger.exception("stripe-ping error")
        return jsonify(ok=False, error=str(e)), 502


# ────────────────────────────────────────────────────────────────
# Alias nuevos coherentes con el checklist: endpoints explícitos
# ────────────────────────────────────────────────────────────────

@bp.post("/billing/checkout/pro")
@_require_auth
def checkout_pro():
    """Checkout Pro (individual). Body: { interval: 'monthly'|'yearly' } -> {url} """
    _, err = _init_stripe()
    if err: return err
    body = request.get_json(silent=True) or {}
    interval = (body.get("interval") or "monthly").strip().lower()
    price_id = _cfg("STRIPE_PRICE_PRO_MONTHLY") if interval == "monthly" else _cfg("STRIPE_PRICE_PRO_YEARLY")
    if not price_id:
        return jsonify(error="Missing STRIPE_PRICE_PRO_MONTHLY/STRIPE_PRICE_PRO_YEARLY"), 500

    user_id, user_email, user_name, _, _ = _derive_identity()
    try:
        customer_id = _ensure_customer_for_user(user_id)
    except Exception as e:
        current_app.logger.exception("checkout_pro: ensure customer failed")
        return jsonify(error="cannot ensure stripe customer", detail=str(e)), 502

    success_url = _cfg("CHECKOUT_SUCCESS_URL") or f"{_front_base()}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = _cfg("CHECKOUT_CANCEL_URL")  or f"{_front_base()}/pricing?canceled=1"

    address_mode = _update_mode(_cfg("STRIPE_SAVE_ADDRESS_AUTO"), "auto")
    name_mode    = _update_mode(_cfg("STRIPE_SAVE_NAME_AUTO"), "auto")
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            allow_promotion_codes=True,
            subscription_data={
                "metadata": {
                    "entity_type": "user",
                    "entity_id": user_id,
                    "plan_scope": "user",
                    "plan": "pro",
                }
            },
            metadata={
                "entity_type": "user",
                "entity_id": user_id,
                "plan_scope": "user",
                "plan": "pro",
                "price_id": price_id,
            },
            tax_id_collection={"enabled": True},
            automatic_tax={"enabled": True},
            customer_update={"address": address_mode, "name": name_mode},
            locale=_cfg("STRIPE_CHECKOUT_LOCALE", "auto"),
        )
        return jsonify(url=session.url), 200
    except Exception as e:
        current_app.logger.exception("checkout_pro: Session.create failed")
        return jsonify(error="checkout creation failed", detail=str(e)), 502


@bp.post("/billing/checkout/enterprise")
@_require_auth
def checkout_enterprise():
    """Checkout Enterprise (por asientos).
    Body: { seats: number, org_id?: string } -> {url}
    Si no se pasa org_id, se crea una organización para el comprador.
    """
    _, err = _init_stripe()
    if err: return err
    body = request.get_json(silent=True) or {}
    try:
        seats = int(body.get("seats") or 1)
    except Exception:
        seats = 1
    if seats < 1: seats = 1

    price_id = (_cfg("STRIPE_PRICE_ENTERPRISE_SEAT") or "").strip()
    if not price_id:
        return jsonify(error="Missing STRIPE_PRICE_ENTERPRISE_SEAT"), 500

    user_id, user_email, user_name, ctx_org_id, _ = _derive_identity()
    org_id = body.get("org_id") or ctx_org_id

    # Crear organización si no existe
    if not org_id:
        try:
            wanted_name = (user_name or user_email or f"org-{user_id}").split("@")[0]
            org = clerk_svc.create_org_for_user(
                user_id=user_id,
                name=wanted_name,
                public={"plan": "enterprise", "seats": seats, "subscription": "enterprise"},
                private={},
            )
            org_id = org.get("id")
        except Exception as e:
            current_app.logger.exception("checkout_enterprise: create org failed")
            return jsonify(error="cannot create organization", detail=str(e)), 502

    # Customer en Stripe para la org
    try:
        customer_id = _ensure_customer_for_org(org_id)
    except Exception as e:
        current_app.logger.exception("checkout_enterprise: ensure org customer failed")
        return jsonify(error="cannot ensure stripe customer", detail=str(e)), 502

    success_url = _cfg("CHECKOUT_SUCCESS_URL") or f"{_front_base()}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = _cfg("CHECKOUT_CANCEL_URL")  or f"{_front_base()}/pricing?canceled=1"

    address_mode = _update_mode(_cfg("STRIPE_SAVE_ADDRESS_AUTO"), "auto")
    name_mode    = _update_mode(_cfg("STRIPE_SAVE_NAME_AUTO"), "auto")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": seats}],
            success_url=success_url,
            cancel_url=cancel_url,
            allow_promotion_codes=True,
            subscription_data={
                "metadata": {
                    "entity_type": "org",
                    "entity_id": org_id,
                    "plan_scope": "org",
                    "plan": "enterprise",
                }
            },
            metadata={
                "entity_type": "org",
                "entity_id": org_id,
                "plan_scope": "org",
                "plan": "enterprise",
                "price_id": price_id,
                "seats": str(seats),
            },
            tax_id_collection={"enabled": True},
            automatic_tax={"enabled": True},
            customer_update={"address": address_mode, "name": name_mode},
            locale=_cfg("STRIPE_CHECKOUT_LOCALE", "auto"),
        )
        return jsonify(url=session.url), 200
    except Exception as e:
        current_app.logger.exception("checkout_enterprise: Session.create failed")
        return jsonify(error="checkout creation failed", detail=str(e)), 502


@bp.post("/billing/portal")
@_require_auth
def portal_post():
    """Portal de facturación. Body: { context: 'user'|'org', org_id?: string } -> {url} """
    _, err = _init_stripe()
    if err: return err
    data = request.get_json(silent=True) or {}
    context = (data.get("context") or "user").strip().lower()
    body_org_id = data.get("org_id")
    user_id, _, _, ctx_org_id, _ = _derive_identity()
    org_id = body_org_id or ctx_org_id

    try:
        if context == "org":
            if not org_id:
                return jsonify(error="org_id required for context=org"), 400
            if not _is_org_admin(user_id, org_id):
                return jsonify(error="forbidden: organization admin required"), 403
            customer_id = _ensure_customer_for_org(org_id)
        else:
            customer_id = _ensure_customer_for_user(user_id)
        return_url = _cfg("STRIPE_BILLING_PORTAL_RETURN_URL") or f"{_front_base()}/account/billing"
        ps = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
        return jsonify(url=ps.url), 200
    except Exception as e:
        current_app.logger.exception("portal_post error")
        return jsonify(error="portal creation failed", detail=str(e)), 502
