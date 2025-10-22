# app/blueprints/enterprise.py
from __future__ import annotations

from typing import Any, Dict, Optional, List
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

import requests
from flask import Blueprint, current_app, request, g

from app.auth import require_auth, require_org_admin

bp = Blueprint("enterprise", __name__, url_prefix="/api/enterprise")

# ───────────────── Roles (Clerk ↔ API) ─────────────────
# → Cuando escribimos a Clerk (invites / memberships)
CLERK_ROLE_TO_API = {
    "admin": "org:admin",
    "owner": "org:admin",
    "org:admin": "org:admin",
    "member": "org:member",
    "org:member": "org:member",
    "basic_member": "org:member",  # compat hacia delante
}
# ← Cuando leemos desde Clerk
CLERK_ROLE_FROM_API = {
    "admin": "admin",
    "owner": "admin",
    "org:admin": "admin",
    "basic_member": "member",
    "member": "member",
    "org:member": "member",
}

def _normalize_role(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    return CLERK_ROLE_FROM_API.get(str(v).strip().lower(), None)


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
    if not r.text or not r.text.strip():
        return None
    try:
        return r.json()
    except Exception:
        return r.text

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
    """
    Normaliza un membership de Clerk, aceptando respuestas con:
    - include_public_user_data=true (public_user_data)
    - expand=user
    """
    pud = (m.get("public_user_data") or {})
    user = (m.get("user") or {})
    uid = m.get("user_id") or pud.get("user_id") or user.get("id")

    email = pud.get("email_address") or _extract_email_from_user(user)
    name = " ".join([
        (pud.get("first_name") or user.get("first_name") or "") or "",
        (pud.get("last_name") or user.get("last_name") or "") or "",
    ]).strip()

    role = _normalize_role(m.get("role")) or "member"
    return {
        "id": m.get("id"),  # membership_id
        "membership_id": m.get("id"),
        "user_id": uid,
        "email": email,
        "name": name,
        "role": role,
    }

def _find_membership_id(org_id: str, user_id: str) -> Optional[str]:
    """Devuelve membership_id a partir de org_id + user_id."""
    res = _req("GET", f"/organizations/{org_id}/memberships?limit=200&include_public_user_data=true")
    for m in res.get("data", []):
        if (m.get("user_id") or (m.get("public_user_data") or {}).get("user_id")) == user_id:
            return m.get("id")
    return None

def _find_membership(org_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    Devuelve el membership del usuario en la org:
      1) Busca en /organizations/:id/memberships
      2) Fallback: /users/:id?expand=organization_memberships y, si hay id, hidrata con expand=user
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
    """
    Completa email/nombre/user_id si faltan:
      - Primero, membership con expand=user
      - Luego, /users/:id?expand=email_addresses
    """
    for it in items:
        uid = it.get("user_id")
        if not (it.get("email") and uid):
            mid = it.get("membership_id")
            try:
                mem = _req("GET", f"/organizations/{org_id}/memberships/{mid}?expand=user&include_public_user_data=true")
                n = _normalize_member(mem or {})
                for k in ("user_id", "email", "name"):
                    if not it.get(k) and n.get(k):
                        it[k] = n[k]
                uid = it.get("user_id")
            except Exception:
                pass

        if uid and (not it.get("email") or not it.get("name")):
            try:
                u = _req("GET", f"/users/{uid}?expand=email_addresses")
                it["email"] = it.get("email") or _extract_email_from_user(u or {})
                fn = (u or {}).get("first_name") or ""
                ln = (u or {}).get("last_name") or ""
                full = (fn + " " + ln).strip()
                if full:
                    it["name"] = it.get("name") or full
            except Exception:
                pass


def _json_ok(payload: Any, code: int = 200):
    return ({"ok": True, "data": payload}, code)

def _json_err(msg: str, code: int = 400):
    return ({"ok": False, "error": msg}, code)

def _frontend_base() -> str:
    return (current_app.config.get("FRONTEND_BASE_URL") or "http://localhost:3000").rstrip("/")

def _append_query(url: str, extra: Dict[str, str]) -> str:
    u = urlparse(url)
    q = dict(parse_qsl(u.query))
    q.update({k: v for k, v in extra.items() if v is not None})
    return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(q), u.fragment))


