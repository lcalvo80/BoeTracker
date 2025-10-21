from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests
from flask import Blueprint, current_app, request, jsonify, g

from app.auth import require_auth, require_org_admin

bp = Blueprint("enterprise", __name__)

CLERK_ROLE_TO_API = {"admin": "admin", "member": "basic_member"}
CLERK_ROLE_FROM_API = {"admin": "admin", "basic_member": "member"}


@bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)


# ───────────────── Helpers Clerk ─────────────────

def _clerk_headers() -> Dict[str, str]:
    key = current_app.config.get("CLERK_SECRET_KEY", "")
    if not key:
        raise RuntimeError("Falta CLERK_SECRET_KEY")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _clerk_base() -> str:
    return (current_app.config.get("CLERK_API_BASE") or "https://api.clerk.com/v1").rstrip("/")


def _req(method: str, path: str, **kwargs) -> Any:
    url = f"{_clerk_base()}{path}"
    r = requests.request(method, url, headers=_clerk_headers(), timeout=20, **kwargs)
    if r.status_code >= 400:
        raise RuntimeError(f"Clerk {method} {path} -> {r.status_code}: {r.text}")
    if r.text.strip():
        return r.json()
    return None


def _normalize_member(m: Dict[str, Any]) -> Dict[str, Any]:
    user = (m.get("public_user_data") or {}) if "public_user_data" in m else (m.get("user") or {})
    emails = user.get("email_addresses") or []
    email = user.get("email_address") or (emails[0] if emails else None)
    name = user.get("first_name", "") + (" " + user.get("last_name", "") if user.get("last_name") else "")
    role = CLERK_ROLE_FROM_API.get(m.get("role"), "member")
    return {
        "membership_id": m.get("id"),
        "user_id": m.get("user_id"),
        "email": email,
        "name": name.strip(),
        "role": role,
    }


def _find_membership_id(org_id: str, user_id: str) -> Optional[str]:
    res = _req("GET", f"/organizations/{org_id}/memberships?limit=100")
    for m in res.get("data", []):
        if m.get("user_id") == user_id:
            return m.get("id")
    return None


def _json_ok(payload: Any):
    return jsonify({"ok": True, "data": payload})


def _json_err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


# ───────────────── Endpoints ─────────────────

@bp.route("/org", methods=["GET", "OPTIONS"])
@require_auth
def get_org_info():
    if not g.org_id:
        return _json_err("Debes indicar organización (X-Org-Id o en el token).", 400)

    org = _req("GET", f"/organizations/{g.org_id}")
    # Intentamos resolver el rol del usuario actual de forma robusta
    current_role = g.org_role
    try:
        mid = _find_membership_id(g.org_id, g.user_id)
        if mid:
            mem = _req("GET", f"/organizations/{g.org_id}/memberships/{mid}")
            if mem:
                current_role = CLERK_ROLE_FROM_API.get(mem.get("role"), current_role or "member")
    except Exception:
        pass

    seats = int((org.get("public_metadata") or {}).get("seats") or 0)
    out = {
        "id": org.get("id"),
        "name": org.get("name"),
        "slug": org.get("slug"),
        "seats": seats,
        "current_user_role": current_role,
    }
    return _json_ok(out)


@bp.route("/users", methods=["GET", "OPTIONS"])
@require_auth
def list_users():
    if not g.org_id:
        return _json_err("Missing org (X-Org-Id).", 400)

    res = _req("GET", f"/organizations/{g.org_id}/memberships?limit=200")
    items = [_normalize_member(m) for m in res.get("data", [])]
    return _json_ok({"items": items, "total": len(items)})


@bp.route("/invite", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def invite_user():
    data = request.get_json(silent=True) or {}
    emails = data.get("emails")
    if isinstance(emails, str):
        emails = [emails]
    emails = [e for e in (emails or []) if e]

    role = (data.get("role") or "member").lower().strip()
    role_api = CLERK_ROLE_TO_API.get(role, "basic_member")

    if not emails:
        return _json_err("Debes indicar 'emails'.", 400)

    redirect_url = data.get("redirect_url") or (
        current_app.config.get("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")
        + "/auth/callback"
    )

    allow_overbook = bool(data.get("allow_overbook") or False)

    payload = {
        "email_addresses": emails,
        "role": role_api,
        "redirect_url": redirect_url,
        "allow_duplicates": False,
        "send_email": True,
    }
    # Clerk no gestiona 'seats', lo controlamos nosotros; allow_overbook sirve a nivel app
    if allow_overbook:
        payload["metadata"] = {"allow_overbook": True}

    res = _req("POST", f"/organizations/{g.org_id}/invitations", json=payload)
    out = [{"email": r.get("email_address"), "status": r.get("status")} for r in (res or [])]
    return _json_ok({"results": out})


@bp.route("/update-role", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def update_role():
    data = request.get_json(silent=True) or {}
    membership_id = data.get("membership_id")
    user_id = data.get("user_id")
    role = (data.get("role") or "").lower().strip()
    role_api = CLERK_ROLE_TO_API.get(role)
    if not role_api:
        return _json_err("role debe ser 'admin' o 'member'.", 400)

    if not membership_id and user_id:
        membership_id = _find_membership_id(g.org_id, user_id)
    if not membership_id:
        return _json_err("membership_id o user_id requeridos.", 400)

    res = _req(
        "PATCH",
        f"/organizations/{g.org_id}/memberships/{membership_id}",
        json={"role": role_api},
    )
    return _json_ok(_normalize_member(res))


@bp.route("/remove", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def remove_user():
    data = request.get_json(silent=True) or {}
    membership_id = data.get("membership_id")
    user_id = data.get("user_id")

    if not membership_id and user_id:
        membership_id = _find_membership_id(g.org_id, user_id)
    if not membership_id:
        return _json_err("membership_id o user_id requeridos.", 400)

    _req("DELETE", f"/organizations/{g.org_id}/memberships/{membership_id}")
    return _json_ok({"removed": True, "membership_id": membership_id})


@bp.route("/set-seat-limit", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def set_seat_limit():
    data = request.get_json(silent=True) or {}
    try:
        seats = max(0, int(data.get("seats")))
    except Exception:
        return _json_err("'seats' debe ser número entero.", 400)

    org = _req(
        "PATCH",
        f"/organizations/{g.org_id}",
        json={"public_metadata": {"seats": seats}},
    )
    return _json_ok({"org_id": org.get("id"), "seats": seats})
