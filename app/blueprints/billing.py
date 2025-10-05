# app/blueprints/billing.py
from __future__ import annotations
import os
import stripe
from typing import Optional, Dict, Any

from flask import Blueprint, request, jsonify, current_app, g
from app.services import clerk_svc  # get_user, get_org, update_*, create_org_for_user, set_* , get_membership

bp = Blueprint("billing", __name__, url_prefix="/api")

# ─────────── helpers config ───────────
def _cfg(k: str, default: str | None = None) -> str | None:
    try:
        v = current_app.config.get(k)
    except Exception:
        v = None
    if v is None or str(v).strip() == "":
        v = os.getenv(k, default)
    return v

def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")

def _front_base() -> str:
    return (_cfg("FRONTEND_URL", "http://localhost:5173") or "").rstrip("/")

def _init_stripe():
    sk = _cfg("STRIPE_SECRET_KEY")
    if not sk:
        return None, (jsonify(error="Missing STRIPE_SECRET_KEY"), 500)
    stripe.api_key = sk
    return sk, None

def _get_price(kind: str) -> str | None:
    """Lee price ids desde múltiples envs por compatibilidad."""
    def first(*keys):
        for kk in keys:
            v = _cfg(kk)
            if v and str(v).strip():
                return str(v).strip()
        return None
    k = (kind or "").lower()
    if k == "pro_monthly":
        return first("STRIPE_PRICE_PRO_MONTHLY", "STRIPE_PRICE_PRO_MONTHLY_ID", "PRICE_PRO_MONTHLY_ID")
    if k == "pro_yearly":
        return first("STRIPE_PRICE_PRO_YEARLY", "STRIPE_PRICE_PRO_YEARLY_ID", "PRICE_PRO_YEARLY_ID")
    if k == "enterprise_seat":
        return first("STRIPE_PRICE_ENTERPRISE_SEAT", "STRIPE_PRICE_ENTERPRISE_SEAT_ID", "PRICE_ENTERPRISE_SEAT_ID")
    return None

# Solo valores válidos para Stripe: 'auto' | 'never'
def _update_mode(v: str | None, default: str = "auto") -> str:
    s = (v or default).strip().lower()
    return "auto" if s in ("auto", "1", "true", "yes", "on") else "never"

# ─────────── auth guard ───────────
def _load_auth_guard():
    if _truthy(_cfg("DISABLE_AUTH", "0") or "0"):
        def _noop(fn):
            def wrapper(*a, **kw):
                g.clerk = {"user_id": "dev_user", "org_id": None, "email": "dev@example.com", "name": "Dev"}
                return fn(*a, **kw)
            return wrapper
        return _noop
    from app.auth import require_clerk_auth
    return require_clerk_auth

_require_auth = _load_auth_guard()

# ─────────── identidad ───────────
def _derive_identity():
    c = getattr(g, "clerk", {}) or {}
    return c.get("user_id"), c.get("email"), c.get("name"), c.get("org_id"), c.get("raw_claims") or {}

def _is_org_admin(user_id: str, org_id: str) -> bool:
    try:
        m = clerk_svc.get_membership(user_id, org_id)
        role = (m.get("role") or "member").lower()
        return role in ("admin", "owner")
    except Exception:
        return False

# ─────────── customers helpers ───────────
def _ensure_customer_for_user(user_id: str) -> str:
    _, err = _init_stripe()
    if err: raise RuntimeError("stripe init failed")
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
    c = stripe.Customer.create(email=email or None, name=name or None,
                               metadata={"entity_type": "user", "entity_id": user_id})
    try:
        clerk_svc.update_user_metadata(user_id, private={"billing": {"stripeCustomerId": c.id}})
    except Exception:
        current_app.logger.exception("cannot persist stripeCustomerId on clerk user")
    return c.id

def _ensure_customer_for_org(org_id: str) -> str:
    _, err = _init_stripe()
    if err: raise RuntimeError("stripe init failed")
    try:
        org = clerk_svc.get_org(org_id)
        priv = (org.get("private_metadata") or {})
        existing = (priv.get("billing") or {}).get("stripeCustomerId")
        if existing:
            return existing
        name = (org.get("name") or f"org-{org_id}")
    except Exception:
        name = f"org-{org_id}"
    c = stripe.Customer.create(name=name or None, metadata={"entity_type": "org", "entity_id": org_id})
    try:
        clerk_svc.update_org_metadata(org_id, private={"billing": {"stripeCustomerId": c.id}})
    except Exception:
        current_app.logger.exception("cannot persist stripeCustomerId on clerk org")
    return c.id

