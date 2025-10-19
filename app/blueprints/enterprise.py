# backend/enterprise.py
from __future__ import annotations
import os, math, time
from typing import Dict, Any, List, Tuple
import requests
from flask import Blueprint, request, jsonify, g, current_app

# ───────── Dependencias de tu app (ajusta) ─────────
# from yourapp.extensions import db
# from yourapp.auth import require_auth

bp = Blueprint("enterprise", __name__, url_prefix="/api/enterprise")

CLERK_API = "https://api.clerk.com/v1"
CLERK_SECRET = os.environ.get("CLERK_SECRET_KEY", "")

# Mapeo de roles BOE <-> Clerk
ROLE_TO_CLERK = {"admin": "admin", "member": "basic_member"}
ROLE_FROM_CLERK = {"admin": "admin", "basic_member": "member"}

# ============ Helpers comunes ==============================================

def _auth_headers():
    if not CLERK_SECRET:
        raise RuntimeError("CLERK_SECRET_KEY no configurado")
    return {"Authorization": f"Bearer {CLERK_SECRET}"}

def _clerk(method: str, path: str, *, params=None, json=None):
    url = f"{CLERK_API}{path}"
    r = requests.request(method, url, headers=_auth_headers(), params=params, json=json, timeout=15)
    # devolvemos el Response para poder inspeccionar status en algunos casos
    return r

def _org_id_from_context() -> str:
    """
    Obtiene el org_id desde g (JWT backend template) o desde el header 'X-Org-Id' como fallback.
    Debes tener un middleware que pueble g.user_id, g.org_id, g.org_role desde el JWT.
    """
    org_id = getattr(g, "org_id", None) or request.headers.get("X-Org-Id")
    if not org_id:
        raise ValueError("No hay organización activa (org_id).")
    return org_id

def _canonical_email(email: str) -> str:
    """
    Normaliza SOLO gmail/googlemail para evitar falsos duplicados por +alias y puntos.
    Para otros dominios no tocamos nada.
    """
    e = (email or "").strip().lower()
    if "@" not in e:
        return e
    local, domain = e.split("@", 1)
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
    return f"{local}@{domain}"

def _json_error(code: int, message: str, **extra):
    payload = {"message": message, **extra}
    return jsonify(payload), code

def _current_user_role() -> str:
    raw = (getattr(g, "org_role", "") or "").strip().lower()
    return "admin" if raw in ("admin", "owner", "org:admin", "org_admin", "organization_admin", "org:owner", "org_owner") else "member"


# ============ (opcional) Modelo de asientos =================================
# Si ya lo guardas en DB, deja este stub adaptado a tu ORM.
#
# class EnterpriseOrg(db.Model):
#     __tablename__ = "enterprise_orgs"
#     org_id = db.Column(db.String, primary_key=True)
#     seat_limit = db.Column(db.Integer, nullable=False, default=0)

def _get_seat_limit(org_id: str) -> int:
    """
    Lee el límite de asientos desde DB o metadata. Aquí: ejemplo con config/dict.
    Sustituye por tu query real (db.session.query(EnterpriseOrg.seat_limit)...).
    """
    # TODO: reemplazar por DB real
    # row = db.session.get(EnterpriseOrg, org_id)
    # return row.seat_limit if row else 0
    store = getattr(current_app, "_SEAT_LIMITS", {})
    return int(store.get(org_id, 0))

def _set_seat_limit(org_id: str, seats: int) -> None:
    # TODO: persistir en DB real
    store = getattr(current_app, "_SEAT_LIMITS", {})
    store[org_id] = int(max(0, seats))
    current_app._SEAT_LIMITS = store


# ============ Contadores (memberships + invites) ============================

def _list_memberships(org_id: str) -> List[Dict[str, Any]]:
    r = _clerk("GET", f"/organizations/{org_id}/memberships", params={"limit": 200})
    r.raise_for_status()
    data = r.json()
    items = data["data"] if "data" in data else data  # safety
    return items

def _list_pending_invitations(org_id: str) -> List[Dict[str, Any]]:
    r = _clerk("GET", f"/organizations/{org_id}/invitations", params={"status": "pending", "limit": 200})
    r.raise_for_status()
    data = r.json()
    items = data["data"] if "data" in data else data
    return items

def _count_used_and_pending(org_id: str) -> Tuple[int, int]:
    memberships = _list_memberships(org_id)
    pending = _list_pending_invitations(org_id)
    return len(memberships), len(pending)

