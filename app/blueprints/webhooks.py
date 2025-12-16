from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import stripe
from flask import Blueprint, request, jsonify, current_app
from svix.webhooks import Webhook, WebhookVerificationError

from app.services import clerk_svc, stripe_svc

bp = Blueprint("webhooks", __name__, url_prefix="/api")


# ───────────────── Helpers genéricos ─────────────────

def _cfg(k, default=None):
    v = current_app.config.get(k)
    if v is None or str(v).strip() == "":
        v = os.getenv(k, default)
    return v


def _init_stripe():
    sk = _cfg("STRIPE_SECRET_KEY", "")
    if not sk:
        return None, (jsonify(error="STRIPE_SECRET_KEY missing"), 500)
    if stripe.api_key != sk:
        stripe.api_key = sk
    return sk, None


def _sum_seats_from_subscription(sub: dict) -> int:
    try:
        items = (sub.get("items") or {}).get("data") or []
        return max(int(items[0].get("quantity") or 0), 0) if items else 0
    except Exception:
        return 0


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_entity(meta: dict, fallback: dict | None = None) -> dict:
    """
    Acepta ambos formatos de metadata:
    - Nuevo: entity_type ('user'|'org'), entity_id, entity_email, buyer_user_id, seats
    - Legacy/checkout: scope ('user'|'org'), org_id / clerk_user_id, buyer_user_id, seats
    """
    md = dict(fallback or {})
    md.update(meta or {})

    entity_type = (md.get("entity_type") or "").strip()
    entity_id = (md.get("entity_id") or "").strip()
    buyer_user_id = (md.get("buyer_user_id") or "").strip() or None
    entity_email = (md.get("entity_email") or "").strip() or None
    seats = md.get("seats")

    if not entity_type:
        scope = (md.get("scope") or "").strip().lower()
        if scope == "org":
            entity_type = "org"
            entity_id = entity_id or (md.get("org_id") or "").strip()
        elif scope == "user":
            entity_type = "user"
            entity_id = entity_id or (md.get("clerk_user_id") or md.get("user_id") or "").strip()

    return {
        "entity_type": entity_type or None,
        "entity_id": entity_id or None,
        "buyer_user_id": buyer_user_id,
        "entity_email": entity_email,
        "seats": seats,
    }


# ───────────────── Stripe ─────────────────