def _payment_method_summary(customer_id: str) -> Dict[str, Any] | None:
    """Intenta devolver {'brand': 'visa', 'last4': '4242'} del método por defecto."""
    try:
        cust = stripe.Customer.retrieve(customer_id, expand=["invoice_settings.default_payment_method"])
        pm = (cust.get("invoice_settings") or {}).get("default_payment_method")
        if isinstance(pm, dict) and pm.get("card"):
            card = pm["card"]
            return {"brand": card.get("brand"), "last4": card.get("last4")}
        # fallback: primer PM de tipo card
        pms = stripe.PaymentMethod.list(customer=customer_id, type="card", limit=1)
        first = next(iter(pms.auto_paging_iter()), None)
        if first and first.get("card"):
            return {"brand": first["card"].get("brand"), "last4": first["card"].get("last4")}
    except Exception:
        pass
    return None

# ─────────── Endpoints ───────────

@bp.post("/billing/portal")
@_require_auth
def portal_post():
    _, err = _init_stripe()
    if err: return err
    body = request.get_json(silent=True) or {}
    context = (body.get("context") or "user").lower()
    user_id, _, _, org_id, _ = _derive_identity()
    try:
        if context == "org":
            if not org_id or not _is_org_admin(user_id, org_id):
                return jsonify(error="forbidden: organization admin required"), 403
            customer_id = _ensure_customer_for_org(org_id)
        else:
            customer_id = _ensure_customer_for_user(user_id)
        return_url = _cfg("STRIPE_BILLING_PORTAL_RETURN_URL") or f"{_front_base()}/account"
        ps = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
        return jsonify(url=ps.url), 200
    except Exception as e:
        current_app.logger.exception("portal_post error: %s", e)
        return jsonify(error="portal creation failed", detail=str(e)), 502

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
        current_app.logger.exception("portal_get error: %s", e)
        return jsonify(error="portal creation failed", detail=str(e)), 502

@bp.get("/billing/summary")
@_require_auth
def summary_get():
    _, err = _init_stripe()
    if err: return err
    scope = (request.args.get("scope") or "user").lower()
    user_id, _, _, org_id, _ = _derive_identity()
    try:
        if scope == "org":
            if not org_id or not _is_org_admin(user_id, org_id):
                return jsonify(error="forbidden"), 403
            customer_id = _ensure_customer_for_org(org_id)
        else:
            customer_id = _ensure_customer_for_user(user_id)

        subs = stripe.Subscription.list(customer=customer_id, limit=1, status="all")
        sub = next(iter(subs.auto_paging_iter()), None)
        status = (sub.get("status") if sub else "canceled") or "canceled"
        is_active = status in ("active", "trialing", "past_due")

        # enriquecido
        current_period_end = sub.get("current_period_end") if sub else None  # unix ts
        payment_method = _payment_method_summary(customer_id)

        if scope == "org":
            plan = "enterprise" if is_active else "free"
            seats = int(sub["items"]["data"][0]["quantity"] or 0) if sub and sub.get("items", {}).get("data") else 0
            return jsonify({
                "status": status,
                "plan": plan,
                "seats": seats,
                "current_period_end": current_period_end,
                "payment_method": payment_method,
            }), 200
        else:
            plan = "pro" if is_active else "free"
            return jsonify({
                "status": status,
                "plan": plan,
                "current_period_end": current_period_end,
                "payment_method": payment_method,
            }), 200
    except Exception as e:
        current_app.logger.exception("summary_get failed: %s", e)
        return jsonify(error="summary failed", detail=str(e)), 502

@bp.get("/billing/invoices")
@_require_auth
def invoices_get():
    _, err = _init_stripe()
    if err: return err
    scope = (request.args.get("scope") or "user").lower()
    user_id, _, _, org_id, _ = _derive_identity()
    try:
        if scope == "org":
            if not org_id or not _is_org_admin(user_id, org_id):
                return jsonify(error="forbidden"), 403
            customer_id = _ensure_customer_for_org(org_id)
        else:
            customer_id = _ensure_customer_for_user(user_id)

        invs = stripe.Invoice.list(customer=customer_id, limit=20)
        out = []
        for inv in invs.auto_paging_iter():
            out.append({
                "id": inv.get("id"),
                "status": inv.get("status"),
                "currency": inv.get("currency"),
                "amount_due": inv.get("amount_due"),
                "amount_paid": inv.get("amount_paid"),
                "amount_remaining": inv.get("amount_remaining"),
                "created": inv.get("created"),
                "hosted_invoice_url": inv.get("hosted_invoice_url"),
                "invoice_pdf": inv.get("invoice_pdf"),
                "number": inv.get("number"),
                "period_start": inv.get("period_start"),
                "period_end": inv.get("period_end"),
            })
        return jsonify({"data": out}), 200
    except Exception as e:
        current_app.logger.exception("invoices_get failed: %s", e)
        return jsonify({"data": []}), 200  # no rompemos UI

