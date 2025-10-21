from __future__ import annotations

from typing import Any, Dict, Optional, List

import stripe
from flask import Blueprint, current_app, request, jsonify, g

from app.auth import require_auth

bp = Blueprint("billing", __name__)


@bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)


# ───────────────── Helpers ─────────────────

def _stripe() -> None:
    stripe.api_key = current_app.config.get("STRIPE_SECRET_KEY", "")

def _cfg(k: str) -> str:
    return current_app.config.get(k, "")

def _success_cancel(default_path: str = "/billing/return") -> tuple[str, str]:
    base = current_app.config.get("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")
    data = request.get_json(silent=True) or {}
    success_url = data.get("success_url") or f"{base}{default_path}?status=success"
    cancel_url = data.get("cancel_url") or f"{base}{default_path}?status=cancel"
    return success_url, cancel_url

def _get_or_create_customer(email: Optional[str], metadata: Dict[str, Any]) -> stripe.Customer:
    _stripe()
    if email:
        existing = stripe.Customer.list(email=email, limit=1).data
        if existing:
            cust = existing[0]
            to_set = {k: v for k, v in (metadata or {}).items() if not (cust.metadata or {}).get(k)}
            if to_set:
                stripe.Customer.modify(cust.id, metadata={**(cust.metadata or {}), **to_set})
            return cust
    return stripe.Customer.create(email=email or None, metadata=metadata or None)

def _pm_from_customer(customer_id: str) -> Dict[str, Optional[str]]:
    try:
        cust = stripe.Customer.retrieve(customer_id, expand=["invoice_settings.default_payment_method"])
        pm = (cust.get("invoice_settings") or {}).get("default_payment_method")
        if pm and pm.get("card"):
            card = pm["card"]
            return {"brand": card.get("brand"), "last4": card.get("last4")}
        invs = stripe.Invoice.list(customer=customer_id, limit=1).data
        if invs:
            inv = invs[0]
            ch_id = inv.get("charge")
            if ch_id:
                ch = stripe.Charge.retrieve(ch_id)
                det = (ch.get("payment_method_details") or {}).get("card") or {}
                return {"brand": det.get("brand"), "last4": det.get("last4")}
    except Exception:
        pass
    return {"brand": None, "last4": None}

def _sub_summary(sub: Dict[str, Any]) -> Dict[str, Any]:
    status = sub.get("status")
    cpe = sub.get("current_period_end")
    cust_id = sub.get("customer")
    pm = _pm_from_customer(cust_id) if cust_id else {"brand": None, "last4": None}
    return {"status": status, "current_period_end": cpe, "payment_method": pm}

def _invoice_dto(inv: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": inv.get("id"),
        "number": inv.get("number"),
        "status": inv.get("status"),
        "total": inv.get("total"),
        "currency": inv.get("currency"),
        "created": inv.get("created"),
        "hosted_invoice_url": inv.get("hosted_invoice_url"),
        "invoice_pdf": inv.get("invoice_pdf"),
        "subscription": inv.get("subscription"),
        "customer": inv.get("customer"),
    }

def _json_ok(payload: Any, code: int = 200):
    return ({"ok": True, "data": payload}, code)

def _json_err(msg: str, code: int = 400):
    return ({"ok": False, "error": msg}, code)


# ───────────────── Endpoints ─────────────────

@bp.route("/summary", methods=["GET", "OPTIONS"])
@require_auth
def billing_summary():
    _stripe()
    try:
        if g.org_id:
            subs = stripe.Subscription.search(
                query=f'metadata["org_id"]:"{g.org_id}" AND status:"active"'
            ).data
            if subs:
                sub = subs[0]
                item = (sub.get("items") or {}).get("data", [{}])[0]
                seats = item.get("quantity") or 0
                base = _sub_summary(sub)
                base.update({"scope": "org", "org_id": g.org_id, "plan": "ENTERPRISE", "seats": seats})
                return _json_ok(base)
            return _json_ok({"scope": "org", "org_id": g.org_id, "plan": "NO_PLAN", "seats": 0, "status": None, "current_period_end": None, "payment_method": {"brand": None, "last4": None}})
        else:
            cust = _get_or_create_customer(g.email, {"clerk_user_id": g.user_id})
            subs = stripe.Subscription.list(customer=cust.id, status="active", limit=1).data
            if subs:
                sub = subs[0]
                base = _sub_summary(sub)
                base.update({"scope": "user", "plan": "PRO"})
                return _json_ok(base)
            return _json_ok({"scope": "user", "plan": "NO_PLAN", "status": None, "current_period_end": None, "payment_method": {"brand": None, "last4": None}})
    except Exception as e:
        return _json_err(str(e), 500)