def _is_last_admin(org_id: str, membership_id: str | None, user_id: str | None) -> bool:
    memberships = _list_memberships(org_id)
    admins = [m for m in memberships if (m.get("role") == "admin")]
    if len(admins) <= 1:
        # ¿el target es ese único admin?
        target = None
        if membership_id:
            target = next((m for m in memberships if m.get("id") == membership_id), None)
        elif user_id:
            target = next((m for m in memberships if (m.get("public_user_data", {}).get("user_id") == user_id or m.get("user_id") == user_id)), None)
        if target and target.get("role") == "admin":
            return True
    return False


# ============ GET /org ======================================================

@bp.get("/org")
# @require_auth(org=True)  # Asegúrate de proteger con tu decorador
def get_org():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))

    used, pending = _count_used_and_pending(org_id)
    seats = _get_seat_limit(org_id)

    # Puedes ampliar: name desde Clerk
    org = _clerk("GET", f"/organizations/{org_id}")
    org_name = org.json().get("name") if org.ok else None

    return jsonify({
        "id": org_id,
        "name": org_name,
        "seats": seats,
        "used_seats": used,
        "pending_invites": pending,
        "current_user_role": _current_user_role(),
        # opcionales para compatibilidad camelCase
        "seatLimit": seats,
        "usedSeats": used,
    })


# ============ GET /users ====================================================

@bp.get("/users")
# @require_auth(org=True)
def list_users():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))

    items = _list_memberships(org_id)
    rows = []
    for m in items:
        # Clerk puede devolver user_id directamente o dentro de public_user_data
        pud = m.get("public_user_data") or {}
        user_id = m.get("user_id") or pud.get("user_id")
        name = pud.get("first_name", "") + (" " if pud.get("last_name") else "") + (pud.get("last_name", "") or "")
        email = pud.get("email_address") or pud.get("identifier") or ""
        role = ROLE_FROM_CLERK.get(m.get("role", ""), "member")
        rows.append({
            "id": m.get("id"),               # membership_id
            "user_id": user_id,
            "name": name.strip() or None,
            "email": email or None,
            "role": role,
        })

    return jsonify({"data": rows})


# ============ POST /invite ==================================================

@bp.post("/invite")
# @require_auth(org_admin=True)
def invite_users():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))

    payload = request.get_json(silent=True) or {}
    emails = payload.get("emails")
    role = (payload.get("role") or "member").strip().lower()
    redirect_url = payload.get("redirect_url") or payload.get("redirectUrl")
    expires_in_days = int(payload.get("expires_in_days") or payload.get("expiresInDays") or 7)
    allow_overbook = bool(payload.get("allow_overbook"))

    if isinstance(emails, str):
        emails = [emails]
    emails = list(filter(None, [e.strip() for e in (emails or [])]))
    if not emails:
        return _json_error(400, "emails requerido")

    # Capacidad
    seats = _get_seat_limit(org_id)
    used, pending = _count_used_and_pending(org_id)
    if not allow_overbook and (used + pending + len(emails) > seats):
        return _json_error(409, "seat_limit_exceeded", results=[{"email": e, "ok": False, "error": "seat_limit_exceeded"} for e in emails])

    # Detección previa de duplicados por email canónico
    existing = _list_pending_invitations(org_id)
    existing_map = { _canonical_email(i.get("email_address","")): i for i in existing }

    results = []
    any_error = False
    for raw in emails:
        canon = _canonical_email(raw)
        if canon in existing_map:
            results.append({"email": raw, "ok": False, "error": "invitation_exists_pending"})
            any_error = True
            continue

        body = {
            "email_address": raw,
            "role": ROLE_TO_CLERK.get(role, "basic_member"),
        }
        if redirect_url:
            body["redirect_url"] = redirect_url
        # Clerk soporta expires_in_days para org invitations
        if expires_in_days:
            body["expires_in_days"] = max(1, int(expires_in_days))

        r = _clerk("POST", f"/organizations/{org_id}/invitations", json=body)
        if r.status_code in (200, 201):
            inv = r.json()
            results.append({"email": raw, "ok": True, "invitation_id": inv.get("id"), "status": inv.get("status")})
        elif r.status_code == 409:
            # Clerk ya detectó duplicada
            results.append({"email": raw, "ok": False, "error": "invitation_exists_pending"})
            any_error = True
        else:
            any_error = True
            try:
                msg = r.json()
            except Exception:
                msg = {"error": r.text}
            results.append({"email": raw, "ok": False, "error": "clerk_error", "detail": msg})

    status = 409 if any_error else 200
    return jsonify({"results": results}), status


