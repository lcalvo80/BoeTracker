# app/blueprints/webhooks.py
from flask import Blueprint, request, jsonify, current_app
from app.services import clerk_svc
from app.services.stripe_svc import init_stripe, set_subscription_quantity
import stripe

bp = Blueprint("webhooks", __name__)

@bp.post("/stripe")
def stripe_webhook():
    init_stripe()
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig, current_app.config["STRIPE_WEBHOOK_SECRET"])
    except Exception as e:
        return jsonify({"error": f"Invalid signature: {e}"}), 400

    etype = event["type"]
    data  = event["data"]["object"]

    if etype == "checkout.session.completed":
        sub_id      = data.get("subscription")
        customer_id = data.get("customer")
        meta        = data.get("metadata", {}) or {}
        scope       = meta.get("plan_scope", "user")
        user_id     = meta.get("clerk_user_id")
        org_id      = meta.get("clerk_org_id") or None

        sub    = stripe.Subscription.retrieve(sub_id)
        status = sub["status"]
        item   = sub["items"]["data"][0]
        sub_item_id = item["id"]

        if scope == "org" and org_id:
            clerk_svc.update_org_metadata(
                org_id,
                public={"plan": "enterprise"},
                private={"billing": {
                    "stripeCustomerId": customer_id,
                    "subscriptionId": sub_id,
                    "subscriptionItemId": sub_item_id,
                    "status": status
                }}
            )
        else:
            clerk_svc.update_user_metadata(
                user_id,
                public={"plan": "pro"},
                private={"billing": {
                    "stripeCustomerId": customer_id,
                    "subscriptionId": sub_id,
                    "status": status
                }}
            )

    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        sub    = data
        status = sub["status"]
        cust   = stripe.Customer.retrieve(sub["customer"])
        user_id = (cust.metadata or {}).get("clerk_user_id")
        org_id  = (cust.metadata or {}).get("clerk_org_id") or None

        if org_id:
            public = {"plan": "enterprise"} if status in {"active","trialing"} else {"plan": "free"}
            clerk_svc.update_org_metadata(org_id, public=public, private={"billing": {"status": status}})
        elif user_id:
            public = {"plan": "pro"} if status in {"active","trialing"} else {"plan": "free"}
            clerk_svc.update_user_metadata(user_id, public=public, private={"billing": {"status": status}})

    elif etype == "invoice.payment_failed":
        # marca 'past_due' si quieres y notifica
        pass

    return jsonify({"ok": True})

# -------- Opcional: webhook de Clerk para seats enterprise --------------
# Requiere: pip install svix
try:
    from svix.webhooks import Webhook, WebhookVerificationError

    @bp.post("/clerk")
    def clerk_webhook():
        payload = request.get_data()
        headers = dict(request.headers)

        try:
            wh = Webhook(current_app.config["CLERK_WEBHOOK_SECRET"])
            event = wh.verify(payload, headers)
        except Exception:
            return jsonify({"error": "invalid_signature"}), 400

        type_ = event["type"]
        data  = event["data"]

        # cuando se añaden o eliminan miembros, ajusta asiento
        if type_ in ("organizationMembership.created", "organizationMembership.deleted"):
            org_id = data["organization"]["id"]
            org = clerk_svc.get_org(org_id)
            # Puedes obtener miembros exactos con el endpoint de miembros;
            # aquí usamos un aproximado si el payload no trae todos.
            members_count = int((org.get("members_count") or 0))

            billing = (org.get("private_metadata") or {}).get("billing", {})
            item_id = billing.get("subscriptionItemId")
            if item_id:
                set_subscription_quantity(item_id, members_count)
                clerk_svc.update_org_metadata(org_id, private={"billing": {**billing, "seatCount": members_count}})

        return jsonify({"ok": True})
except Exception:
    # Si no instalas svix no registramos este endpoint
    pass
