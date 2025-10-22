from __future__ import annotations
import os
import stripe
from flask import Blueprint, request, jsonify, current_app
from svix.webhooks import Webhook, WebhookVerificationError
from app.services import clerk_svc

bp = Blueprint("webhooks", __name__, url_prefix="/api")

# ───────────────── Helpers ─────────────────
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

def _resolve_entity(meta: dict, fallback: dict | None = None) -> dict:
    """
    Acepta ambos formatos de metadata:
    - Nuevo: entity_type ('user'|'org'), entity_id, entity_email, buyer_user_id, seats
    - Actual (tu checkout): scope ('user'|'org'), org_id / clerk_user_id, buyer_user_id, seats
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

    event_id = event.get("id")
    etype = event.get("type")
    obj = event.get("data", {}).get("object") or {}

    try:
        # ───────── checkout.session.completed ─────────
        if etype == "checkout.session.completed":
            sess = obj
            customer_id = sess.get("customer")
            sub_id = sess.get("subscription")
            md = (sess.get("metadata") or {})  # scope/org_id/... en tu flujo actual

            # Leer suscripción (seats/estado)
            sub = None
            try:
                if sub_id:
                    sub = stripe.Subscription.retrieve(sub_id)
            except Exception:
                current_app.logger.exception("[Stripe] retrieve subscription failed")

            status = (sub.get("status") if sub else None) or "active"
            is_active = status in ("active", "trialing", "past_due")
            seats = _sum_seats_from_subscription(sub) if sub else int((md.get("seats") or 1))

            ent = _resolve_entity(md)
            entity_type, entity_id = ent["entity_type"], ent["entity_id"]
            buyer_user_id = ent["buyer_user_id"]
            entity_email = ent["entity_email"]

            if entity_type == "user" and entity_id:
                priv = {"billing": {"stripeCustomerId": customer_id, "subscriptionId": sub.get("id") if sub else None, "status": status}}
                clerk_svc.set_user_plan(
                    entity_id,
                    plan=("pro" if is_active else "free"),
                    status=status,
                    extra_private=priv
                )

            elif entity_type == "org" and entity_id:
                # Vincular customer a org en metadata (sin sobrescribir si ya pertenece a otra entidad)
                try:
                    cust = stripe.Customer.retrieve(customer_id) if customer_id else None
                    if cust:
                        md_cust = dict(cust.get("metadata") or {})
                        changed = False
                        if md_cust.get("entity_type") != "org": md_cust["entity_type"] = "org"; changed = True
                        if md_cust.get("entity_id") != entity_id: md_cust["entity_id"] = entity_id; changed = True
                        if entity_email and md_cust.get("entity_email") != entity_email: md_cust["entity_email"] = entity_email; changed = True
                        if changed:
                            stripe.Customer.modify(customer_id, metadata=md_cust)
                except Exception:
                    current_app.logger.exception("[Stripe] no se pudo garantizar metadata de customer")

                priv = {"billing": {"stripeCustomerId": customer_id, "subscriptionId": sub.get("id") if sub else None, "status": status}}
                clerk_svc.set_org_plan(
                    entity_id,
                    plan=("enterprise" if is_active else "free"),
                    status=status,
                    extra_private=priv,
                    extra_public={
                        "seats": seats,
                        "subscription": ("enterprise" if is_active else None),
                        "plan": ("enterprise" if is_active else "free"),
                    },
                )

                # Promover comprador a admin si viene en metadata
                try:
                    if buyer_user_id:
                        clerk_svc.promote_user_to_admin(entity_id, buyer_user_id)
                except Exception:
                    current_app.logger.exception("[Stripe] no se pudo promover buyer a admin")

                # (Opcional) Propagar entitlement a miembros
                try:
                    clerk_svc.set_entitlement_for_org_members(
                        entity_id,
                        "enterprise_member" if is_active else None
                    )
                except Exception:
                    current_app.logger.exception("[Stripe] no se pudo propagar entitlement a miembros")

        # ───────── customer.subscription.* ─────────
        elif etype in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
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
                priv = {"billing": {"stripeCustomerId": sub.get("customer"), "subscriptionId": sub.get("id"), "status": status}}
                clerk_svc.set_user_plan(
                    entity_id,
                    plan=("pro" if is_active else "free"),
                    status=status,
                    extra_private=priv
                )

            elif entity_type == "org" and entity_id:
                priv = {"billing": {"stripeCustomerId": sub.get("customer"), "subscriptionId": sub.get("id"), "status": status}}
                clerk_svc.set_org_plan(
                    entity_id,
                    plan=("enterprise" if is_active else "free"),
                    status=status,
                    extra_private=priv,
                    extra_public={
                        "seats": seats,
                        "subscription": ("enterprise" if is_active else None),
                        "plan": ("enterprise" if is_active else "free"),
                    }
                )
                try:
                    clerk_svc.set_entitlement_for_org_members(
                        entity_id,
                        "enterprise_member" if is_active else None
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
        # Puedes añadir organizationMembership.created/updated/deleted si quieres invalidar cachés
    except Exception:
        current_app.logger.exception("clerk handler error")

    return jsonify(ok=True), 200

# ─────────── Endpoint interno (solo DEV) para re-sincronizar entitlements ───────────
@bp.post("/_int/entitlements/sync")
def _int_sync_entitlements():
    if not current_app.config.get("DEBUG", False):
        return jsonify(error="not_found"), 404
    data = request.get_json(silent=True) or {}
    org_id = (data.get("org_id") or request.args.get("org_id") or "").strip()
    ent = (data.get("entitlement") or "enterprise_member").strip() or None
    if not org_id:
        return jsonify(error="org_id required"), 400
    try:
        clerk_svc.set_entitlement_for_org_members(org_id, ent)
        return jsonify(ok=True, org_id=org_id, entitlement=ent), 200
    except Exception as e:
        current_app.logger.exception("[_int] sync entitlements error")
        return jsonify(error=str(e)), 500

# 👇 Importante: NO registramos alias tipo /api/billing/webhook ni /api/billing/stripe.
# Canónicos e inmutables por decisión del proyecto:
#   - POST /api/stripe
#   - POST /api/clerk
