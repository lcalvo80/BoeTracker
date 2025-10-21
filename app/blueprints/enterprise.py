from __future__ import annotations
import os
import re
from typing import Dict, Any, List, Tuple
import requests
from flask import Blueprint, request, jsonify, g, current_app

bp = Blueprint("enterprise", __name__, url_prefix="/api/enterprise")

CLERK_API = "https://api.clerk.com/v1"
CLERK_SECRET = os.environ.get("CLERK_SECRET_KEY", "")

# owner -> admin en lectura
ROLE_TO_CLERK = {"admin": "admin", "member": "basic_member"}
ROLE_FROM_CLERK = {"admin": "admin", "owner": "admin", "basic_member": "member"}

_PLACEHOLDER_RE = re.compile(r"^\s*\{\{.*\}\}\s*$", re.I)


def _is_placeholder(v: str) -> bool:
    if not isinstance(v, str):
        return False
    s = v.strip().lower()
    return bool(_PLACEHOLDER_RE.match(s)) or s in {
        "organization.id",
        "organization_membership.role",
        "organization.slug",
        "user.id",
        "user.email_address",
    }


def _valid_org_id(v: str) -> bool:
    return isinstance(v, str) and v.startswith("org_") and not _is_placeholder(v)


def _auth_headers() -> Dict[str, str]:
    if not CLERK_SECRET:
        raise RuntimeError("CLERK_SECRET_KEY no configurado")
    return {"Authorization": f"Bearer {CLERK_SECRET}"}


def _clerk(method: str, path: str, *, params=None, json=None) -> requests.Response:
    url = f"{CLERK_API}{path}"
    return requests.request(
        method, url, headers=_auth_headers(), params=params, json=json, timeout=20
    )


def _json_error(code: int, message: str, **extra):
    return jsonify({"message": message, **extra}), code


def _org_id_from_context() -> str:
    hdr = request.headers.get("X-Org-Id")
    if _valid_org_id(hdr):
        return hdr
    claim = getattr(g, "org_id", None)
    if _valid_org_id(claim):
        return claim
    raise ValueError(
        "No hay un org_id válido. Incluye cabecera X-Org-Id o corrige el JWT template."
    )


def _current_user_id() -> str | None:
    # Poblado por tu middleware de Clerk
    if hasattr(g, "user_id") and g.user_id:
        return g.user_id
    c = getattr(g, "clerk", {}) or {}
    return c.get("user_id")


def _current_user_role_from_membership(org_id: str) -> str:
    """
    Rol real preguntando a Clerk, ignorando los claims del JWT (que pueden traer placeholders).
    """
    uid = _current_user_id()
    if not uid:
        return "member"
    ok, memberships, _, _ = _fetch_memberships(org_id)
    if not ok:
        return "member"
    mm = next(
        (
            m
            for m in memberships
            if (m.get("user_id") == uid or m.get("public_user_data", {}).get("user_id") == uid)
        ),
        None,
    )
    if not mm:
        return "member"
    return "admin" if mm.get("role") in ("admin", "owner") else "member"


def _canonical_email(email: str) -> str:
    e = (email or "").strip().lower()
    if "@" not in e:
        return e
    local, domain = e.split("@", 1)
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
    return f"{local}@{domain}"


def _get_seat_limit(org_id: str) -> int:
    """
    1) cache en memoria
    2) Clerk.organization.public_metadata.seats (persistente)
    """
    store = getattr(current_app, "_SEAT_LIMITS", {})
    cached = int(store.get(org_id, 0))
    if cached > 0:
        return cached

    # 2) pedir a Clerk la org y leer public_metadata.seats
    try:
        r = _clerk("GET", f"/organizations/{org_id}")
        if r.ok:
            j = r.json()
            seats = int(((j.get("public_metadata") or {}).get("seats") or 0))
            if seats > 0:
                store[org_id] = seats
                current_app._SEAT_LIMITS = store
                return seats
    except Exception:
        pass

    return 0


def _set_seat_limit(org_id: str, seats: int) -> None:
    """
    Actualiza cache en memoria y PERSISTE en Clerk (public_metadata.seats).
    """
    seats = int(max(0, seats))
    store = getattr(current_app, "_SEAT_LIMITS", {})
    store[org_id] = seats
    current_app._SEAT_LIMITS = store

    # Persistir en Clerk
    try:
        _ = _clerk(
            "PATCH",
            f"/organizations/{org_id}",
            json={"public_metadata": {"seats": seats}},
        )
        # No necesitamos .ok duro; si falla, al menos la cache está actualizada
    except Exception:
        current_app.logger.warning("No se pudo persistir seats en Clerk")


