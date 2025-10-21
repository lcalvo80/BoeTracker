from __future__ import annotations

from typing import Any, Dict, Optional, List

import requests
from flask import Blueprint, current_app, request, g

from app.auth import require_auth, require_org_admin

bp = Blueprint("enterprise", __name__)

# ───────────────── Mapeos de roles ─────────────────
CLERK_ROLE_TO_API = {
    "admin": "admin",
    "member": "basic_member",
    "basic_member": "basic_member",
}
CLERK_ROLE_FROM_API = {
    "admin": "admin",
    "basic_member": "member",
}

@bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)

# ───────────────── Clerk helpers ─────────────────
def _clerk_headers() -> Dict[str, str]:
    key = current_app.config.get("CLERK_SECRET_KEY", "")
    if not key:
        raise RuntimeError("Falta CLERK_SECRET_KEY")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

def _clerk_base() -> str:
    return (current_app.config.get("CLERK_API_BASE") or "https://api.clerk.com/v1").rstrip("/")

def _req(method: str, path: str, **kwargs) -> Any:
    url = f"{_clerk_base()}{path}"
    r = requests.request(method, url, headers=_clerk_headers(), timeout=20, **kwargs)
    if r.status_code >= 400:
        raise RuntimeError(f"Clerk {method} {path} -> {r.status_code}: {r.text}")
    return r.json() if r.text.strip() else None

def _extract_email_from_user(user: Dict[str, Any] | None) -> Optional[str]:
    if not user:
        return None
    if user.get("email_address"):
        return user.get("email_address")
    primary_id = user.get("primary_email_address_id")
    emails = user.get("email_addresses") or []
    if primary_id:
        for ea in emails:
            if ea.get("id") == primary_id and ea.get("email_address"):
                return ea["email_address"]
    for ea in emails:
        if ea.get("email_address"):
            return ea["email_address"]
    return None