@bp.post("/billing/sync")
@_require_auth
def billing_sync():
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
        sub = next(iter(subs.auto_paging_iter()), None)
        status = (sub.get("status") if sub else "canceled") or "canceled"
        is_active = status in ("active", "trialing", "past_due")

        if scope == "org":
            org_id = _derive_identity()[3]
            qty = (sub["items"]["data"][0]["quantity"] if sub and sub.get("items") and sub["items"].get("data") else 0)
            clerk_svc.update_org_metadata(
                org_id,
                public={
                    "subscription": ("enterprise" if is_active else None),
                    "plan": ("enterprise" if is_active else "free"),  # 👈 añade plan consistente
                    "seats": int(qty or 0),
                },
                private={
                    "billing": {
                        "stripeCustomerId": sub.get("customer") if sub else None,
                        "subscriptionId": sub.get("id") if sub else None,
                        "status": status
                    }
                },
            )
        else:
            user_id = _derive_identity()[0]
            clerk_svc.update_user_metadata(
                user_id,
                public={
                    "subscription": ("pro" if is_active else None),
                    "plan": ("pro" if is_active else "free"),          # 👈 también para usuario
                },
                private={
                    "billing": {
                        "stripeCustomerId": sub.get("customer") if sub else None,
                        "subscriptionId": sub.get("id") if sub else None,
                        "status": status
                    }
                },
            )

        return jsonify(ok=True, status=status), 200
    except Exception as e:
        current_app.logger.exception("billing_sync failed: %s", e)
        return jsonify(error="sync failed", detail=str(e)), 502

@bp.post("/billing/checkout/pro")
@_require_auth
def checkout_pro():
    _, err = _init_stripe()
    if err: return err
    body = request.get_json(silent=True) or {}
    interval = (body.get("interval") or "monthly").strip().lower()
    price_id = _get_price("pro_monthly") if interval == "monthly" else _get_price("pro_yearly")
    if not price_id:
        return jsonify(error="Missing STRIPE_PRICE_PRO_MONTHLY/STRIPE_PRICE_PRO_YEARLY (o aliases PRICE_*_ID)"), 500

    user_id, user_email, user_name, _, _ = _derive_identity()
    try:
        customer_id = _ensure_customer_for_user(user_id)
    except Exception as e:
        current_app.logger.exception("ensure customer failed: %s", e)
        return jsonify(error="cannot ensure stripe customer", detail=str(e)), 502

    success_url = _cfg("CHECKOUT_SUCCESS_URL") or f"{_front_base()}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = _cfg("CHECKOUT_CANCEL_URL")  or f"{_front_base()}/pricing?canceled=1"

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            allow_promotion_codes=True,
            subscription_data={"metadata": {
                "entity_type": "user", "entity_id": user_id, "plan_scope": "user", "plan": "pro"
            }},
            metadata={
                "entity_type": "user", "entity_id": user_id, "plan_scope": "user", "plan": "pro",
                "price_id": price_id, "entity_email": user_email or "", "entity_name": user_name or ""
            },
            tax_id_collection={"enabled": True},
            automatic_tax={"enabled": True},
            customer_update={"address": _update_mode(_cfg("STRIPE_SAVE_ADDRESS_AUTO"), "auto"),
                             "name": _update_mode(_cfg("STRIPE_SAVE_NAME_AUTO"), "auto")},
            locale=_cfg("STRIPE_CHECKOUT_LOCALE", "auto"),
        )
        return jsonify(url=session.url), 200
    except Exception as e:
        current_app.logger.exception("create pro checkout failed: %s", e)
        return jsonify(error="checkout creation failed", detail=str(e)), 502

