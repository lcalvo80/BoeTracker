# app/blueprints/billing.py
from __future__ import annotations

from typing import Any, Dict, Optional

from flask import Blueprint, current_app, request, g

from app.auth import require_auth
from app.services import clerk_svc, stripe_svc

bp = Blueprint("billing", __name__)


@bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)


# ───────────────── Helpers ─────────────────

def _cfg(k: str) -> str:
    return current_app.config.get(k, "")


def _frontend_base() -> str:
    return current_app.config.get("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")


def _success_cancel(default_path: str = "/settings/billing") -> tuple[str, str]:
    base = _frontend_base()
    data = request.get_json(silent=True) or {}
    success_url = data.get("success_url") or f"{base}{default_path}?status=success"
    cancel_url = data.get("cancel_url") or f"{base}{default_path}?status=cancel"
    return success_url, cancel_url


def _success_cancel_pricing(org_id: str) -> tuple[str, str]:
    base = _frontend_base()
    success_url = f"{base}/pricing?checkout=success&org_id={org_id}"
    cancel_url = f"{base}/pricing?checkout=cancel&org_id={org_id}"
    return success_url, cancel_url


def _json_ok(payload: Any, code: int = 200):
    return ({"ok": True, "data": payload}, code)


def _json_err(msg: str, code: int = 400):
    return ({"ok": False, "error": msg}, code)


def _parse_seats(body: Dict[str, Any]) -> tuple[Optional[int], Optional[str]]:
    max_seats = int(current_app.config.get("ENTERPRISE_MAX_SEATS", 200))
    try:
        seats = int(body.get("seats") or 1)
    except Exception:
        return None, "'seats' debe ser número entero."

    if seats < 1:
        return None, "'seats' debe ser >= 1."
    if seats > max_seats:
        return None, f"'seats' supera el máximo permitido ({max_seats})."
    return seats, None


def _is_adminish_in_org(user_id: str, org_id: str) -> bool:
    mem = clerk_svc.get_membership_raw(user_id=user_id, org_id=org_id) or {}
    role = (mem.get("role") or "").strip().lower()
    return role in ("admin", "org:admin", "owner")


def _stripe_invalid_request_to_message(e: Exception) -> str:
    user_msg = getattr(e, "user_message", None)
    if user_msg:
        return str(user_msg)
    return str(e)


def _is_stripe_invalid_request(e: Exception) -> bool:
    name = e.__class__.__name__
    mod = (e.__class__.__module__ or "").lower()
    return name == "InvalidRequestError" and "stripe" in mod


# ───────────────── Endpoints ─────────────────

@bp.route("/summary", methods=["GET", "OPTIONS"])
@require_auth
def billing_summary():
    try:
        if g.org_id:
            data = stripe_svc.get_billing_summary_for_org(org_id=g.org_id)
        else:
            data = stripe_svc.get_billing_summary_for_user(user_id=g.user_id, email=g.email)
        return _json_ok(data)
    except Exception as e:
        current_app.logger.exception("[billing_summary] error")
        return _json_err(str(e), 500)


