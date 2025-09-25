# app/blueprints/billing.py
from __future__ import annotations
import os
import stripe
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app, g

bp = Blueprint("billing", __name__, url_prefix="/api")

# ───────── helpers de config ─────────
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
    stripe.api_key = sk
    return sk, None

def _front_base() -> str:
    return (_cfg("FRONTEND_URL", "http://localhost:5173") or "").rstrip("/")

# Solo valores válidos para Stripe: 'auto' | 'never'
def _update_mode(v: str | None, default: str = "auto") -> str:
    s = (v or default).strip().lower()
    if s in ("auto", "1", "true", "yes", "on"):
        return "auto"
    return "never"

# ───────── Auth guard (mantiene tu require_clerk_auth; fallback si DISABLE_AUTH) ─────────
def _load_auth_guard():
    disabled = _truthy(_cfg("DISABLE_AUTH", "0") or "0")
    if disabled:
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

# ───────── Clerk svc (opcional) ─────────
try:
    # wrapper de tu proyecto para usar Clerk Admin API (get_user, update_user_metadata, set_user_plan, etc.)
    from app.integrations import clerk_admin as clerk_svc  # type: ignore
except Exception:
    clerk_svc = None  # type: ignore

# ───────── Customer helpers (Clerk como única fuente de verdad) ─────────
def _derive_identity():
    """Devuelve (user_id, email, name) desde g.clerk o valores de dev si bypass."""
    user_id = getattr(g, "clerk", {}).get("user_id") or "dev_user"
    email   = getattr(g, "clerk", {}).get("email")
    name    = getattr(g, "clerk", {}).get("name") or user_id
    return user_id, email, name

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
    """
    Garantiza un Stripe Customer para el usuario **persistiendo siempre en Clerk**.
    - Busca en private_metadata.billing.stripeCustomerId
    - Reutiliza por email (search) si existe
    - Crea en Stripe y guarda en Clerk
    """
    # Intento con Clerk
    if clerk_svc:
        try:
            u = clerk_svc.get_user(user_id)
            priv = (u.get("private_metadata") or {})
            existing = (priv.get("billing") or {}).get("stripeCustomerId") or priv.get("stripe_customer_id")
            if existing:
                return existing

            # Datos primarios de email/nombre desde Clerk
            email = None
            try:
                emails = u.get("email_addresses") or []
                primary_id = u.get("primary_email_address_id")
                primary = next((e for e in emails if e.get("id") == primary_id), emails[0] if emails else None)
                email = primary.get("email_address") if primary else None
            except Exception:
                pass
            name = " ".join(filter(None, [u.get("first_name"), u.get("last_name")])) or u.get("username") or u.get("id")

            # Reuso por email
            cid = _search_customer_by_email(email)
            if cid:
                try:
                    clerk_svc.update_user_metadata(user_id, private={"billing": {"stripeCustomerId": cid}})
                except Exception:
                    current_app.logger.warning("No se pudo persistir stripeCustomerId en Clerk (continuamos).")
                return cid

            # Crear
            _init_stripe()
            customer = stripe.Customer.create(
                email=email,
                name=name,
                metadata={"clerk_user_id": user_id, "entity_type": "user", "entity_id": user_id},
            )
            try:
                clerk_svc.update_user_metadata(user_id, private={"billing": {"stripeCustomerId": customer.id}})
            except Exception:
                current_app.logger.warning("No se pudo persistir stripeCustomerId en Clerk (continuamos).")
            return customer.id
        except Exception:
            current_app.logger.exception("ensure_customer_for_user via Clerk falló; intentamos sin Clerk")

    # Fallback sin clerk_svc: usa g.clerk
    _, email, name = _derive_identity()
    cid = _search_customer_by_email(email)
    if cid:
        return cid
    _init_stripe()
    customer = stripe.Customer.create(
        email=email,
        name=name,
        metadata={"clerk_user_id": user_id, "entity_type": "user", "entity_id": user_id},
    )
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

    return {"plan_name": plan_name, "status": sub["status"], "current_period_end": period_end_iso, "payment_method": pm_info}

# ───────── Endpoints públicos (compat + nuevos GET) ─────────

