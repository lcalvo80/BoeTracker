from __future__ import annotations

import os
import json
from typing import Any, Dict, Optional

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
            # Merge simple metadata keys if absent
            cust = existing[0]
            to_set = {k: v for k, v in metadata.items() if not cust.metadata.get(k)}
            if to_set:
                stripe.Customer.modify(cust.id, metadata={**cust.metadata, **to_set})
            return cust
    # Create
    return stripe.Customer.create(
        email=email or None,
        metadata=metadata or None,
    )


def _pro_price() -> str:
    return current_app.config.get("STRIPE_PRICE_PRO", "")


def _ent_price() -> str:
    return current_app.config.get("STRIPE_PRICE_ENTERPRISE", "")


def _json_ok(payload: Any) -> tuple[Dict[str, Any], int]:
    return {"ok": True, "data": payload}, 200


def _json_err(msg: str, code: int = 400) -> tuple[Dict[str, Any], int]:
    return {"ok": False, "error": msg}, code


# ───────────────── Endpoints ─────────────────

@bp.route("/summary", methods=["GET", "OPTIONS"])
@require_auth
def billing_summary():
    """
    Devuelve un resumen ligero del estado en Stripe para el usuario/org actual.
    - Si hay X-Org-Id → busca la suscripción enterprise de esa organización (por metadata).
    - Si no, busca la del usuario (Pro).
    """
    _stripe()
    try:
        if g.org_id:
            # Como simplificación, buscamos suscripciones con metadata.org_id == g.org_id
            subs = stripe.Subscription.search(
                query=f'metadata["org_id"]:"{g.org_id}" AND status:"active"'
            ).data
            plan = "ENTERPRISE" if subs else "NO_PLAN"
            seats = 0
            if subs and subs[0].items.data:
                seats = subs[0].items.data[0].quantity or 0
            return _json_ok({"scope": "org", "org_id": g.org_id, "plan": plan, "seats": seats})
        else:
            cust = _get_or_create_customer(g.email, {"clerk_user_id": g.user_id})
            subs = stripe.Subscription.list(customer=cust.id, status="active", limit=1).data
            plan = "PRO" if subs else "NO_PLAN"
            return _json_ok({"scope": "user", "plan": plan})
    except Exception as e:
        return _json_err(str(e), 500)


@bp.route("/checkout/pro", methods=["POST", "OPTIONS"])
@require_auth
def checkout_pro():
    _stripe()
    try:
        success_url, cancel_url = _success_cancel("/billing/return")
        price = (_data := (request.get_json(silent=True) or {})).get("price") or _pro_price()
        if not price:
            return _json_err("Falta STRIPE_PRICE_PRO", 500)

        customer = _get_or_create_customer(
            email=g.email,
            metadata={"clerk_user_id": g.user_id},
        )

        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer.id,
            line_items=[{"price": price, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            subscription_data={
                "metadata": {
                    "scope": "user",
                    "clerk_user_id": g.user_id,
                }
            },
            metadata={"scope": "user", "clerk_user_id": g.user_id},
            allow_promotion_codes=True,
        )
        return _json_ok({"url": session.url})
    except Exception as e:
        return _json_err(str(e), 500)


@bp.route("/checkout/enterprise", methods=["POST", "OPTIONS"])
@require_auth
def checkout_enterprise():
    """
    Crea un checkout de suscripción enterprise con seats como quantity.
    Requisitos:
      - X-Org-Id (o claim org_id)
      - price STRIPE_PRICE_ENTERPRISE
    """
    if not g.org_id:
        return _json_err("Debes indicar organización (X-Org-Id o en el token).", 400)

    _stripe()
    body = request.get_json(silent=True) or {}
    seats = max(1, int(body.get("seats") or 1))
    price = body.get("price") or _ent_price()
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
            subscription_data={
                "metadata": {
                    "scope": "org",
                    "org_id": g.org_id,
                    "buyer_user_id": g.user_id,
                    "seats": str(seats),
                }
            },
            metadata={
                "scope": "org",
                "org_id": g.org_id,
                "buyer_user_id": g.user_id,
                "seats": str(seats),
            },
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
        # portal para el customer del usuario actual (ya sea Pro o comprador Enterprise)
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


@bp.route("/webhook", methods=["POST"])
def webhook():
    """
    ÚNICO webhook Stripe consolidado.
    """
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

    # Manejo básico de eventos relevantes
    try:
        if etype == "checkout.session.completed":
            # No distinguimos pro/enterprise aquí; delegamos a sync
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
        # Log y 200 para evitar reintentos infinitos (Stripe reintenta igualmente)
        current_app.logger.exception("Error processing webhook: %s", etype)
        return _json_ok({"handled": False, "error": str(e)})

    return _json_ok({"handled": True, "type": etype})


# ───────────────── Sync helpers (stubs seguras) ─────────────────

def _sync_entitlements_from_checkout(sess: Dict[str, Any]) -> None:
    """
    Extrae metadata y decide si es user(Pro) u org(Enterprise).
    """
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
    """
    Integra con tu capa real de sync (servicio interno). Aquí dejamos stub no-op.
    Si tienes app.services.entitlements.sync_entitlements_for_org, lo invocamos.
    """
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
