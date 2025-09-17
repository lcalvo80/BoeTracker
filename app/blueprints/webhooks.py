# app/routes/webhooks.py
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from flask import Blueprint, request, jsonify, current_app
from app.services import clerk_svc
from app.services.stripe_svc import init_stripe, set_subscription_quantity
import stripe

bp = Blueprint("webhooks", __name__)


# ───────────────────────── Helpers ─────────────────────────

def _stripe_secret() -> str:
    s = current_app.config.get("STRIPE_WEBHOOK_SECRET") or ""
    if not s:
        raise RuntimeError("Missing STRIPE_WEBHOOK_SECRET in backend config/env")
    return s

def _safe_get(d: dict, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def _plan_from_status(status: str, scope: str) -> str:
    """
    status: active | trialing | past_due | canceled | incomplete | incomplete_expired | unpaid | paused
    scope : "user" | "org"
    """
    if status in {"active", "trialing"}:
        return "enterprise" if scope == "org" else "pro"
    return "free"

def _merge_meta(*objs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for o in objs:
        if isinstance(o, dict):
            out.update(o)
    return out


# ───────────────────────── Stripe Webhook ─────────────────────────

@bp.post("/stripe")
def stripe_webhook():
    """Webhook receptor de Stripe: sincroniza plan y billing en Clerk."""
    init_stripe()

    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature")

    try:
        secret = _stripe_secret()
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except Exception as e:
        # 400 → Stripe reintentará; no exponer detalles internos
        return jsonify({"error": "invalid_signature", "detail": str(e)}), 400

    etype: str = event.get("type", "")
    data: dict = event.get("data", {}).get("object", {}) or {}

    # ── checkout.session.completed ──────────────────────────────
    if etype == "checkout.session.completed":
        try:
            session = data
            sub_id: Optional[str] = session.get("subscription")
            customer_id: Optional[str] = session.get("customer")

            # metadatos que enviamos desde el backend al crear la sesión
            meta_from_session = session.get("metadata") or {}
            # algunos SDKs no “reflejan” subscription_data.metadata en la session → cogemos de la Subscription
            sub: Optional[stripe.Subscription] = None
            sub_meta: Dict[str, Any] = {}
            sub_status: str = "active"
            sub_item_id: Optional[str] = None

            if sub_id:
                sub = stripe.Subscription.retrieve(sub_id)
                sub_meta = getattr(sub, "metadata", {}) or {}
                sub_status = sub.get("status") or "active"
                items = _safe_get(sub, "items", "data", default=[]) or []
                if items:
                    sub_item_id = items[0].get("id")

            # También unimos cualquier metadato que tenga el Customer (lo rellenamos al crearlo)
            cust_meta = {}
            if customer_id:
                cust = stripe.Customer.retrieve(customer_id)
                cust_meta = getattr(cust, "metadata", {}) or {}

            meta = _merge_meta(meta_from_session, sub_meta, cust_meta)

            scope: str = meta.get("plan_scope", "user")
            user_id: Optional[str] = meta.get("clerk_user_id")
            org_id: Optional[str] = meta.get("clerk_org_id") or None

            plan = _plan_from_status(sub_status, scope)

            billing_payload = {
                "stripeCustomerId": customer_id,
                "subscriptionId": sub_id,
                "subscriptionItemId": sub_item_id,
                "status": sub_status,
            }

            if scope == "org" and org_id:
                clerk_svc.update_org_metadata(
                    org_id,
                    public={"plan": plan},
                    private={"billing": billing_payload},
                )
            elif user_id:
                clerk_svc.update_user_metadata(
                    user_id,
                    public={"plan": plan},
                    private={"billing": billing_payload},
                )
        except Exception as e:
            # 200 con error “registrado”: Stripe no debe reintentar salvo fallos de entrega
            # (si quieres reintentos, devuelve 500; aquí preferimos idempotencia lógica)
            current_app.logger.exception("webhook: checkout.session.completed failed")
            return jsonify({"ok": False, "error": str(e)}), 200

        return jsonify({"ok": True}), 200

    # ── customer.subscription.created / updated / deleted ───────
    elif etype in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        try:
            sub = data
            status: str = sub.get("status") or "active"
            customer_id: Optional[str] = sub.get("customer")

            user_id: Optional[str] = None
            org_id: Optional[str] = None
            scope: str = "user"

            # Primero miramos metadata del customer (lo rellenamos al crear/actualizar)
            if customer_id:
                cust = stripe.Customer.retrieve(customer_id)
                cust_meta = getattr(cust, "metadata", {}) or {}
                user_id = cust_meta.get("clerk_user_id")
                org_id = cust_meta.get("clerk_org_id")
                if org_id:
                    scope = "org"

            plan = _plan_from_status(status, scope)
            billing_patch = {"status": status}

            if scope == "org" and org_id:
                clerk_svc.update_org_metadata(
                    org_id,
                    public={"plan": plan},
                    private={"billing": billing_patch},
                )
            elif user_id:
                clerk_svc.update_user_metadata(
                    user_id,
                    public={"plan": plan},
                    private={"billing": billing_patch},
                )
        except Exception as e:
            current_app.logger.exception("webhook: subscription.* failed")
            return jsonify({"ok": False, "error": str(e)}), 200

        return jsonify({"ok": True}), 200

    # ── invoice.payment_failed (opcional: marcar riesgo, enviar email, etc.) ─
    elif etype == "invoice.payment_failed":
        # Aquí podrías degradar tras periodo de gracia, enviar notificación, etc.
        current_app.logger.info("invoice.payment_failed: %s", json.dumps(data))
        return jsonify({"ok": True}), 200

    # Otros eventos no usados → 200 para evitar reintentos
    return jsonify({"ok": True, "ignored": etype}), 200


# ───────────────────────── Clerk Webhook (opcional) ─────────────────────────
# Requiere: pip install svix
try:
    from svix.webhooks import Webhook

    @bp.post("/clerk")
    def clerk_webhook():
        """
        Mantiene en Stripe la cantidad de asientos (seats) igual al número de miembros
        de la organización, reaccionando a cambios de membresía en Clerk.
        """
        payload = request.get_data()
        sig = request.headers.get("svix-signature")
        if not sig:
            return jsonify({"error": "missing signature"}), 400

        wh = Webhook(current_app.config.get("CLERK_WEBHOOK_SECRET") or "")
        try:
            evt = wh.verify(payload, sig)  # type: ignore
        except Exception as e:
            return jsonify({"error": "invalid signature", "detail": str(e)}), 400

        type_ = evt.get("type", "")
        data  = evt.get("data", {}) or {}

        if type_ in ("organizationMembership.created", "organizationMembership.deleted"):
            try:
                org_id = _safe_get(data, "organization", "id")
                if not org_id:
                    return jsonify({"ok": True}), 200

                org = clerk_svc.get_org(org_id)
                members_count = int(org.get("members_count") or 0)

                billing = (org.get("private_metadata") or {}).get("billing", {}) or {}
                item_id = billing.get("subscriptionItemId")
                if item_id:
                    # Ajusta seats en Stripe y persiste recuento en Clerk
                    set_subscription_quantity(item_id, members_count)
                    clerk_svc.update_org_metadata(
                        org_id,
                        private={"billing": {**billing, "seatCount": members_count}},
                    )
            except Exception as e:
                current_app.logger.exception("clerk webhook seat sync failed")
                return jsonify({"ok": False, "error": str(e)}), 200

        return jsonify({"ok": True}), 200

except Exception:
    # svix no instalado; endpoint omitido
    pass