# Compat: tu endpoint existente para Checkout (suscripción)
@bp.post("/checkout")
@_require_auth
def create_checkout():
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
    if quantity < 1:
        quantity = 1

    user_id, _, _ = _derive_identity()

    # Customer
    try:
        customer_id = _ensure_customer_for_user(user_id)
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
            subscription_data={"metadata": {"entity_type": "user", "entity_id": user_id, "plan_scope": "user"}},
            metadata={"entity_type": "user", "entity_id": user_id, "plan_scope": "user", "price_id": price_id},
            tax_id_collection={"enabled": True},
            locale=_cfg("STRIPE_CHECKOUT_LOCALE", "auto") or "auto",
            automatic_tax={"enabled": _truthy(_cfg("STRIPE_AUTOMATIC_TAX", "true") or "true")},
            billing_address_collection=("required" if _truthy(_cfg("STRIPE_REQUIRE_BILLING_ADDRESS", "true") or "true") else "auto"),
            customer_update={"address": address_mode, "name": name_mode},
            customer_email=None,
        )
    except stripe.error.AuthenticationError as e:
        current_app.logger.exception("Stripe authentication error creando checkout")
        return jsonify(error="stripe authentication error", detail=str(e)), 502
    except Exception as e:
        current_app.logger.exception("Error creando Stripe Checkout")
        return jsonify(error="checkout creation failed", detail=str(e)), 502

    return jsonify(checkout_url=session.url), 200

# Compat: tu endpoint existente (POST) para crear portal
@bp.post("/portal")
@_require_auth
def create_portal_compat():
    _, err = _init_stripe()
    if err: return err
    user_id, _, _ = _derive_identity()
    try:
        customer_id = _ensure_customer_for_user(user_id)
        ps = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{_front_base()}/account"
        )
        return jsonify(portal_url=ps.url), 200
    except Exception as e:
        current_app.logger.exception("Error creando portal")
        return jsonify(error="portal creation failed", detail=str(e)), 502

# Nuevo: GET /api/billing/portal → { url }
@bp.get("/billing/portal")
@_require_auth
def portal_get():
    _, err = _init_stripe()
    if err: return err
    user_id, _, _ = _derive_identity()
    try:
        customer_id = _ensure_customer_for_user(user_id)
        return_url = _cfg("STRIPE_BILLING_PORTAL_RETURN_URL") or f"{_front_base()}/account"
        ps = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
        return jsonify(url=ps.url), 200
    except Exception as e:
        current_app.logger.exception("Error creando portal (GET)")
        return jsonify(error="portal creation failed", detail=str(e)), 502

# Nuevo: GET /api/billing/summary
@bp.get("/billing/summary")
@_require_auth
def summary_get():
    _, err = _init_stripe()
    if err: return err
    user_id, _, _ = _derive_identity()
    try:
        customer_id = _ensure_customer_for_user(user_id)
        data = _subscription_summary(customer_id)
        # opcional: reflejar plan en Clerk
        if clerk_svc:
            try:
                plan = "pro" if data["status"] in ("active", "trialing", "past_due") else "free"
                clerk_svc.update_user_metadata(
                    user_id,
                    public={"plan": plan},
                    private={"billing": {"stripeCustomerId": customer_id}},
                )
            except Exception:
                pass
        return jsonify(data), 200
    except Exception as e:
        current_app.logger.exception("summary_get error")
        return jsonify(error="summary failed", detail=str(e)), 502

# Nuevo: GET /api/billing/invoices
@bp.get("/billing/invoices")
@_require_auth
def invoices_get():
    _, err = _init_stripe()
    if err: return err
    user_id, _, _ = _derive_identity()
    try:
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

# Compat: tu sync (actualiza metadata en Clerk tras éxito de Checkout)
@bp.post("/sync")
@_require_auth
def sync_after_success():
    _, err = _init_stripe()
    if err: return err
    b = request.get_json(silent=True) or {}
    sid = (b.get("session_id") or "").strip()
    if not sid:
        return jsonify(error="session_id is required"), 400

    try:
        sess = stripe.checkout.Session.retrieve(sid, expand=["subscription", "subscription.items.data.price"])
        sub = sess.get("subscription") or {}
        status = sub.get("status") or "active"
        price = None
        try:
            price = sub["items"]["data"][0]["price"]["id"]
        except Exception:
            pass

        user_id, _, _ = _derive_identity()
        if clerk_svc:
            try:
                priv = {"billing": {
                    "stripeCustomerId": sess.get("customer"),
                    "subscriptionId": sub.get("id"),
                    "status": status,
                    "planPriceId": price
                }}
                plan = "pro" if status in ("active", "trialing", "past_due") else "free"
                if hasattr(clerk_svc, "set_user_plan"):
                    clerk_svc.set_user_plan(user_id, plan=plan, status=status, extra_private=priv)
                else:
                    clerk_svc.update_user_metadata(user_id, public={"plan": plan}, private=priv)
            except Exception:
                current_app.logger.exception("No se pudo actualizar Clerk en /billing/sync (continuamos).")
    except Exception as e:
        current_app.logger.exception("sync_after_success error")
        return jsonify(error="sync failed", detail=str(e)), 502

    return jsonify(ok=True), 200

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