@bp.route("/checkout/pro", methods=["POST", "OPTIONS"])
@require_auth
def checkout_pro():
    _stripe()
    try:
        body = request.get_json(silent=True) or {}
        price = body.get("price") or _cfg("STRIPE_PRICE_PRO")
        if not price:
            return _json_err("Falta STRIPE_PRICE_PRO", 500)

        success_url, cancel_url = _success_cancel("/billing/return")
        customer = _get_or_create_customer(g.email, {"clerk_user_id": g.user_id})

        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer.id,
            line_items=[{"price": price, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            subscription_data={"metadata": {"scope": "user", "clerk_user_id": g.user_id}},
            metadata={"scope": "user", "clerk_user_id": g.user_id},
            allow_promotion_codes=True,
        )
        return _json_ok({"url": session.url})
    except Exception as e:
        return _json_err(str(e), 500)


@bp.route("/checkout/enterprise", methods=["POST", "OPTIONS"])
@require_auth
def checkout_enterprise():
    if not g.org_id:
        return _json_err("Debes indicar organización (X-Org-Id o en el token).", 400)

    _stripe()
    body = request.get_json(silent=True) or {}
    try:
        seats = max(1, int(body.get("seats") or 1))
    except Exception:
        return _json_err("'seats' debe ser número entero.", 400)

    price = body.get("price") or _cfg("STRIPE_PRICE_ENTERPRISE")
    if not price:
        return _json_err("Falta STRIPE_PRICE_ENTERPRISE", 500)

    try:
        success_url, cancel_url = _success_cancel("/billing/return")

        customer = _get_or_create_customer(
            email=g.email,
            metadata={"clerk_user_id": g.user_id, "org_id": g.org_id},
        )

        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer.id,
            line_items=[{"price": price, "quantity": seats}],
            success_url=success_url,
            cancel_url=cancel_url,
            subscription_data={"metadata": {"scope": "org", "org_id": g.org_id, "buyer_user_id": g.user_id, "seats": str(seats)}},
            metadata={"scope": "org", "org_id": g.org_id, "buyer_user_id": g.user_id, "seats": str(seats)},
            allow_promotion_codes=True,
        )
        return _json_ok({"url": session.url})
    except Exception as e:
        return _json_err(str(e), 500)


@bp.route("/portal", methods=["POST", "OPTIONS"])
@require_auth
def billing_portal():
    _stripe()
    try:
        customer = _get_or_create_customer(
            email=g.email,
            metadata={"clerk_user_id": g.user_id, "org_id": g.org_id or ""},
        )
        base = current_app.config.get("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")
        ret_url = f"{base}/settings/billing"
        portal = stripe.billing_portal.Session.create(customer=customer.id, return_url=ret_url)
        return _json_ok({"url": portal.url})
    except Exception as e:
        return _json_err(str(e), 500)


@bp.route("/invoices", methods=["GET", "OPTIONS"])
@require_auth
def billing_invoices():
    """
    Lista facturas:
      - Scope org (si hay X-Org-Id/g.org_id): busca subs por metadata.org_id y lista sus invoices.
      - Scope user: lista por customer del usuario.
    Query: ?limit=20 (opcional)
    """
    _stripe()
    try:
        limit = max(1, min(100, int(request.args.get("limit", "20"))))
    except Exception:
        limit = 20

    try:
        if g.org_id:
            # Preferimos todas las subs (cualquier status) para mostrar histórico
            subs = stripe.Subscription.search(query=f'metadata["org_id"]:"{g.org_id}"').data
            if not subs:
                return _json_ok({"scope": "org", "org_id": g.org_id, "items": []})
            sub = subs[0]
            invs = stripe.Invoice.list(subscription=sub.id, limit=limit).data
            items = [_invoice_dto(i) for i in invs]
            return _json_ok({"scope": "org", "org_id": g.org_id, "items": items})

        # user scope
        cust = _get_or_create_customer(g.email, {"clerk_user_id": g.user_id})
        invs = stripe.Invoice.list(customer=cust.id, limit=limit).data
        items = [_invoice_dto(i) for i in invs]
        return _json_ok({"scope": "user", "items": items})

    except Exception as e:
        return _json_err(str(e), 500)


@bp.route("/webhook", methods=["POST"])
def webhook():
    _stripe()
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature", "")
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET", "")
    try:
        event = stripe.Webhook.construct_event(payload=payload, sig_header=sig_header, secret=secret)
    except Exception as e:
        return _json_err(f"Invalid signature: {e}", 400)

    etype = event["type"]
    data = event["data"]["object"]

    try:
        if etype == "checkout.session.completed":
            _sync_entitlements_from_checkout(data)
        elif etype in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "invoice.paid",
            "invoice.payment_failed",
        }:
            _sync_entitlements_from_subscription(data)
    except Exception as e:
        current_app.logger.exception("Error processing webhook: %s", etype)
        return _json_ok({"handled": False, "error": str(e)})

    return _json_ok({"handled": True, "type": etype})


# ───────────────── Sync stubs ─────────────────

def _sync_entitlements_from_checkout(sess: Dict[str, Any]) -> None:
    md = sess.get("metadata") or {}
    scope = (md.get("scope") or "").lower()
    if scope == "org" and md.get("org_id"):
        _sync_entitlements_for_org(md["org_id"])
    elif scope == "user" and md.get("clerk_user_id"):
        _sync_entitlements_for_user(md["clerk_user_id"])

def _sync_entitlements_from_subscription(sub: Dict[str, Any]) -> None:
    md = sub.get("metadata") or {}
    scope = (md.get("scope") or "").lower()
    if scope == "org" and md.get("org_id"):
        _sync_entitlements_for_org(md["org_id"])
    elif scope == "user" and md.get("clerk_user_id"):
        _sync_entitlements_for_user(md["clerk_user_id"])

def _sync_entitlements_for_org(org_id: str) -> None:
    try:
        from app.services.entitlements import sync_entitlements_for_org as _real
        _real(org_id)
    except Exception:
        current_app.logger.info("sync_entitlements_for_org stub for %s", org_id)

def _sync_entitlements_for_user(user_id: str) -> None:
    try:
        from app.services.entitlements import sync_entitlements_for_user as _real
        _real(user_id)
    except Exception:
        current_app.logger.info("sync_entitlements_for_user stub for %s", user_id)