@bp.post("/billing/checkout/enterprise")
@_require_auth
def checkout_enterprise():
    """
    Body: { seats: number, org_id?: string } → { url }
    Si no se pasa org_id, se crea una organización para el comprador (admin).
    """
    _, err = _init_stripe()
    if err: return err
    body = request.get_json(silent=True) or {}
    try:
        seats = int(body.get("seats") or 1)
    except Exception:
        seats = 1
    if seats < 1:
        seats = 1

    price_id = (_get_price("enterprise_seat") or "").strip()
    if not price_id:
        return jsonify(error="Missing STRIPE_PRICE_ENTERPRISE_SEAT (o aliases *_SEAT_ID)"), 500

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
            current_app.logger.exception("[checkout] cannot create org: %s", e)
            return jsonify(error="cannot create organization", detail=str(e)), 502

    # Asegurar customer
    try:
        customer_id = _ensure_customer_for_org(org_id)
    except Exception as e:
        current_app.logger.exception("ensure customer failed: %s", e)
        return jsonify(error="cannot ensure stripe customer", detail=str(e)), 502

    success_url = _cfg("CHECKOUT_SUCCESS_URL") or f"{_front_base()}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = _cfg("CHECKOUT_CANCEL_URL")  or f"{_front_base()}/pricing?canceled=1"

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": seats}],
            success_url=success_url,
            cancel_url=cancel_url,
            allow_promotion_codes=True,
            subscription_data={"metadata": {
                "entity_type": "org", "entity_id": org_id, "plan_scope": "org", "plan": "enterprise",
                "seats": str(seats)
            }},
            metadata={
                "entity_type": "org", "entity_id": org_id, "plan_scope": "org", "plan": "enterprise",
                "price_id": price_id, "entity_email": user_email or "", "entity_name": user_name or "",
                "seats": str(seats)
            },
            tax_id_collection={"enabled": True},
            automatic_tax={"enabled": True},
            customer_update={"address": _update_mode(_cfg("STRIPE_SAVE_ADDRESS_AUTO"), "auto"),
                             "name": _update_mode(_cfg("STRIPE_SAVE_NAME_AUTO"), "auto")},
            locale=_cfg("STRIPE_CHECKOUT_LOCALE", "auto"),
        )
        return jsonify(url=session.url), 200
    except Exception as e:
        current_app.logger.exception("create enterprise checkout failed: %s", e)
        return jsonify(error="checkout creation failed", detail=str(e)), 502

@bp.post("/public/enterprise-checkout")
def public_enterprise_checkout():
    _, err = _init_stripe()
    if err: return err

    body = request.get_json(silent=True) or {}
    price_id = (body.get("price_id") or _get_price("enterprise_seat") or "").strip()
    if not price_id:
        return jsonify(error="price_id required or STRIPE_PRICE_ENTERPRISE_SEAT missing"), 400

    try:
        quantity = int(body.get("seats") or 1)
    except Exception:
        quantity = 1
    if quantity < 1:
        quantity = 1

    buyer_email = (body.get("email") or "").strip().lower()
    if not buyer_email:
        return jsonify(error="email required"), 400

    success_url = _cfg("CHECKOUT_SUCCESS_URL") or f"{_front_base()}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = _cfg("CHECKOUT_CANCEL_URL")  or f"{_front_base()}/pricing?canceled=1"

    try:
        customer = stripe.Customer.create(
            email=buyer_email,
            metadata={"entity_type": "org", "entity_id": "", "plan_scope": "org", "plan": "enterprise",
                      "entity_email": buyer_email},
        )
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer.id,
            line_items=[{"price": price_id, "quantity": quantity}],
            success_url=success_url,
            cancel_url=cancel_url,
            allow_promotion_codes=True,
            subscription_data={"metadata": {"entity_type": "org", "entity_id": "", "plan_scope": "org", "plan": "enterprise"}},
            metadata={"entity_type": "org", "entity_id": "", "plan_scope": "org", "plan": "enterprise",
                      "price_id": price_id, "entity_email": buyer_email, "seats": str(quantity)},
            tax_id_collection={"enabled": True},
            automatic_tax={"enabled": True},
            customer_update={"address": _update_mode(_cfg("STRIPE_SAVE_ADDRESS_AUTO"), "auto"),
                             "name": _update_mode(_cfg("STRIPE_SAVE_NAME_AUTO"), "auto")},
            locale=_cfg("STRIPE_CHECKOUT_LOCALE", "auto"),
        )
        return jsonify(url=session.url), 200
    except Exception as e:
        current_app.logger.exception("create public enterprise checkout failed: %s", e)
        return jsonify(error="checkout creation failed", detail=str(e)), 502

@bp.get("/_int/stripe-ping")
def stripe_ping():
    sk, err = _init_stripe()
    if err: return err
    try:
        acct = stripe.Account.retrieve()
        return jsonify({"ok": True, "account": acct.get("id")}), 200
    except Exception as e:
        current_app.logger.exception("stripe ping failed: %s", e)
        return jsonify(error="stripe ping failed", detail=str(e)), 502
