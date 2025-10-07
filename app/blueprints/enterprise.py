# app/blueprints/billing.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional, List

import requests
from flask import Blueprint, jsonify, request, g, current_app

from app.auth import require_clerk_auth

bp = Blueprint("billing", __name__, url_prefix="/api/billing")

# ───────── helpers ─────────
def _cfg(k: str, default: Optional[str] = None) -> str:
    try:
        v = current_app.config.get(k)  # type: ignore[attr-defined]
    except Exception:
        v = None
    if v is None or str(v).strip() == "":
        v = os.getenv(k, default or "")
    return str(v or "")

def _headers_json() -> Dict[str, str]:
    sk = _cfg("CLERK_SECRET_KEY", "")
    if not sk:
        raise RuntimeError("Missing CLERK_SECRET_KEY")
    return {"Authorization": f"Bearer {sk}", "Content-Type": "application/json"}

def _clerk_base() -> str:
    return "https://api.clerk.com/v1"

def _map_role_out(role: str) -> str:
    r = (role or "").strip().lower()
    if r in ("admin", "owner", "org:admin", "organization_admin"):
        return "admin"
    return "member"

def _current_user_ids() -> tuple[str, Optional[str]]:
    c = getattr(g, "clerk", {}) or {}
    org_from_req = request.headers.get("X-Org-Id") or request.args.get("org_id")
    org_id = org_from_req or c.get("org_id")
    return c.get("user_id"), org_id

def _get_org(org_id: str) -> dict:
    r = requests.get(f"{_clerk_base()}/organizations/{org_id}", headers=_headers_json(), timeout=10)
    r.raise_for_status()
    return r.json()

def _is_admin_for_org(user_id: str, org_id: str) -> bool:
    # si el token ya dice admin…
    me = getattr(g, "clerk", {}) or {}
    if me.get("user_id") == user_id and me.get("org_id") == org_id and me.get("org_role") == "admin":
        return True
    # consulta rápida a Clerk
    try:
        q = requests.get(
            f"{_clerk_base()}/organizations/{org_id}/memberships?limit=1&user_id={user_id}",
            headers=_headers_json(),
            timeout=10,
        )
        q.raise_for_status()
        data = q.json()
        arr = data if isinstance(data, list) else data.get("data") or []
        role = (arr[0].get("role") or "").lower() if arr else ""
        return role in ("admin", "owner", "organization_admin", "org:admin")
    except Exception:
        return False

# ───────── Stripe helpers ─────────
def _stripe_enabled() -> bool:
    return bool(_cfg("STRIPE_SECRET_KEY"))

def _stripe_init():
    import stripe  # type: ignore
    stripe.api_key = _cfg("STRIPE_SECRET_KEY")
    return stripe

def _org_customer_id(org: dict) -> Optional[str]:
    pm = org.get("public_metadata") or {}
    # soporta varios alias
    for k in ("stripe_customer_id", "stripe_customer", "customer_id", "stripe_cus"):
        v = pm.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None