def _fetch_memberships(org_id: str) -> Tuple[bool, List[Dict[str, Any]], int, Any]:
    r = _clerk("GET", f"/organizations/{org_id}/memberships", params={"limit": 200})
    if not r.ok:
        detail = (
            r.json()
            if r.headers.get("content-type", "").startswith("application/json")
            else r.text
        )
        return False, [], r.status_code, detail
    data = r.json()
    items = data["data"] if isinstance(data, dict) and "data" in data else data
    return True, items, 200, None


def _fetch_pending_invitations(
    org_id: str,
) -> Tuple[bool, List[Dict[str, Any]], int, Any]:
    r = _clerk(
        "GET",
        f"/organizations/{org_id}/invitations",
        params={"status": "pending", "limit": 200},
    )
    if not r.ok:
        detail = (
            r.json()
            if r.headers.get("content-type", "").startswith("application/json")
            else r.text
        )
        return False, [], r.status_code, detail
    data = r.json()
    items = data["data"] if isinstance(data, dict) and "data" in data else data
    return True, items, 200, None


def _count_used_and_pending(org_id: str) -> Tuple[int, int] | Tuple[None, None]:
    ok_u, memberships, _, _ = _fetch_memberships(org_id)
    ok_i, invites, _, _ = _fetch_pending_invitations(org_id)
    if not ok_u or not ok_i:
        return None, None
    return len(memberships), len(invites)


def _is_last_admin(
    org_id: str, membership_id: str | None, user_id: str | None
) -> Tuple[bool, Tuple[int, int]]:
    ok, memberships, _, _ = _fetch_memberships(org_id)
    if not ok:
        return False, (0, 0)
    admins = [m for m in memberships if m.get("role") in ("admin", "owner")]
    if len(admins) <= 1:
        target = None
        if membership_id:
            target = next((m for m in memberships if m.get("id") == membership_id), None)
        elif user_id:
            target = next(
                (
                    m
                    for m in memberships
                    if (
                        m.get("user_id") == user_id
                        or m.get("public_user_data", {}).get("user_id") == user_id
                    )
                ),
                None,
            )
        if target and target.get("role") in ("admin", "owner"):
            return True, (len(memberships), len(admins))
    return False, (len(memberships), len(admins))


@bp.get("/org")
def get_org():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))

    org_resp = _clerk("GET", f"/organizations/{org_id}")
    org_name = None
    if org_resp.ok:
        try:
            org_name = org_resp.json().get("name")
        except Exception:
            org_name = None

    ok_u, memberships, code_u, detail_u = _fetch_memberships(org_id)
    if not ok_u:
        return _json_error(code_u, "clerk_error_memberships", detail=detail_u)

    ok_i, invites, code_i, detail_i = _fetch_pending_invitations(org_id)
    if not ok_i:
        return _json_error(code_i, "clerk_error_invitations", detail=detail_i)

    used = len(memberships)
    pending = len(invites)
    seats = _get_seat_limit(org_id)

    return jsonify(
        {
            "id": org_id,
            "name": org_name,
            "seats": seats,
            "used_seats": used,
            "pending_invites": pending,
            "current_user_role": _current_user_role_from_membership(org_id),
            # alias legacy
            "seatLimit": seats,
            "usedSeats": used,
        }
    )


@bp.get("/users")
def list_users():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))

    ok, items, code, detail = _fetch_memberships(org_id)
    if not ok:
        return _json_error(code, "clerk_error_memberships", detail=detail)

    rows = []
    for m in items:
        pud = m.get("public_user_data") or {}
        user_id = m.get("user_id") or pud.get("user_id")
        first = (pud.get("first_name") or "").strip()
        last = (pud.get("last_name") or "").strip()
        name = " ".join(filter(None, [first, last])) or None
        email = pud.get("email_address") or pud.get("identifier") or None
        role = ROLE_FROM_CLERK.get(m.get("role", ""), "member")
        rows.append(
            {
                "id": m.get("id"),
                "user_id": user_id,
                "name": name,
                "email": email,
                "role": role,
            }
        )
    return jsonify({"data": rows})