# ─────────────── Utilidades de asientos / admins ───────────────
def _org_usage(org_id: str) -> Dict[str, int]:
    """Devuelve contadores: members (aceptados), pending (invitaciones), used (members+pending)."""
    members = 0
    pending = 0
    try:
        mems = _req("GET", f"/organizations/{org_id}/memberships?limit=200&include_public_user_data=true")
        members = len(mems.get("data", []))
    except Exception:
        pass
    try:
        invs = _req("GET", f"/organizations/{org_id}/invitations?status=pending&limit=200")
        arr = invs if isinstance(invs, list) else (invs.get("data") or [])
        pending = len(arr)
    except Exception:
        pass
    return {"members": members, "pending": pending, "used": members + pending}

def _count_admins(org_id: str) -> int:
    try:
        res = _req("GET", f"/organizations/{org_id}/memberships?limit=200")
        return sum(1 for m in res.get("data", []) if _normalize_role(m.get("role")) == "admin")
    except Exception:
        return 0

def _is_last_admin(org_id: str, membership_id: str) -> bool:
    try:
        mem = _req("GET", f"/organizations/{org_id}/memberships/{membership_id}")
        if _normalize_role(mem.get("role")) != "admin":
            return False
    except Exception:
        return False
    return _count_admins(org_id) <= 1


