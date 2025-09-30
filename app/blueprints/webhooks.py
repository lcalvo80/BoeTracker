from __future__ import annotations
import os
import stripe
from flask import Blueprint, request, jsonify, current_app
from app.services import clerk_svc
from svix.webhooks import Webhook, WebhookVerificationError

bp = Blueprint("webhooks", __name__, url_prefix="/api")

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

# (Opcional) marca procesados si implementas idempotencia fuera (aquí no persistimos)
def _mark_processed(event_id: str):
    # sin DB: no-op
    return

def _sum_seats_from_subscription(sub: dict) -> int:
    try:
        items = (sub.get("items") or {}).get("data") or []
        return max(int(items[0].get("quantity") or 0), 0) if items else 0
    except Exception:
        return 0

def _ensure_customer_has_entity(customer_id: str, entity_type: str, entity_id: str, entity_email: str | None = None):
    try:
        cust = stripe.Customer.retrieve(customer_id)
        md = cust.get("metadata") or {}
        changed = False
        if md.get("entity_type") != entity_type: md["entity_type"] = entity_type; changed = True
        if md.get("entity_id") != entity_id: md["entity_id"] = entity_id; changed = True
        if entity_type == "user" and md.get("clerk_user_id") != entity_id: md["clerk_user_id"] = entity_id; changed = True
        if entity_type == "org" and md.get("clerk_org_id") != entity_id: md["clerk_org_id"] = entity_id; changed = True
        if entity_email and md.get("entity_email") != entity_email: md["entity_email"] = entity_email; changed = True
        if changed: stripe.Customer.modify(customer_id, metadata=md)
    except Exception:
        current_app.logger.warning("[Stripe] no se pudo garantizar metadata de customer")

@bp.post("/stripe")
def stripe_webhook_api():
    _, err = _init_stripe()
    if err: return err

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
        # checkout.session.completed
        if etype == "checkout.session.completed":
            sess = obj
            customer_id = sess.get("customer")
            sub_id = sess.get("subscription")
            meta = (sess.get("metadata") or {})
            entity_type = (meta.get("entity_type") or "").strip() or (sess.get("metadata") or {}).get("entity_type")
            entity_id = (meta.get("entity_id") or "").strip()
            entity_email = (meta.get("entity_email") or "").strip() or None
            plan = (meta.get("plan") or "").strip().lower()

            # cuando fue público enterprise, entity_id puede venir vacío; lo creamos ahora
            if entity_type == "org" and not entity_id:
                try:
                    # buscar por email y/o crear org con comprador invitado luego
                    # sin DB: creamos org con nombre básico
                    name = (entity_email or "enterprise").split("@")[0]
                    # nota: no podemos identificar al admin sin sesión; se gestionará posteriormente en UI
                    org = clerk_svc.create_org_for_user(user_id=None, name=name)  # user_id=None -> org sin owner en Clerk
                    entity_id = org.get("id")
                    # liga customer->org
                    _ensure_customer_has_entity(customer_id, "org", entity_id, entity_email)
                except Exception:
                    current_app.logger.exception("[Stripe] guest enterprise provisioning failed")

            # Refleja estado en Clerk
            sub = None
            try:
                if sub_id:
                    sub = stripe.Subscription.retrieve(sub_id)
            except Exception:
                current_app.logger.exception("[Stripe] retrieve subscription failed")

            status = (sub.get("status") if sub else None) or "active"
            seats = _sum_seats_from_subscription(sub) if sub else int((meta.get("seats") or "1"))

            if entity_type == "user" and entity_id:
                priv = {"billing": {"stripeCustomerId": customer_id, "subscriptionId": sub.get("id") if sub else None, "status": status}}
                clerk_svc.set_user_plan(entity_id, plan=("pro" if status in ("active","trialing","past_due") else "free"),
                                        status=status, extra_private=priv)

            elif entity_type == "org" and entity_id:
                priv = {"billing": {"stripeCustomerId": customer_id, "subscriptionId": sub.get("id") if sub else None, "status": status}}
                clerk_svc.set_org_plan(entity_id, plan="enterprise", status=status,
                                       extra_private=priv,
                                       extra_public={"seats": seats, "subscription": "enterprise"})

        # customer.subscription.{created,updated,deleted}
        elif etype in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
            sub = obj
            status = sub.get("status") or "canceled"
            seats = _sum_seats_from_subscription(sub)
            cust = None
            try:
                if sub.get("customer"):
                    cust = stripe.Customer.retrieve(sub["customer"])
            except Exception:
                current_app.logger.exception("[Stripe] error retrieving customer")

            md = (cust.get("metadata") if isinstance(cust, dict) else {}) or {}
            entity_type = md.get("entity_type")
            entity_id = md.get("entity_id")
            entity_email = md.get("entity_email")

            # Si es org “guest” creada por email, crea org y vincula
            if entity_type == "org" and entity_id in (None, "", " "):
                try:
                    name = (entity_email or "enterprise").split("@")[0]
                    org = clerk_svc.create_org_for_user(user_id=None, name=name)
                    entity_id = org.get("id")
                    # actualiza Clerk y Stripe con la relación
                    clerk_svc.set_org_plan(entity_id, plan=("enterprise" if status in ("active","trialing","past_due") else "free"),
                                           status=status,
                                           extra_public={"subscription": ("enterprise" if status in ("active","trialing","past_due") else None),
                                                         "seats": (seats if status in ("active","trialing","past_due") else 0)})
                    org_id = org.get("id")
                    priv = {"billing": {"stripeCustomerId": sub.get("customer"), "subscriptionId": sub.get("id"), "status": status}}
                    clerk_svc.set_org_plan(org_id, plan=("enterprise" if status in ("active","trialing","past_due") else "free"), status=status, extra_private=priv)
                    _ensure_customer_has_entity(sub.get("customer"), "org", org_id, entity_email)
                    if event_id: _mark_processed(event_id)
                    return jsonify(received=True), 200
                except Exception:
                    current_app.logger.exception("[Stripe] guest enterprise provisioning (subs.*) failed")

            if entity_type == "user" and entity_id:
                priv = {"billing": {"stripeCustomerId": sub.get("customer"), "subscriptionId": sub.get("id"), "status": status}}
                clerk_svc.set_user_plan(entity_id, plan=("pro" if status in ("active","trialing","past_due") else "free"),
                                        status=status, extra_private=priv)

            elif entity_type == "org" and entity_id:
                priv = {"billing": {"stripeCustomerId": sub.get("customer"), "subscriptionId": sub.get("id"), "status": status}}
                clerk_svc.set_org_plan(entity_id, plan=("enterprise" if status in ("active","trialing","past_due") else "free"),
                                       status=status, extra_private=priv,
                                       extra_public={"seats": seats, "subscription": ("enterprise" if status in ("active","trialing","past_due") else None)})

        if event_id: _mark_processed(event_id)
        return jsonify(received=True), 200

    except Exception:
        current_app.logger.exception("stripe webhook handler error")
        return jsonify(error="handler error"), 500

# Clerk (opcional)
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


# Alias recomendado: /api/billing/webhook (igual que /api/stripe)
@bp.post("/billing/webhook")
def stripe_webhook_api_alias():
    return stripe_webhook_api()