@bp.post("/stripe")
def stripe_webhook_api():
    _, err = _init_stripe()
    if err:
        return err

    wh_secret = _cfg("STRIPE_WEBHOOK_SECRET", "")
    if not wh_secret:
        return jsonify(error="STRIPE_WEBHOOK_SECRET missing"), 500

    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, wh_secret)
    except Exception as e:
        current_app.logger.warning(f"[Stripe] invalid signature: {e}")
        return jsonify(error="invalid signature"), 400

    etype = event.get("type")
    obj = event.get("data", {}).get("object") or {}

    try:
        # ───────── checkout.session.completed ─────────
        if etype == "checkout.session.completed":
            sess = obj
            customer_id = sess.get("customer")
            sub_id = sess.get("subscription")
            md = (sess.get("metadata") or {})

            # Leer suscripción (para seats/estado reales)
            sub = None
            try:
                if sub_id:
                    sub = stripe.Subscription.retrieve(sub_id)
            except Exception:
                current_app.logger.exception("[Stripe] retrieve subscription failed")

            status = (sub.get("status") if sub else None) or "active"
            is_active = status in ("active", "trialing", "past_due")

            # Seats: preferimos quantity real; fallback metadata
            seats = _sum_seats_from_subscription(sub) if sub else 0
            if seats <= 0:
                try:
                    seats = max(int(md.get("seats") or 1), 1)
                except Exception:
                    seats = 1

            ent = _resolve_entity(md)
            entity_type, entity_id = ent["entity_type"], ent["entity_id"]
            buyer_user_id = ent["buyer_user_id"]
            entity_email = ent["entity_email"]

            # Intentar leer nombre de empresa desde Stripe Customer
            company_name = None
            try:
                if customer_id:
                    cust = stripe.Customer.retrieve(customer_id)
                    company_name = (cust.get("name") or "").strip() or None
            except Exception:
                current_app.logger.exception("[Stripe] no se pudo leer customer.name")

            # ─── USER SCOPE ───
            if entity_type == "user" and entity_id:
                priv = {
                    "billing": {
                        "stripeCustomerId": customer_id,
                        "subscriptionId": (sub.get("id") if sub else sub_id),
                        "status": status,
                    }
                }
                clerk_svc.set_user_plan(
                    entity_id,
                    plan=("pro" if is_active else "free"),
                    status=status,
                    extra_private=priv,
                )

            # ─── ORG SCOPE (ENTERPRISE) ───
            elif entity_type == "org" and entity_id:
                org_id = entity_id

                # ✅ (A) Opcional: actualizar nombre org desde Stripe customer.name (sin requests; vía clerk_svc)
                try:
                    if company_name:
                        clerk_svc.update_org_name(org_id, company_name)
                except Exception:
                    current_app.logger.exception("[Stripe] no se pudo actualizar nombre de org desde Stripe")

                # ✅ (B) Garantizar customer metadata org (sin “flip”; delegado a stripe_svc)
                try:
                    if customer_id:
                        stripe_svc.ensure_customer_metadata(
                            customer_id=customer_id,
                            entity_type="org",
                            entity_id=org_id,
                            entity_email=entity_email,
                            strict=True,
                        )
                except Exception:
                    current_app.logger.exception("[Stripe] no se pudo garantizar metadata de customer (org)")

                # ✅ (C) Cambio crítico: asegurar admin membership del comprador (idempotente)
                try:
                    if buyer_user_id:
                        clerk_svc.ensure_membership_admin(org_id, buyer_user_id)
                except Exception:
                    current_app.logger.exception("[Stripe] no se pudo asegurar buyer como admin")

                # ✅ (D) Activación Enterprise en org metadata + quitar pending + guardar Stripe ids
                try:
                    clerk_svc.merge_org_metadata(
                        org_id,
                        public_updates={
                            "plan": ("enterprise" if is_active else "free"),
                            "seats": int(seats),
                        },
                        private_updates={
                            "pending_enterprise_checkout": False,
                            "stripe_subscription_id": (sub.get("id") if sub else sub_id),
                            "stripe_customer_id": customer_id,
                            "enterprise_activated_at": (_now_iso_utc() if is_active else None),
                        },
                    )
                except Exception:
                    current_app.logger.exception("[Stripe] no se pudo actualizar metadata de org (activation)")

                # ✅ (E) Propagar entitlement a miembros (si lo sigues usando)
                try:
                    clerk_svc.set_entitlement_for_org_members(
                        org_id,
                        "enterprise_member" if is_active else None,
                    )
                except Exception:
                    current_app.logger.exception("[Stripe] no se pudo propagar entitlement a miembros")

        # ───────── checkout.session.expired (cleanup recomendado) ─────────
        elif etype == "checkout.session.expired":
            sess = obj
            customer_id = sess.get("customer")
            sub_id = sess.get("subscription")
            md = (sess.get("metadata") or {})

            ent = _resolve_entity(md)
            entity_type, entity_id = ent["entity_type"], ent["entity_id"]

            if entity_type == "org" and entity_id:
                org_id = entity_id
                try:
                    clerk_svc.enterprise_cleanup_org(
                        org_id,
                        seats=0,
                        plan="free",
                        mark_canceled_at=True,
                        canceled_reason="checkout_expired",
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=sub_id,
                    )
                except Exception:
                    current_app.logger.exception("[Stripe] checkout.session.expired cleanup failed")

        # ───────── customer.subscription.* ─────────
        elif etype in (
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        ):
            sub = obj
            status = sub.get("status") or "canceled"
            is_active = status in ("active", "trialing", "past_due")
            seats = _sum_seats_from_subscription(sub)

            sub_md = (sub.get("metadata") or {})
            cust = None
            cust_md = {}
            try:
                if sub.get("customer"):
                    cust = stripe.Customer.retrieve(sub["customer"])
                    cust_md = (cust.get("metadata") if isinstance(cust, dict) else {}) or {}
            except Exception:
                current_app.logger.exception("[Stripe] error retrieving customer")

            ent = _resolve_entity(sub_md, fallback=cust_md)
            entity_type, entity_id = ent["entity_type"], ent["entity_id"]

            if entity_type == "user" and entity_id:
                priv = {
                    "billing": {
                        "stripeCustomerId": sub.get("customer"),
                        "subscriptionId": sub.get("id"),
                        "status": status,
                    }
                }
                clerk_svc.set_user_plan(
                    entity_id,
                    plan=("pro" if is_active else "free"),
                    status=status,
                    extra_private=priv,
                )

            elif entity_type == "org" and entity_id:
                org_id = entity_id
                try:
                    clerk_svc.merge_org_metadata(
                        org_id,
                        public_updates={
                            "plan": ("enterprise" if is_active else "free"),
                            "seats": int(seats),
                        },
                        private_updates={
                            "stripe_subscription_id": sub.get("id"),
                            "stripe_customer_id": sub.get("customer"),
                        },
                    )
                except Exception:
                    current_app.logger.exception("[Stripe] no se pudo actualizar metadata org en subs.*")

                try:
                    clerk_svc.set_entitlement_for_org_members(
                        org_id,
                        "enterprise_member" if is_active else None,
                    )
                except Exception:
                    current_app.logger.exception("[Stripe] no se pudo propagar entitlement a miembros (subs.*)")

        return jsonify(received=True), 200

    except Exception:
        current_app.logger.exception("stripe webhook handler error")
        return jsonify(error="handler error"), 500


# ───────────────── Clerk (Svix) ─────────────────

@bp.post("/clerk")
def clerk_webhook_api():
    secret = _cfg("CLERK_WEBHOOK_SECRET", "")
    if not secret:
        return jsonify(error="CLERK_WEBHOOK_SECRET missing"), 500

    headers = {
        "svix-id": request.headers.get("svix-id", ""),
        "svix-timestamp": request.headers.get("svix-timestamp", ""),
        "svix-signature": request.headers.get("svix-signature", ""),
    }
    payload = request.get_data()
    try:
        Webhook(secret).verify(payload, headers)
    except WebhookVerificationError:
        return jsonify(error="invalid signature"), 400

    try:
        event = request.get_json() or {}
    except Exception:
        current_app.logger.exception("clerk webhook error")
        return jsonify(error="bad request"), 400

    evt_type = event.get("type")
    data = event.get("data") or {}
    try:
        if evt_type == "user.created":
            uid = data.get("id")
            if uid:
                clerk_svc.set_user_plan(uid, plan="free", status="none")
    except Exception:
        current_app.logger.exception("clerk handler error")

    return jsonify(ok=True), 200


# 👇 Importante: endpoints canónicos e inmutables por decisión del proyecto:
#   - POST /api/stripe
#   - POST /api/clerk