# ───────────────── Endpoints ─────────────────
@bp.route("/org", methods=["GET", "OPTIONS"])
@require_auth
def get_org_info():
    if not g.org_id:
        return _json_err("Debes indicar organización (X-Org-Id o en el token).", 400)

    try:
        org = _req("GET", f"/organizations/{g.org_id}")

        # Rol "estimado" del token…
        current_role = g.org_role

        # …y rol "real" consultando membership en Clerk
        try:
            mem = _find_membership(g.org_id, g.user_id)
            if mem and mem.get("role"):
                nr = _normalize_role(mem.get("role"))
                if nr:
                    current_role = nr
        except Exception:
            pass

        # Seats configurados en public_metadata
        seats = int((org.get("public_metadata") or {}).get("seats") or 0)

        # Métricas
        usage = _org_usage(g.org_id)
        used = usage["used"]
        free = max(0, seats - used)

        out = {
            "id": org.get("id"),
            "name": org.get("name"),
            "slug": org.get("slug"),
            "seats": seats,
            "used_seats": used,       # compat
            "pending_invites": usage["pending"],
            "current_user_role": current_role,
            # campos claros
            "used": used,
            "free_seats": free,
            "free": free,
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


@bp.route("/invitations", methods=["GET", "OPTIONS"])
@require_auth
@require_org_admin
def list_invitations():
    """Lista invitaciones (por defecto solo 'pending'). Soporta ?status=pending|accepted|revoked|expired|all."""
    if not g.org_id:
        return _json_err("Missing org (X-Org-Id).", 400)
    status = (request.args.get("status") or "pending").strip().lower()
    q = "" if status in ("all", "*") else f"?status={status}"
    try:
        res = _req("GET", f"/organizations/{g.org_id}/invitations{q}&limit=200" if q else f"/organizations/{g.org_id}/invitations?limit=200")
        arr = res if isinstance(res, list) else (res.get("data") or [])
        items = [{
            "id": it.get("id"),
            "email": it.get("email_address"),
            "status": it.get("status"),
            "role": CLERK_ROLE_FROM_API.get((it.get("role") or "").lower(), it.get("role")),
            "created_at": it.get("created_at"),
            "updated_at": it.get("updated_at"),
            "expires_at": it.get("expires_at"),
        } for it in arr]
        return _json_ok({"items": items, "total": len(items)})
    except Exception as e:
        return _json_err(f"Clerk error: {e}", 502)


@bp.route("/invitations/revoke", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def revoke_invitation():
    """
    Revoca invitaciones por id (preferido) o por email(s).
    Usa Clerk: POST /organizations/{org}/invitations/{id}/revoke con requesting_user_id.
    """
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    emails = data.get("emails") or []
    if isinstance(ids, str):
        ids = [ids]
    if isinstance(emails, str):
        emails = [emails]

    revoked: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    try:
        # Si llegan emails, mapear a ids desde pendientes
        if emails && not ids:
            res = _req("GET", f"/organizations/{g.org_id}/invitations?status=pending&limit=200")
            arr = res if isinstance(res, list) else (res.get("data") or [])
            email_to_id = {it.get("email_address"): it.get("id") for it in arr}
            ids = [email_to_id[e] for e in emails if e in email_to_id]

        if not ids:
            return _json_err("Debes indicar 'ids' o 'emails'.", 400)

        for inv_id in ids:
            try:
                _req("POST", f"/organizations/{g.org_id}/invitations/{inv_id}/revoke",
                     json={"requesting_user_id": g.user_id})
                revoked.append({"id": inv_id, "revoked": True})
            except Exception as ex:
                errors.append({"id": inv_id, "error": str(ex)})

        ok = {"results": revoked, "errors": errors, "revoked": len(revoked), "failed": len(errors)}
        code = 207 if errors and revoked else 200 if revoked and not errors else 502
        return _json_ok(ok, code=code)
    except Exception as e:
        return _json_err(f"Clerk error: {e}", 502)


@bp.route("/invite", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def invite_user():
    """
    Invita N emails haciendo 1 llamada por email al endpoint simple:
    POST /organizations/{org_id}/invitations
    Body: { inviter_user_id, email_address, role (org:*), redirect_url, [expires_in_days] }
    """
    data = request.get_json(silent=True) or {}

    emails = data.get("emails")
    if isinstance(emails, str):
        emails = [emails]
    emails = [e.strip() for e in (emails or []) if e and isinstance(e, str)]
    if not emails:
        return _json_err("Debes indicar 'emails'.", 400)

    role_in = (data.get("role") or "member").strip().lower()
    role_api = CLERK_ROLE_TO_API.get(role_in)
    if role_api not in ("org:member", "org:admin"):
        return _json_err("role debe ser 'admin' o 'member'.", 400)

    allow_overbook = bool(data.get("allow_overbook", False))

    # Redirect seguro a /accept-invitation con org_id como query param
    frontend = _frontend_base()
    base_redirect = f"{frontend}/accept-invitation"
    redirect_url = data.get("redirect_url") or _append_query(base_redirect, {"org_id": g.org_id})
    if not str(redirect_url).startswith(frontend):
        redirect_url = _append_query(base_redirect, {"org_id": g.org_id})

    expires_in_days = data.get("expires_in_days")
    if expires_in_days is not None:
        try:
            expires_in_days = int(expires_in_days)
            if not (1 <= expires_in_days <= 30):
                return _json_err("'expires_in_days' debe estar entre 1 y 30.", 400)
        except Exception:
            return _json_err("'expires_in_days' debe ser entero.", 400)

    # Guard-rail de asientos (members + pending)
    try:
        org = _req("GET", f"/organizations/{g.org_id}")
        seats = int((org.get("public_metadata") or {}).get("seats") or 0)
    except Exception:
        seats = 0
    usage = _org_usage(g.org_id)
    used = usage["used"]
    free = max(0, seats - used)
    needed = len(emails)
    if not allow_overbook and needed > free:
        return ({"ok": False, "error": "not_enough_seats",
                 "details": {"seats": seats, "used": used, "free": free, "needed": needed}}, 409)

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for email in emails:
        payload = {
            "inviter_user_id": g.user_id,
            "email_address": email,
            "role": role_api,
            "redirect_url": redirect_url,
        }
        if expires_in_days is not None:
            payload["expires_in_days"] = expires_in_days

        try:
            r = _req("POST", f"/organizations/{g.org_id}/invitations", json=payload)
            results.append({
                "id": r.get("id"),
                "email": r.get("email_address"),
                "status": r.get("status"),
            })
        except Exception as e:
            errors.append({"email": email, "error": str(e)})

    code = 207 if errors and results else 200 if results else 502
    return _json_ok({"results": results, "errors": errors}, code)


@bp.route("/update-role", methods=["POST", "OPTIONS"])
@require_auth
@require_org_admin
def update_role():
    data = request.get_json(silent=True) or {}
    membership_id = data.get("membership_id")
    user_id = data.get("user_id")
    role = (data.get("role") or "").lower().strip()
    role_api = CLERK_ROLE_TO_API.get(role)  # -> org:admin/org:member
    if role_api not in ("org:admin", "org:member"):
        return _json_err("role debe ser 'admin' o 'member'.", 400)

    if not membership_id and user_id:
        membership_id = _find_membership_id(g.org_id, user_id)
    if not membership_id:
        return _json_err("membership_id o user_id requeridos.", 400)

    # Guard-rail: no degradar al último admin
    if role_api != "org:admin" and _is_last_admin(g.org_id, membership_id):
        return ({"ok": False, "error": "cannot_demote_last_admin"}, 409)

    try:
        res = _req("PATCH", f"/organizations/{g.org_id}/memberships/{membership_id}",
                   json={"role": role_api})
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

    # Guard-rail: no eliminar al último admin
    if _is_last_admin(g.org_id, membership_id):
        return ({"ok": False, "error": "cannot_remove_last_admin"}, 409)

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