# ============ POST /update-role ============================================

@bp.post("/update-role")
# @require_auth(org_admin=True)
def update_role():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))

    payload = request.get_json(silent=True) or {}
    membership_id = payload.get("membership_id")
    user_id = payload.get("user_id")
    role = (payload.get("role") or "member").strip().lower()

    if not membership_id and not user_id:
        return _json_error(400, "membership_id o user_id requerido")

    if role not in ("admin", "member"):
        return _json_error(400, "role inválido (usa 'admin'|'member')")

    # Resolver membership_id por user_id si hace falta
    if not membership_id:
        memberships = _list_memberships(org_id)
        mm = next((m for m in memberships if (m.get("user_id") == user_id or m.get("public_user_data", {}).get("user_id") == user_id)), None)
        if not mm:
            return _json_error(404, "membership no encontrada para ese user_id")
        membership_id = mm["id"]

    # Evitar dejar la org sin admin
    if role == "member" and _is_last_admin(org_id, membership_id, None):
        return _json_error(400, "no puedes degradar al último admin")

    body = {"role": ROLE_TO_CLERK.get(role, "basic_member")}
    r = _clerk("PATCH", f"/organizations/{org_id}/memberships/{membership_id}", json=body)
    if r.ok:
        return jsonify({"ok": True})
    return _json_error(r.status_code, "clerk_error", detail=r.json() if r.headers.get("content-type","").startswith("application/json") else r.text)


# ============ POST /remove ==================================================

@bp.post("/remove")
# @require_auth(org_admin=True)
def remove_member():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))

    payload = request.get_json(silent=True) or {}
    membership_id = payload.get("membership_id")
    user_id = payload.get("user_id")

    if not membership_id and not user_id:
        return _json_error(400, "membership_id o user_id requerido")

    # Resolver membership_id si llega user_id
    if not membership_id:
        memberships = _list_memberships(org_id)
        mm = next((m for m in memberships if (m.get("user_id") == user_id or m.get("public_user_data", {}).get("user_id") == user_id)), None)
        if not mm:
            return _json_error(404, "membership no encontrada para ese user_id")
        membership_id = mm["id"]

    if _is_last_admin(org_id, membership_id, None):
        return _json_error(400, "no puedes eliminar al último admin")

    r = _clerk("DELETE", f"/organizations/{org_id}/memberships/{membership_id}")
    if r.status_code in (200, 204):
        return jsonify({"ok": True})
    try:
        detail = r.json()
    except Exception:
        detail = r.text
    return _json_error(r.status_code, "clerk_error", detail=detail)


# ============ POST /set-seat-limit =========================================

@bp.post("/set-seat-limit")
# @require_auth(org_admin=True)
def set_seat_limit():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))

    payload = request.get_json(silent=True) or {}
    seats = int(payload.get("seats") or 0)
    if seats < 0:
        return _json_error(400, "seats debe ser >= 0")

    used, pending = _count_used_and_pending(org_id)
    if seats < (used + pending):
        return _json_error(400, f"seats no puede ser < used+pending ({used}+{pending})")

    _set_seat_limit(org_id, seats)
    return jsonify({"ok": True, "seats": seats})


# ============ (Opcional) Invites: listar / revocar ==========================

@bp.get("/invitations")
# @require_auth(org_admin=True)
def list_invitations():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))
    items = _list_pending_invitations(org_id)
    out = [{
        "id": i.get("id"),
        "email": i.get("email_address"),
        "status": i.get("status"),
        "created_at": i.get("created_at"),
    } for i in items]
    return jsonify({"items": out})

@bp.post("/revoke-invite")
# @require_auth(org_admin=True)
def revoke_invite():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))

    payload = request.get_json(silent=True) or {}
    invitation_id = payload.get("invitation_id")
    email = (payload.get("email") or "").strip().lower()

    if not invitation_id and not email:
        return _json_error(400, "email o invitation_id requerido")

    if not invitation_id:
        items = _list_pending_invitations(org_id)
        for inv in items:
            if (inv.get("email_address") or "").strip().lower() == email:
                invitation_id = inv.get("id")
                break

    if not invitation_id:
        return _json_error(404, "invitation no encontrada")

    r = _clerk("POST", f"/organizations/{org_id}/invitations/{invitation_id}/revoke")
    if r.ok:
        return jsonify({"ok": True})
    try:
        detail = r.json()
    except Exception:
        detail = r.text
    return _json_error(r.status_code, "clerk_error", detail=detail)