def _normalize_member(m: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza un membership de Clerk con varias formas de respuesta (public_user_data / expand=user)."""
    pud = (m.get("public_user_data") or {})
    user = (m.get("user") or {})
    uid = m.get("user_id") or pud.get("user_id") or user.get("id")

    email = pud.get("email_address") or _extract_email_from_user(user)
    name = " ".join([
        (pud.get("first_name") or user.get("first_name") or "") or "",
        (pud.get("last_name") or user.get("last_name") or "") or "",
    ]).strip()

    role = CLERK_ROLE_FROM_API.get(m.get("role"), "member")
    return {
        "id": m.get("id"),  # membership_id
        "membership_id": m.get("id"),
        "user_id": uid,
        "email": email,
        "name": name,
        "role": role,
    }

def _find_membership_id(org_id: str, user_id: str) -> Optional[str]:
    """Búsqueda rápida de membership_id en la org (usando include_public_user_data)."""
    res = _req("GET", f"/organizations/{org_id}/memberships?limit=100&include_public_user_data=true")
    for m in res.get("data", []):
        if (m.get("user_id") or (m.get("public_user_data") or {}).get("user_id")) == user_id:
            return m.get("id")
    return None

def _find_membership(org_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    Devuelve el membership del usuario en la org.
    1) Lista de memberships de la org (include_public_user_data) → match por user_id
    2) Fallback: /users/{id}?expand=organization_memberships → hidrata membership con expand=user
    """
    try:
        res = _req("GET", f"/organizations/{org_id}/memberships?limit=200&include_public_user_data=true")
        for m in res.get("data", []):
            uid = m.get("user_id") or (m.get("public_user_data") or {}).get("user_id")
            if uid == user_id:
                return m
    except Exception:
        pass

    try:
        u = _req("GET", f"/users/{user_id}?expand=organization_memberships")
        for mem in (u.get("organization_memberships") or []):
            if mem.get("organization_id") == org_id or mem.get("organization") == org_id:
                mid = mem.get("id")
                if mid:
                    try:
                        return _req("GET", f"/organizations/{org_id}/memberships/{mid}?expand=user&include_public_user_data=true")
                    except Exception:
                        return mem
                return mem
    except Exception:
        pass

    return None

def _hydrate_members_if_needed(org_id: str, items: List[Dict[str, Any]]) -> None:
    """Para los items sin email o user_id, intenta hidratar con expand=user y, último recurso, /users/{id}."""
    for it in items:
        if it.get("email") and it.get("user_id"):
            continue
        mid = it.get("membership_id")
        try:
            mem = _req("GET", f"/organizations/{org_id}/memberships/{mid}?expand=user&include_public_user_data=true")
            n = _normalize_member(mem or {})
            for k in ("user_id", "email", "name"):
                if not it.get(k) and n.get(k):
                    it[k] = n[k]
            if it.get("user_id") and not it.get("email"):
                u = _req("GET", f"/users/{it['user_id']}?expand=email_addresses")
                it["email"] = _extract_email_from_user(u or {}) or it.get("email")
        except Exception:
            pass

def _json_ok(payload: Any, code: int = 200):
    return ({"ok": True, "data": payload}, code)

def _json_err(msg: str, code: int = 400):
    return ({"ok": False, "error": msg}, code)

# ───────────────── Endpoints ─────────────────
@bp.route("/org", methods=["GET", "OPTIONS"])
@require_auth
def get_org_info():
    if not g.org_id:
        return _json_err("Debes indicar organización (X-Org-Id o en el token).", 400)

    try:
        org = _req("GET", f"/organizations/{g.org_id}")

        # Rol por defecto desde el token (puede ser None si hay placeholders)
        current_role = g.org_role

        # Rol real (robusto)
        try:
            mem = _find_membership(g.org_id, g.user_id)
            if mem and mem.get("role"):
                current_role = CLERK_ROLE_FROM_API.get(mem.get("role"), current_role or "member")
        except Exception:
            pass

        # Seats configurados en metadata
        seats = int((org.get("public_metadata") or {}).get("seats") or 0)

        # Métricas de uso e invitaciones
        used_seats = 0
        pending_invites = 0
        try:
            mems = _req("GET", f"/organizations/{g.org_id}/memberships?limit=200&include_public_user_data=true")
            used_seats = len(mems.get("data", []))
        except Exception:
            pass
        try:
            invs = _req("GET", f"/organizations/{g.org_id}/invitations?status=pending&limit=200")
            arr = invs if isinstance(invs, list) else (invs.get("data") or [])
            pending_invites = len(arr)
        except Exception:
            pass

        out = {
            "id": org.get("id"),
            "name": org.get("name"),
            "slug": org.get("slug"),
            "seats": seats,
            "used_seats": used_seats,
            "pending_invites": pending_invites,
            "current_user_role": current_role,
        }
        return _json_ok(out)
    except Exception as e:
        return _json_err(f"Clerk error: {e}", 502)

@bp.route("/users", methods=["GET", "OPTIONS"])
@require_auth
def list_users():
    if not g.org_id:
        return _json_err("Missing org (X-Org-Id).", 400)
    try:
        res = _req("GET", f"/organizations/{g.org_id}/memberships?limit=200&include_public_user_data=true")
        raw = res.get("data", [])
        items = [_normalize_member(m) for m in raw]
        _hydrate_members_if_needed(g.org_id, items)
        return _json_ok({"items": items, "total": len(items)})
    except Exception as e:
        return _json_err(f"Clerk error: {e}", 502)

@bp.route("/invite", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def invite_user():
    data = request.get_json(silent=True) or {}
    emails = data.get("emails")
    if isinstance(emails, str):
        emails = [emails]
    emails = [e for e in (emails or []) if e]
    if not emails:
        return _json_err("Debes indicar 'emails'.", 400)

    role_in = (data.get("role") or "member").strip().lower()
    role_api = CLERK_ROLE_TO_API.get(role_in, "basic_member")

    redirect_url = data.get("redirect_url") or (
        current_app.config.get("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")
        + "/auth/callback"
    )

    payload = {
        "email_addresses": emails,
        "role": role_api,
        "redirect_url": redirect_url,
        "allow_duplicates": False,
        "send_email": True,
    }

    try:
        res = _req("POST", f"/organizations/{g.org_id}/invitations", json=payload)
        out = [{"email": r.get("email_address"), "status": r.get("status")} for r in (res or [])]
        return _json_ok({"results": out})
    except Exception as e:
        return _json_err(f"Clerk error: {e}", 502)

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

    try:
        res = _req("PATCH", f"/organizations/{g.org_id}/memberships/{membership_id}", json={"role": role_api})
        return _json_ok(_normalize_member(res))
    except Exception as e:
        return _json_err(f"Clerk error: {e}", 502)

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

    try:
        _req("DELETE", f"/organizations/{g.org_id}/memberships/{membership_id}")
        return _json_ok({"removed": True, "membership_id": membership_id})
    except Exception as e:
        return _json_err(f"Clerk error: {e}", 502)

@bp.route("/set-seat-limit", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def set_seat_limit():
    data = request.get_json(silent=True) or {}
    try:
        seats = max(0, int(data.get("seats")))
    except Exception:
        return _json_err("'seats' debe ser número entero.", 400)

    try:
        org = _req("PATCH", f"/organizations/{g.org_id}", json={"public_metadata": {"seats": seats}})
        return _json_ok({"org_id": org.get("id"), "seats": seats})
    except Exception as e:
        return _json_err(f"Clerk error: {e}", 502)