@bp.route("/checkout/pro", methods=["POST", "OPTIONS"])
@require_auth
def checkout_pro():
    body = request.get_json(silent=True) or {}

    price_id = body.get("price") or _cfg("STRIPE_PRICE_PRO")
    if not price_id:
        return _json_err("Falta STRIPE_PRICE_PRO", 500)

    try:
        success_url, cancel_url = _success_cancel("/settings/billing")

        cust = stripe_svc.get_or_create_customer_for_entity(
            entity_type="user",
            entity_id=g.user_id,
            email=g.email,
            name=None,
            extra_metadata={"created_from": "checkout_pro"},
        )

        meta = stripe_svc.build_pro_meta(
            user_id=g.user_id,
            price_id=price_id,
            entity_email=g.email or "",
            entity_name="",
        )
        meta["scope"] = "user"
        meta["clerk_user_id"] = g.user_id

        session = stripe_svc.create_checkout_session(
            customer_id=cust.id,
            price_id=price_id,
            quantity=1,
            meta=meta,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return _json_ok({"url": session.url})
    except Exception as e:
        if _is_stripe_invalid_request(e):
            current_app.logger.warning(
                "[checkout_pro] stripe invalid request: %s",
                _stripe_invalid_request_to_message(e),
            )
            return _json_err(_stripe_invalid_request_to_message(e), 400)

        current_app.logger.exception("[checkout_pro] error")
        return _json_err(str(e), 500)


@bp.route("/checkout/enterprise", methods=["POST", "OPTIONS"])
@require_auth
def checkout_enterprise():
    if not g.org_id:
        return _json_err("Debes indicar organización (X-Org-Id o en el token).", 400)

    # ✅ HARDENING: evitar org_id arbitrario
    if not clerk_svc.is_user_member_of_org(g.org_id, g.user_id):
        return _json_err("No eres miembro de la organización indicada (X-Org-Id).", 403)
    if not _is_adminish_in_org(g.user_id, g.org_id):
        return _json_err(
            "No tienes permisos suficientes: debes ser admin de la organización para contratar Enterprise.",
            403,
        )

    body = request.get_json(silent=True) or {}

    seats, seats_err = _parse_seats(body)
    if seats_err:
        return _json_err(seats_err, 400)

    price_id = body.get("price") or _cfg("STRIPE_PRICE_ENTERPRISE")
    if not price_id:
        return _json_err("Falta STRIPE_PRICE_ENTERPRISE", 500)

    try:
        success_url, cancel_url = _success_cancel_pricing(g.org_id)

        cust = stripe_svc.get_or_create_customer_for_entity(
            entity_type="org",
            entity_id=g.org_id,
            email=None,
            name=None,
            extra_metadata={"created_from": "checkout_enterprise"},
        )

        meta = stripe_svc.build_enterprise_meta(
            org_id=g.org_id,
            seats=seats,
            price_id=price_id,
            plan="enterprise",
            plan_scope="org",
            entity_email="",
            entity_name="",
        )
        meta["scope"] = "org"
        meta["org_id"] = g.org_id
        meta["buyer_user_id"] = g.user_id
        meta["seats"] = str(seats)

        session = stripe_svc.create_checkout_session(
            customer_id=cust.id,
            price_id=price_id,
            quantity=seats,
            meta=meta,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return _json_ok({"url": session.url})
    except Exception as e:
        if _is_stripe_invalid_request(e):
            current_app.logger.warning(
                "[checkout_enterprise] stripe invalid request: %s",
                _stripe_invalid_request_to_message(e),
            )
            return _json_err(_stripe_invalid_request_to_message(e), 400)

        current_app.logger.exception("[checkout_enterprise] error")
        return _json_err(str(e), 500)


@bp.route("/portal", methods=["POST", "OPTIONS"])
@require_auth
def billing_portal():
    try:
        base = _frontend_base()
        ret_url = f"{base}/settings/billing"

        if g.org_id:
            cust = stripe_svc.get_or_create_customer_for_entity(
                entity_type="org",
                entity_id=g.org_id,
                email=None,
                name=None,
                extra_metadata={"created_from": "portal"},
            )
        else:
            cust = stripe_svc.get_or_create_customer_for_entity(
                entity_type="user",
                entity_id=g.user_id,
                email=g.email,
                name=None,
                extra_metadata={"created_from": "portal"},
            )

        portal = stripe_svc.create_billing_portal(customer_id=cust.id, return_url=ret_url)
        return _json_ok({"url": portal.url})
    except Exception as e:
        current_app.logger.exception("[billing_portal] error")
        return _json_err(str(e), 500)


@bp.route("/invoices", methods=["GET", "OPTIONS"])
@require_auth
def billing_invoices():
    try:
        try:
            limit = max(1, min(100, int(request.args.get("limit", "20"))))
        except Exception:
            limit = 20

        if g.org_id:
            data = stripe_svc.list_invoices_for_org(org_id=g.org_id, limit=limit)
        else:
            data = stripe_svc.list_invoices_for_user(user_id=g.user_id, email=g.email, limit=limit)

        return _json_ok(data)
    except Exception as e:
        current_app.logger.exception("[billing_invoices] error")
        return _json_err(str(e), 500)
