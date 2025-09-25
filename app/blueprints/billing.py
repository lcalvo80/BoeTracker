# app/blueprints/billing.py
from __future__ import annotations
import os
import stripe
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from flask import Blueprint, request, jsonify, current_app, g

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
        current_app.logger.warning(f"[billing] auth decorator no disponible: {e}")
        def _noop(fn):
            def w(*a, **k): return fn(*a, **k)
            return w
        return _noop

_require_auth = _load_auth_guard()

# ───────────────── Clerk Admin API ─────────────────
def _clerk_headers() -> Dict[str, str]:
    sk = _cfg("CLERK_SECRET_KEY", "")
    if not sk:
        raise RuntimeError("Missing CLERK_SECRET_KEY")
    return {"Authorization": f"Bearer {sk}", "Content-Type": "application/json"}

def _clerk_base() -> str:
    return "https://api.clerk.com/v1"

def _clerk_get_user(user_id: str) -> Dict[str, Any]:
    r = requests.get(f"{_clerk_base()}/users/{user_id}", headers=_clerk_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def _clerk_update_user_metadata(user_id: str, public: Optional[Dict]=None, private: Optional[Dict]=None):
    body: Dict[str, Any] = {}
    if public is not None:
        body["public_metadata"] = public
    if private is not None:
        body["private_metadata"] = private
    if not body:
        return
    r = requests.patch(f"{_clerk_base()}/users/{user_id}", headers=_clerk_headers(), json=body, timeout=10)
    r.raise_for_status()
    return r.json()

def _clerk_get_org(org_id: str) -> Dict[str, Any]:
    r = requests.get(f"{_clerk_base()}/organizations/{org_id}", headers=_clerk_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def _clerk_update_org_metadata(org_id: str, public: Optional[Dict]=None, private: Optional[Dict]=None):
    # merge-friendly: lee primero para no pisar
    org = _clerk_get_org(org_id)
    body: Dict[str, Any] = {}
    if public is not None:
        merged = dict(org.get("public_metadata") or {})
        merged.update(public)
        body["public_metadata"] = merged
    if private is not None:
        mergedp = dict(org.get("private_metadata") or {})
        mergedp.update(private)
        body["private_metadata"] = mergedp
    if not body:
        return org
    r = requests.patch(f"{_clerk_base()}/organizations/{org_id}", headers=_clerk_headers(), json=body, timeout=10)
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
        c.get("user_id") or "dev_user",
        c.get("email"),
        c.get("name") or (c.get("user_id") or "dev_user"),
        c.get("org_id"),
        (c.get("raw_claims", {}) or {}).get("org_role") or None,
    )

# ───────────────── Stripe customer helpers ─────────────────
def _search_customer_by_email(email: str | None) -> str | None:
    if not email:
        return None
    try:
        res = stripe.Customer.search(query=f'email:"{email}"', limit=1)
        if res and res.get("data"):
            return res["data"][0]["id"]
    except Exception as e:
        current_app.logger.info(f"[billing] Customer.search no disponible o sin resultados: {e}")
    return None

def _ensure_customer_for_user(user_id: str) -> str:
    # 1) leer de Clerk private_metadata
    try:
        u = _clerk_get_user(user_id)
        priv = (u.get("private_metadata") or {})
        existing = (priv.get("billing") or {}).get("stripeCustomerId") or priv.get("stripe_customer_id")
        if existing:
            return existing
        # construir info básica
        email = None
        try:
            emails = u.get("email_addresses") or []
            pid = u.get("primary_email_address_id")
            primary = next((e for e in emails if e.get("id") == pid), emails[0] if emails else None)
            email = primary.get("email_address") if primary else None
        except Exception:
            pass
        name = " ".join(filter(None, [u.get("first_name"), u.get("last_name")])) or u.get("username") or u.get("id")
    except Exception:
        # fallback: g.clerk
        uid, email, name, _, _ = _derive_identity()
        user_id = uid  # asegurar
    # 2) reusar por email
    _init_stripe()
    cid = _search_customer_by_email(locals().get("email"))
    if cid:
        try:
            _clerk_update_user_metadata(user_id, private={"billing": {"stripeCustomerId": cid}})
        except Exception:
            pass
        return cid
    # 3) crear
    customer = stripe.Customer.create(
        email=locals().get("email"),
        name=locals().get("name") or user_id,
        metadata={"clerk_user_id": user_id, "entity_type": "user", "entity_id": user_id},
    )
    try:
        _clerk_update_user_metadata(user_id, private={"billing": {"stripeCustomerId": customer.id}})
    except Exception:
        pass
    return customer.id

def _ensure_customer_for_org(org_id: str) -> str:
    # 1) leer de org.private_metadata
    org = _clerk_get_org(org_id)
    priv = org.get("private_metadata") or {}
    existing = (priv.get("billing") or {}).get("stripeCustomerId")
    if existing:
        return existing
    # 2) construir nombre/email de facturación (opcional)
    name = org.get("name") or org_id
    # 3) crear
    _init_stripe()
    customer = stripe.Customer.create(
        name=name,
        metadata={"clerk_org_id": org_id, "entity_type": "org", "entity_id": org_id},
    )
    # 4) persistir en Clerk
    _clerk_update_org_metadata(org_id, private={"billing": {"stripeCustomerId": customer.id}})
    return customer.id

def _subscription_summary(customer_id: str) -> dict:
    _init_stripe()
    subs = stripe.Subscription.list(customer=customer_id, status="all", limit=10)
    sub = None
    for s in subs.auto_paging_iter():
        sub = s
        if s.status in ("active", "trialing", "past_due", "unpaid"):
            break
    if not sub:
        return {"plan_name": "Free", "status": "none", "current_period_end": None, "payment_method": None}

    price = sub["items"]["data"][0]["price"] if sub["items"]["data"] else None
    qty = sub["items"]["data"][0]["quantity"] if sub["items"]["data"] else 1
    plan_name = price.get("nickname") or price.get("id") if price else "Plan"
    period_end = sub.get("current_period_end")
    period_end_iso = datetime.fromtimestamp(period_end, tz=timezone.utc).isoformat() if period_end else None

    cust = stripe.Customer.retrieve(customer_id, expand=["invoice_settings.default_payment_method"])
    pm = cust.get("invoice_settings", {}).get("default_payment_method")
    pm_info = None
    if pm and pm.get("card"):
        pm_info = {"brand": pm["card"]["brand"], "last4": pm["card"]["last4"]}
    elif pm and pm.get("sepa_debit"):
        pm_info = {"brand": "sepa_debit", "last4": pm["sepa_debit"]["last4"]}

    return {
        "plan_name": plan_name,
        "status": sub["status"],
        "current_period_end": period_end_iso,
        "payment_method": pm_info,
        "quantity": qty,
        "subscription_id": sub.get("id"),
    }

# ───────────────── Endpoints ─────────────────

@bp.post("/checkout")
@_require_auth
def create_checkout():
    """
    Crea Stripe Checkout para suscripción.
    Body: {
      price_id: string,
      quantity?: number,
      is_org?: bool,
      org_id?: string   # opcional; si falta y is_org=True, usamos g.clerk.org_id
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

    user_id, _, _, ctx_org_id, _ = _derive_identity()
    is_org = bool(body.get("is_org"))
    org_id = (body.get("org_id") or ctx_org_id)

    # Resolver customer (user u org)
    try:
        if is_org:
            if not org_id:
                return jsonify(error="org_id requerido para checkout de organización"), 400
            # Sólo admins deben poder comprar asientos
            if not _is_org_admin(user_id, org_id):
                return jsonify(error="forbidden: organization admin required"), 403
            customer_id = _ensure_customer_for_org(org_id)
            entity_type, entity_id, plan_scope = "org", org_id, "org"
        else:
            customer_id = _ensure_customer_for_user(user_id)
            entity_type, entity_id, plan_scope = "user", user_id, "user"
    except stripe.error.AuthenticationError as e:
        current_app.logger.exception("Stripe auth error creando/obteniendo customer")
        return jsonify(error="stripe authentication error", detail=str(e)), 502
    except Exception as e:
        current_app.logger.exception("No se pudo asegurar el customer en Stripe")
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
            locale=_cfg("STRIPE_CHECKOUT_LOCALE", "auto") or "auto",
            automatic_tax={"enabled": _truthy(_cfg("STRIPE_AUTOMATIC_TAX", "true") or "true")},
            billing_address_collection=("required" if _truthy(_cfg("STRIPE_REQUIRE_BILLING_ADDRESS", "true") or "true") else "auto"),
            customer_update={"address": address_mode, "name": name_mode},
            customer_email=None,
        )
    except Exception as e:
        current_app.logger.exception("Error creando Stripe Checkout")
        return jsonify(error="checkout creation failed", detail=str(e)), 502

    return jsonify(checkout_url=session.url), 200


# Compat (existente en tu front legacy): POST /api/portal -> { portal_url }
@bp.post("/portal")
@_require_auth
def create_portal_compat():
    _, err = _init_stripe()
    if err: return err
    user_id, _, _, org_id, _ = _derive_identity()
    # Si el usuario está en una org y es admin, intentamos portal de la org; si no, del usuario.
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


# Nuevo (usado por el Settings BillingSection): GET /api/billing/portal -> { url }
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


# Nuevo: GET /api/billing/summary  (por defecto, del usuario; admite ?scope=org)
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
        # Reflejar plan/seats en Clerk
        if data.get("subscription_id"):
            if scope == "org" and org_id and _is_org_admin(user_id, org_id):
                qty = int(data.get("quantity") or 1)
                try:
                    _clerk_update_org_metadata(org_id, public={"seats": qty}, private={"billing": {
                        "stripeCustomerId": customer_id,
                        "subscriptionId": data["subscription_id"],
                        "status": data["status"],
                    }})
                except Exception:
                    pass
            else:
                plan = "pro" if data["status"] in ("active", "trialing", "past_due") else "free"
                try:
                    _clerk_update_user_metadata(user_id, public={"plan": plan}, private={"billing": {
                        "stripeCustomerId": customer_id,
                        "subscriptionId": data["subscription_id"],
                        "status": data["status"],
                    }})
                except Exception:
                    pass
        return jsonify(data), 200
    except Exception as e:
        current_app.logger.exception("summary_get error")
        return jsonify(error="summary failed", detail=str(e)), 502


# Nuevo: GET /api/billing/invoices (user por defecto; admite ?scope=org)
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


# ── ALIAS ESTABLE: usado por el frontend moderno ──
@bp.post("/billing/sync")
@_require_auth
def billing_sync_alias():
    # reutiliza la misma lógica del handler principal
    return sync_after_success()


# Compat (fronts antiguos): POST /api/sync
@bp.post("/sync")
@_require_auth
def sync_after_success():
    """
    Sincroniza tras volver de Stripe Checkout usando session_id.
    - Detecta si la compra fue de usuario (plan_scope=user) u organización (plan_scope=org).
    - Actualiza metadata correspondiente en Clerk.
    """
    _, err = _init_stripe()
    if err: return err
    b = request.get_json(silent=True) or {}
    sid = (b.get("session_id") or "").strip()
    if not sid:
        return jsonify(error="session_id is required"), 400

    try:
        sess = stripe.checkout.Session.retrieve(
            sid,
            expand=["subscription", "subscription.items.data.price", "subscription.items.data"]
        )
        sub = sess.get("subscription") or {}
        status = sub.get("status") or "active"
        items = sub.get("items", {}).get("data") or []
        qty = int(items[0]["quantity"]) if items else 1
        scope = (sub.get("metadata", {}) or {}).get("plan_scope") or (sess.get("metadata", {}) or {}).get("plan_scope")
        entity_type = (sub.get("metadata", {}) or {}).get("entity_type") or (sess.get("metadata", {}) or {}).get("entity_type")
        entity_id = (sub.get("metadata", {}) or {}).get("entity_id") or (sess.get("metadata", {}) or {}).get("entity_id")
        customer_id = sess.get("customer")

        if scope == "org" and entity_type == "org" and entity_id:
            # Actualizar organización (seats = qty) y billing metadata
            try:
                _clerk_update_org_metadata(entity_id,
                    public={"seats": qty},
                    private={"billing": {
                        "stripeCustomerId": customer_id,
                        "subscriptionId": sub.get("id"),
                        "status": status
                    }})
            except Exception as e:
                current_app.logger.warning(f"[billing] No se pudo actualizar metadata de la org: {e}")
        else:
            # Compra personal
            user_id, _, _, _, _ = _derive_identity()
            plan = "pro" if status in ("active", "trialing", "past_due") else "free"
            try:
                _clerk_update_user_metadata(user_id,
                    public={"plan": plan},
                    private={"billing": {
                        "stripeCustomerId": customer_id,
                        "subscriptionId": sub.get("id"),
                        "status": status
                    }})
            except Exception as e:
                current_app.logger.warning(f"[billing] No se pudo actualizar metadata de usuario: {e}")

        return jsonify(ok=True), 200

    except Exception as e:
        current_app.logger.exception("sync_after_success error")
        return jsonify(error="sync failed", detail=str(e)), 502


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
