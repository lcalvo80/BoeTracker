# app/blueprints/enterprise.py
from __future__ import annotations

from typing import Any, Optional

from flask import Blueprint, request, g

from app.auth import require_auth, require_org_admin
from app.services import clerk_svc

# ⚠️ IMPORTANTÍSIMO:
# NO pongas url_prefix aquí si en app/__init__.py ya registras:
# app.register_blueprint(enterprise_bp, url_prefix="/api/enterprise")
bp = Blueprint("enterprise", __name__)


@bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)


def _json_ok(payload: Any, code: int = 200):
    return ({"ok": True, "data": payload}, code)


def _json_err(msg: str, code: int = 400, *, extra: dict | None = None):
    out = {"ok": False, "error": msg}
    if extra:
        out["details"] = extra
    return (out, code)


def _status_from_exception(e: Exception) -> tuple[int, str, Optional[dict]]:
    # Semántica especial para ClerkHttpError (incluye 409 seat guard / last admin)
    if isinstance(e, clerk_svc.ClerkHttpError):
        if e.status_code == 409 and "not_enough_seats" in (e.body or ""):
            details = {"reason": "not_enough_seats", "raw": e.body}
            return 409, "not_enough_seats", details

        if e.status_code == 409 and "cannot_demote_last_admin" in (e.body or ""):
            return 409, "cannot_demote_last_admin", None
        if e.status_code == 409 and "cannot_remove_last_admin" in (e.body or ""):
            return 409, "cannot_remove_last_admin", None
        if e.status_code == 404 and "membership_not_found" in (e.body or ""):
            return 404, "membership_not_found", None

        return 502, f"Clerk error: {e}", None

    if isinstance(e, ValueError):
        return 400, str(e), None

    return 502, f"Clerk error: {e}", None


# ───────────────── Nuevo endpoint: crear organización idempotente ─────────────────
@bp.route("/create-org", methods=["POST", "OPTIONS"])
@require_auth
def create_org():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    try:
        out = clerk_svc.enterprise_create_org_idempotent(user_id=g.user_id, name=name)
        return _json_ok(out, 200)
    except Exception as e:
        code, msg, details = _status_from_exception(e)
        return _json_err(msg, code, extra=details)


# ───────────────── 5) Cleanup cuando NO se completa el pago ─────────────────
@bp.route("/checkout/cancel", methods=["POST", "OPTIONS"])
@require_auth
def checkout_cancel():
    """
    MVP: se llama cuando el FE vuelve con checkout=cancel.
    Limpia todas las orgs del usuario con private_metadata.pending_enterprise_checkout=true.
    Devuelve cuántas limpió.
    """
    try:
        out = clerk_svc.enterprise_checkout_cancel_cleanup(user_id=g.user_id)
        return _json_ok(out, 200)
    except Exception as e:
        code, msg, details = _status_from_exception(e)
        return _json_err(msg, code, extra=details)


# ───────────────── Endpoints ─────────────────
@bp.route("/org", methods=["GET", "OPTIONS"])
@require_auth
def get_org_info():
    try:
        out = clerk_svc.enterprise_get_org_info(
            org_id=getattr(g, "org_id", None),
            user_id=g.user_id,
            token_role=getattr(g, "org_role", None),
        )
        return _json_ok(out, 200)
    except Exception as e:
        code, msg, details = _status_from_exception(e)
        return _json_err(msg, code, extra=details)


@bp.route("/users", methods=["GET", "OPTIONS"])
@require_auth
def list_users():
    try:
        out = clerk_svc.enterprise_list_users(org_id=getattr(g, "org_id", None))
        return _json_ok(out, 200)
    except Exception as e:
        code, msg, details = _status_from_exception(e)
        return _json_err(msg, code, extra=details)