@bp.post("/invite")
def invite_users():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))

    payload = request.get_json(silent=True) or {}
    emails = payload.get("emails")
    role = (payload.get("role") or "member").strip().lower()
    redirect_url = payload.get("redirect_url") or payload.get("redirectUrl")
    expires_in_days = int(
        payload.get("expires_in_days") or payload.get("expiresInDays") or 7
    )
    allow_overbook = bool(payload.get("allow_overbook"))

    if isinstance(emails, str):
        emails = [emails]
    emails = list(filter(None, [e.strip() for e in (emails or [])]))
    if not emails:
        return _json_error(400, "emails requerido")

    seats = _get_seat_limit(org_id)
    ok_u, memberships, code_u, detail_u = _fetch_memberships(org_id)
    if not ok_u:
        return _json_error(code_u, "clerk_error_memberships", detail=detail_u)
    ok_i, invites, code_i, detail_i = _fetch_pending_invitations(org_id)
    if not ok_i:
        return _json_error(code_i, "clerk_error_invitations", detail=detail_i)

    used = len(memberships)
    pending = len(invites)
    if not allow_overbook and (used + pending + len(emails) > seats):
        return _json_error(
            409,
            "seat_limit_exceeded",
            results=[
                {"email": e, "ok": False, "error": "seat_limit_exceeded"} for e in emails
            ],
        )

    existing_map = {_canonical_email(i.get("email_address", "")): i for i in invites}

    results = []
    any_error = False
    for raw in emails:
        canon = _canonical_email(raw)
        if canon in existing_map:
            results.append(
                {"email": raw, "ok": False, "error": "invitation_exists_pending"}
            )
            any_error = True
            continue

        body = {
            "email_address": raw,
            "role": ROLE_TO_CLERK.get(role, "basic_member"),
        }
        if redirect_url:
            body["redirect_url"] = redirect_url
        if expires_in_days:
            body["expires_in_days"] = max(1, int(expires_in_days))

        r = _clerk("POST", f"/organizations/{org_id}/invitations", json=body)
        if r.status_code in (200, 201):
            inv = r.json()
            results.append(
                {
                    "email": raw,
                    "ok": True,
                    "invitation_id": inv.get("id"),
                    "status": inv.get("status"),
                }
            )
        elif r.status_code == 409:
            results.append(
                {"email": raw, "ok": False, "error": "invitation_exists_pending"}
            )
            any_error = True
        else:
            any_error = True
            detail = (
                r.json()
                if r.headers.get("content-type", "").startswith("application/json")
                else r.text
            )
            results.append(
                {"email": raw, "ok": False, "error": "clerk_error", "detail": detail}
            )

    return jsonify({"results": results}), (409 if any_error else 200)


@bp.post("/update-role")
def update_role():
    """
    Body:
      { "membership_id": "orgmem_...", "role": "admin"|"member" }
      o bien
      { "user_id": "user_...", "role": "admin"|"member" }
    Requiere:
      - Authorization: Bearer <JWT Clerk>
      - X-Org-Id: org_...
    """
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))

    # 🔐 Solo admins pueden modificar roles
    if _current_user_role_from_membership(org_id) != "admin":
        return _json_error(403, "forbidden: admin requerido")

    payload = request.get_json(silent=True) or {}
    membership_id = (payload.get("membership_id") or "").strip()
    user_id = (payload.get("user_id") or "").strip()
    role = (payload.get("role") or "member").strip().lower()

    if role not in ("admin", "member"):
        return _json_error(400, "role inválido (usa 'admin'|'member')")

    ok, memberships, code, detail = _fetch_memberships(org_id)
    if not ok:
        return _json_error(code, "clerk_error_memberships", detail=detail)

    # Resolver membership_id con user_id si hace falta
    if not membership_id and user_id:
        mm = next(
            (m for m in memberships
             if (m.get("user_id") == user_id
                 or m.get("public_user_data", {}).get("user_id") == user_id)),
            None,
        )
        if not mm:
            return _json_error(404, "membership no encontrada para ese user_id")
        membership_id = (mm.get("id") or "").strip()

    if not membership_id:
        return _json_error(400, "membership_id o user_id requerido")

    # Verificar que el membership_id pertenece a esta org
    mm_by_id = next((m for m in memberships if (m.get("id") or "").strip() == membership_id), None)
    if not mm_by_id:
        return _json_error(404, "membership no encontrada para ese membership_id")

    # Evitar dejar la org sin admins al degradar
    if role == "member":
        is_last, _ = _is_last_admin(org_id, membership_id, None)
        if is_last:
            return _json_error(400, "no puedes degradar al último admin")

    # Idempotencia: ya tiene ese rol
    current_role = ROLE_FROM_CLERK.get(mm_by_id.get("role", ""), "member")
    if current_role == role:
        return jsonify({"ok": True, "unchanged": True})

    # ✅ Endpoint correcto en Clerk
    body = {"role": ROLE_TO_CLERK.get(role, "basic_member")}
    r = _clerk("PATCH", f"/organization_memberships/{membership_id}", json=body)

    if r.ok:
        return jsonify({"ok": True})
    detail = (
        r.json()
        if r.headers.get("content-type", "").startswith("application/json")
        else r.text
    )
    return _json_error(r.status_code, "clerk_error", detail=detail)