def _find_or_user_customer_id(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    stripe = _stripe_init()
    try:
        res = stripe.Customer.search(query=f"email:'{email}'", limit=1)
        for c in res.auto_paging_iter():
            return c.id
    except Exception:
        pass
    return None

def _serialize_invoice(inv: Any) -> Dict[str, Any]:
    return {
        "id": inv.id,
        "status": getattr(inv, "status", None),
        "amount_due": getattr(inv, "amount_due", 0),
        "amount_paid": getattr(inv, "amount_paid", 0),
        "currency": getattr(inv, "currency", "usd"),
        "created": getattr(inv, "created", None),
        "number": getattr(inv, "number", None),
        "pdf_url": getattr(inv, "invoice_pdf", None) or getattr(inv, "hosted_invoice_url", None),
    }

def _frontend_origin() -> str:
    # para URLs de retorno
    return _cfg("FRONTEND_ORIGIN", "https://boefrontend-production.up.railway.app")

# ───────── endpoints ─────────

@bp.get("/summary")
@require_clerk_auth
def summary():
    scope = (request.args.get("scope") or "user").strip().lower()
    user_id, org_id = _current_user_ids()

    if scope == "org":
        if not org_id:
            return jsonify(error="organization required"), 403
        if not _is_admin_for_org(user_id, org_id):
            return jsonify(error="forbidden: organization admin required"), 403

        try:
            org = _get_org(org_id)
        except Exception:
            return jsonify(error="clerk org fetch failed"), 502

        pm = org.get("public_metadata") or {}
        plan = (pm.get("plan") or pm.get("subscription") or pm.get("tier") or "free").lower()
        seats = int(pm.get("seats") or 0)
        return jsonify({
            "scope": "org",
            "org_id": org_id,
            "org_name": org.get("name"),
            "plan": plan,
            "seats": seats,
            "customer_id": _org_customer_id(org),
        })

    # scope = user
    me = getattr(g, "clerk", {}) or {}
    return jsonify({
        "scope": "user",
        "user_id": me.get("user_id"),
        "email": me.get("email"),
        "plan": (me.get("raw_claims") or {}).get("plan") or "free",
    })


@bp.get("/invoices")
@require_clerk_auth
def invoices():
    if not _stripe_enabled():
        return jsonify({"data": [], "warning": "stripe not configured"}), 200

    stripe = _stripe_init()
    scope = (request.args.get("scope") or "user").strip().lower()
    user_id, org_id = _current_user_ids()

    try:
        if scope == "org":
            if not org_id:
                return jsonify(error="organization required"), 403
            if not _is_admin_for_org(user_id, org_id):
                return jsonify(error="forbidden: organization admin required"), 403
            org = _get_org(org_id)
            customer_id = _org_customer_id(org)
            if not customer_id:
                return jsonify({"data": [], "warning": "org has no stripe customer"}), 200
        else:
            # user
            email = (getattr(g, "clerk", {}) or {}).get("email")
            customer_id = _find_or_user_customer_id(email)
            if not customer_id:
                return jsonify({"data": [], "warning": "user has no stripe customer"}), 200

        invs = stripe.Invoice.list(customer=customer_id, limit=20)
        data = [_serialize_invoice(i) for i in invs.auto_paging_iter()]
        return jsonify({"data": data}), 200
    except Exception as e:
        current_app.logger.exception("[billing] invoices failed: %s", e)
        return jsonify(error="stripe invoices failed"), 502


@bp.get("/portal")
@require_clerk_auth
def portal_get():
    # GET para facilitar pruebas desde el navegador
    return portal()


@bp.post("/portal")
@require_clerk_auth
def portal():
    if not _stripe_enabled():
        return jsonify(error="stripe not configured"), 501

    stripe = _stripe_init()
    scope = (request.args.get("scope") or request.json.get("scope") if request.is_json else None) or "user"
    scope = str(scope).strip().lower()

    user_id, org_id = _current_user_ids()
    success_url = _frontend_origin() + "/billing"
    return_url = success_url

    try:
        if scope == "org":
            if not org_id:
                return jsonify(error="organization required"), 403
            if not _is_admin_for_org(user_id, org_id):
                return jsonify(error="forbidden: organization admin required"), 403
            org = _get_org(org_id)
            customer_id = _org_customer_id(org)
            if not customer_id:
                return jsonify(error="org has no stripe customer"), 404
        else:
            email = (getattr(g, "clerk", {}) or {}).get("email")
            customer_id = _find_or_user_customer_id(email)
            if not customer_id:
                return jsonify(error="user has no stripe customer"), 404

        ps = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return jsonify({"url": ps.url}), 200
    except Exception as e:
        current_app.logger.exception("[billing] portal failed: %s", e)
        return jsonify(error="stripe portal failed"), 502


@bp.post("/checkout/enterprise")
@require_clerk_auth
def checkout_enterprise():
    if not _stripe_enabled():
        return jsonify(error="stripe not configured"), 501

    stripe = _stripe_init()
    body = request.get_json(silent=True) or {}
    seats = int(body.get("seats") or 1)
    price_id = _cfg("STRIPE_PRICE_ENTERPRISE", "")
    if not price_id:
        return jsonify(error="STRIPE_PRICE_ENTERPRISE not configured"), 501

    user_id, org_id = _current_user_ids()
    success_url = _frontend_origin() + "/billing?success=1"
    cancel_url = _frontend_origin() + "/billing?canceled=1"

    try:
        if not org_id:
            return jsonify(error="organization required"), 403
        if not _is_admin_for_org(user_id, org_id):
            return jsonify(error="forbidden: organization admin required"), 403

        org = _get_org(org_id)
        customer_id = _org_customer_id(org)

        params: Dict[str, Any] = dict(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": max(1, seats)}],
            success_url=success_url,
            cancel_url=cancel_url,
            allow_promotion_codes=True,
        )
        if customer_id:
            params["customer"] = customer_id
        else:
            # fallback: creará customer en checkout
            params["customer_email"] = (getattr(g, "clerk", {}) or {}).get("email")

        session = stripe.checkout.Session.create(**params)
        return jsonify({"url": session.url}), 200
    except Exception as e:
        current_app.logger.exception("[billing] checkout enterprise failed: %s", e)
        return jsonify(error="stripe checkout failed"), 502