@bp.route("/invitations", methods=["GET", "OPTIONS"])
@require_auth
@require_org_admin
def list_invitations():
    try:
        status = (request.args.get("status") or "").strip().lower()
        statuses = (request.args.get("statuses") or "").strip().lower()
        if statuses and not status:
            status = statuses.split(",")[0].strip()

        out = clerk_svc.enterprise_list_invitations(
            org_id=getattr(g, "org_id", None),
            status=status or None,
        )
        return _json_ok(out, 200)
    except Exception as e:
        code, msg, details = _status_from_exception(e)
        return _json_err(msg, code, extra=details)


@bp.route("/invitations/revoke", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def revoke_invitation():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    emails = data.get("emails") or []
    if isinstance(ids, str):
        ids = [ids]
    if isinstance(emails, str):
        emails = [emails]

    try:
        out = clerk_svc.enterprise_revoke_invitations(
            org_id=getattr(g, "org_id", None),
            requesting_user_id=g.user_id,
            ids=ids,
            emails=emails,
        )
        code = 207 if out.get("failed") and out.get("revoked") else 200 if out.get("revoked") else 502
        return _json_ok(out, code)
    except Exception as e:
        code, msg, details = _status_from_exception(e)
        return _json_err(msg, code, extra=details)


@bp.route("/invite", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def invite_user():
    data = request.get_json(silent=True) or {}

    emails = data.get("emails")
    if isinstance(emails, str):
        emails = [emails]
    emails = emails or []

    role = (data.get("role") or "member").strip().lower()
    allow_overbook = bool(data.get("allow_overbook", False))
    redirect_url = data.get("redirect_url")
    expires_in_days = data.get("expires_in_days")

    try:
        out = clerk_svc.enterprise_invite_users(
            org_id=getattr(g, "org_id", None),
            inviter_user_id=g.user_id,
            emails=emails,
            role=role,
            allow_overbook=allow_overbook,
            redirect_url=redirect_url,
            expires_in_days=expires_in_days,
        )
        code = 207 if out.get("errors") and out.get("results") else 200 if out.get("results") else 502
        return _json_ok(out, code)
    except Exception as e:
        if isinstance(e, clerk_svc.ClerkHttpError) and e.status_code == 409 and "not_enough_seats" in (e.body or ""):
            return (
                {"ok": False, "error": "not_enough_seats", "details": {"raw": e.body}},
                409,
            )
        code, msg, details = _status_from_exception(e)
        return _json_err(msg, code, extra=details)


@bp.route("/update-role", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def update_role():
    data = request.get_json(silent=True) or {}
    membership_id = data.get("membership_id")
    user_id = data.get("user_id")
    role = (data.get("role") or "").lower().strip()

    try:
        out = clerk_svc.enterprise_update_role(
            org_id=getattr(g, "org_id", None),
            membership_id=membership_id,
            user_id=user_id,
            role=role,
        )
        return _json_ok(out, 200)
    except Exception as e:
        code, msg, details = _status_from_exception(e)
        return _json_err(msg, code, extra=details)


@bp.route("/remove", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def remove_user():
    data = request.get_json(silent=True) or {}
    membership_id = data.get("membership_id")
    user_id = data.get("user_id")

    try:
        out = clerk_svc.enterprise_remove_user(
            org_id=getattr(g, "org_id", None),
            membership_id=membership_id,
            user_id=user_id,
        )
        return _json_ok(out, 200)
    except Exception as e:
        code, msg, details = _status_from_exception(e)
        return _json_err(msg, code, extra=details)


@bp.route("/set-seat-limit", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def set_seat_limit():
    data = request.get_json(silent=True) or {}
    try:
        seats = int(data.get("seats"))
        if seats < 0:
            raise ValueError()
    except Exception:
        return _json_err("'seats' debe ser número entero.", 400)

    try:
        out = clerk_svc.enterprise_set_seat_limit(org_id=getattr(g, "org_id", None), seats=seats)
        return _json_ok({"org_id": out["org_id"], "seats": out["seats"]}, 200)
    except Exception as e:
        code, msg, details = _status_from_exception(e)
        return _json_err(msg, code, extra=details)