@bp.post("/remove")
def remove_member():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))

    # 🔐 Solo admins pueden eliminar miembros
    if _current_user_role_from_membership(org_id) != "admin":
        return _json_error(403, "forbidden: admin requerido")

    payload = request.get_json(silent=True) or {}
    membership_id = (payload.get("membership_id") or "").strip()
    user_id = (payload.get("user_id") or "").strip()

    ok, memberships, code, detail = _fetch_memberships(org_id)
    if not ok:
        return _json_error(code, "clerk_error_memberships", detail=detail)

    if not membership_id and user_id:
        mm = next(
            (
                m
                for m in memberships
                if (
                    m.get("user_id") == user_id
                    or m.get("public_user_data", {}).get("user_id") == user_id
                )
            ),
            None,
        )
        if not mm:
            return _json_error(404, "membership no encontrada para ese user_id")
        membership_id = (mm.get("id") or "").strip()

    if not membership_id and not user_id:
        return _json_error(400, "membership_id o user_id requerido")

    # No permitir dejar la org sin admins
    is_last, _ = _is_last_admin(org_id, membership_id, user_id or None)
    if is_last:
        return _json_error(400, "no puedes eliminar al último admin")

    # ✅ Endpoint correcto
    if membership_id:
        r = _clerk("DELETE", f"/organization_memberships/{membership_id}")
    else:
        r = _clerk("DELETE", f"/organizations/{org_id}/memberships/{user_id}")

    if r.status_code in (200, 204):
        return jsonify({"ok": True})
    detail = (
        r.json()
        if r.headers.get("content-type", "").startswith("application/json")
        else r.text
    )
    return _json_error(r.status_code, "clerk_error", detail=detail)


@bp.post("/set-seat-limit")
def set_seat_limit():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))

    payload = request.get_json(silent=True) or {}
    seats = int(payload.get("seats") or 0)
    if seats < 0:
        return _json_error(400, "seats debe ser >= 0")

    ok_u, memberships, code_u, detail_u = _fetch_memberships(org_id)
    if not ok_u:
        return _json_error(code_u, "clerk_error_memberships", detail=detail_u)
    ok_i, invites, code_i, detail_i = _fetch_pending_invitations(org_id)
    if not ok_i:
        return _json_error(code_i, "clerk_error_invitations", detail=detail_i)

    used = len(memberships)
    pending = len(invites)
    if seats < (used + pending):
        return _json_error(
            400, f"seats no puede ser < used+pending ({used}+{pending})"
        )

    _set_seat_limit(org_id, seats)
    return jsonify({"ok": True, "seats": seats})


@bp.get("/invitations")
def list_invitations():
    try:
        org_id = _org_id_from_context()
    except ValueError as e:
        return _json_error(400, str(e))
    ok, items, code, detail = _fetch_pending_invitations(org_id)
    if not ok:
        return _json_error(code, "clerk_error_invitations", detail=detail)
    out = [
        {
            "id": i.get("id"),
            "email": i.get("email_address"),
            "status": i.get("status"),
            "created_at": i.get("created_at"),
        }
        for i in items
    ]
    return jsonify({"items": out})


@bp.post("/revoke-invite")
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
        ok, items, code, detail = _fetch_pending_invitations(org_id)
        if not ok:
            return _json_error(code, "clerk_error_invitations", detail=detail)
        for inv in items:
            if (inv.get("email_address") or "").strip().lower() == email:
                invitation_id = inv.get("id")
                break

    if not invitation_id:
        return _json_error(404, "invitation no encontrada")

    r = _clerk("POST", f"/organizations/{org_id}/invitations/{invitation_id}/revoke")
    if r.ok:
        return jsonify({"ok": True})
    detail = (
        r.json()
        if r.headers.get("content-type", "").startswith("application/json")
        else r.text
    )
    return _json_error(r.status_code, "clerk_error", detail=detail)
