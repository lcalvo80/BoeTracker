# app/blueprints/enterprise.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests
from flask import Blueprint, jsonify, request, g, current_app

from app.auth import require_clerk_auth

bp = Blueprint("enterprise", __name__, url_prefix="/api/enterprise")

# ───────── helpers ─────────
def _cfg(k: str, default: Optional[str] = None) -> str:
    try:
        v = current_app.config.get(k)  # type: ignore[attr-defined]
    except Exception:
        v = None
    if v is None or str(v).strip() == "":
        v = os.getenv(k, default or "")
    return str(v or "")

def _headers_json() -> Dict[str, str]:
    sk = _cfg("CLERK_SECRET_KEY", "")
    if not sk:
        raise RuntimeError("Missing CLERK_SECRET_KEY")
    return {"Authorization": f"Bearer {sk}", "Content-Type": "application/json"}

def _base() -> str:
    return "https://api.clerk.com/v1"

def _map_role_out(role: str) -> str:
    r = (role or "").strip().lower()
    if r in ("basic_member", "member"):
        return "member"
    if r == "admin":
        return "admin"
    return "member"

def _map_role_in(role: str) -> str:
    r = (role or "").strip().lower()
    return "admin" if r == "admin" else "basic_member"

def _is_enterprise_admin(user_id: str, org_id: str) -> bool:
    """Comprueba membership en Clerk: el usuario debe ser admin de la organización."""
    try:
        url = f"{_base()}/organizations/{org_id}/memberships?limit=1&user_id={user_id}"
        res = requests.get(url, headers=_headers_json(), timeout=10)
        res.raise_for_status()
        data = res.json()
        arr = data if isinstance(data, list) else data.get("data") or []
        if not arr:
            return False
        role = (arr[0].get("role") or "").lower()
        return role == "admin"
    except Exception as e:
        current_app.logger.warning(f"[enterprise] membership check skipped: {e}")
        return False

def _current_user_ids() -> tuple[str, Optional[str]]:
    c = getattr(g, "clerk", {}) or {}
    return c.get("user_id"), c.get("org_id")

# ───────── endpoints ─────────

@bp.get("/org")
@require_clerk_auth
def org_info():
    user_id, org_id = _current_user_ids()
    if not org_id:
        return jsonify({"id": None, "name": None, "seats": 0, "used_seats": 0, "current_user_role": None})

    # Carga de organización
    try:
        org = requests.get(f"{_base()}/organizations/{org_id}", headers=_headers_json(), timeout=10).json()
    except Exception as e:
        current_app.logger.exception("[enterprise] fetch org failed: %s", e)
        return jsonify(error="clerk org fetch failed"), 502

    # seats en public_metadata
    seats = 0
    try:
        seats = int(((org.get("public_metadata") or {}).get("seats") or 0))
    except Exception:
        seats = 0

    # used_seats = miembros con estado activo
    try:
        members = requests.get(f"{_base()}/organizations/{org_id}/memberships?limit=200", headers=_headers_json(), timeout=10).json()
        members = members if isinstance(members, list) else members.get("data") or []
    except Exception:
        members = []
    used = len(members)

    # rol actual
    cur_role = "member"
    try:
        m = requests.get(f"{_base()}/organizations/{org_id}/memberships?limit=1&user_id={user_id}", headers=_headers_json(), timeout=10).json()
        arr = m if isinstance(m, list) else m.get("data") or []
        if arr:
            cur_role = _map_role_out(arr[0].get("role"))
    except Exception:
        pass

    return jsonify({
        "id": org.get("id"),
        "name": org.get("name"),
        "seats": seats,
        "used_seats": used,
        "current_user_role": cur_role,
    }), 200


@bp.get("/users")
@require_clerk_auth
def list_users():
    user_id, org_id = _current_user_ids()
    if not org_id:
        return jsonify({"data": []})
    # No restringimos a admin para que los miembros vean el roster (si quieres, habilita check aquí)
    try:
        res = requests.get(f"{_base()}/organizations/{org_id}/memberships?limit=200", headers=_headers_json(), timeout=10)
        res.raise_for_status()
        memberships = res.json()
        memberships = memberships if isinstance(memberships, list) else memberships.get("data") or memberships
    except Exception as e:
        current_app.logger.exception("[enterprise] list memberships failed: %s", e)
        return jsonify(error="clerk memberships fetch failed"), 502

    out: List[Dict[str, Any]] = []
    for m in memberships:
        try:
            membership_id = m.get("id")
            uid = (m.get("public_user_data") or {}).get("user_id") or m.get("user_id")
            role = _map_role_out(m.get("role", "member"))
            pud = m.get("public_user_data") or {}
            first = pud.get("first_name") or ""
            last = pud.get("last_name") or ""
            name = (first + " " + last).strip() or None
            email = pud.get("identifier") or ""
            out.append({
                "id": membership_id,   # id de membership (lo usamos en updates)
                "user_id": uid,
                "name": name or "—",
                "email": email,
                "role": role,
                "licensed": True,      # si gestionas seats estrictos: aquí puedes calcular true/false
            })
        except Exception:
            continue

    return jsonify({"data": out}), 200


@bp.post("/invite")
@require_clerk_auth
def invite_user():
    user_id, org_id = _current_user_ids()
    if not org_id:
        return jsonify(error="organization required"), 403
    if not _is_enterprise_admin(user_id, org_id):
        return jsonify(error="forbidden: organization admin required"), 403

    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    role_ui = (payload.get("role") or "member")
    if not email:
        return jsonify(error="email requerido"), 400

    role = _map_role_in(role_ui)
    try:
        res = requests.post(
            f"{_base()}/organizations/{org_id}/invitations",
            headers=_headers_json(),
            json={"email_address": email, "role": role},
            timeout=10,
        )
        res.raise_for_status()
        return jsonify({"ok": True}), 201
    except requests.HTTPError:
        try:
            return jsonify(res.json()), res.status_code
        except Exception:
            return jsonify(error="clerk invite failed"), res.status_code
    except Exception as e:
        current_app.logger.exception("[enterprise] invite failed: %s", e)
        return jsonify(error="clerk invite failed"), 502


@bp.post("/remove")
@require_clerk_auth
def remove_user():
    user_id, org_id = _current_user_ids()
    if not org_id:
        return jsonify(error="organization required"), 403
    if not _is_enterprise_admin(user_id, org_id):
        return jsonify(error="forbidden: organization admin required"), 403

    payload = request.get_json(silent=True) or {}
    membership_id = payload.get("membership_id")
    # Permitimos también remove por user_id (buscando su membership)
    target_user_id = payload.get("user_id")

    try:
        if not membership_id and target_user_id:
            q = requests.get(
                f"{_base()}/organizations/{org_id}/memberships?limit=1&user_id={target_user_id}",
                headers=_headers_json(),
                timeout=10,
            ).json()
            arr = q if isinstance(q, list) else q.get("data") or []
            if arr:
                membership_id = arr[0].get("id")
        if not membership_id:
            return jsonify(error="membership_id o user_id requerido"), 400

        res = requests.delete(
            f"{_base()}/organizations/{org_id}/memberships/{membership_id}",
            headers=_headers_json(),
            timeout=10,
        )
        res.raise_for_status()
        return jsonify({"ok": True}), 200
    except requests.HTTPError:
        try:
            return jsonify(res.json()), res.status_code
        except Exception:
            return jsonify(error="clerk membership delete failed"), res.status_code
    except Exception as e:
        current_app.logger.exception("[enterprise] delete membership failed: %s", e)
        return jsonify(error="clerk membership delete failed"), 502


@bp.post("/update-role")
@require_clerk_auth
def update_role():
    user_id, org_id = _current_user_ids()
    if not org_id:
        return jsonify(error="organization required"), 403
    if not _is_enterprise_admin(user_id, org_id):
        return jsonify(error="forbidden: organization admin required"), 403

    payload = request.get_json(silent=True) or {}
    membership_id = payload.get("membership_id")
    target_user_id = payload.get("user_id")
    role_ui = (payload.get("role") or "").strip().lower()
    if role_ui not in ("member", "admin"):
        return jsonify(error="role inválido (member|admin)"), 400

    try:
        if not membership_id and target_user_id:
            q = requests.get(
                f"{_base()}/organizations/{org_id}/memberships?limit=1&user_id={target_user_id}",
                headers=_headers_json(),
                timeout=10,
            ).json()
            arr = q if isinstance(q, list) else q.get("data") or []
            if arr:
                membership_id = arr[0].get("id")
        if not membership_id:
            return jsonify(error="membership_id o user_id requerido"), 400

        res = requests.patch(
            f"{_base()}/organizations/{org_id}/memberships/{membership_id}",
            headers=_headers_json(),
            json={"role": _map_role_in(role_ui)},
            timeout=10,
        )
        res.raise_for_status()
        return jsonify({"ok": True}), 200
    except requests.HTTPError:
        try:
            return jsonify(res.json()), res.status_code
        except Exception:
            return jsonify(error="clerk membership update failed"), res.status_code
    except Exception as e:
        current_app.logger.exception("[enterprise] update role failed: %s", e)
        return jsonify(error="clerk membership update failed"), 502


@bp.post("/set-seat-limit")
@require_clerk_auth
def set_seats():
    user_id, org_id = _current_user_ids()
    if not org_id:
        return jsonify(error="organization required"), 403
    if not _is_enterprise_admin(user_id, org_id):
        return jsonify(error="forbidden: organization admin required"), 403

    payload = request.get_json(silent=True) or {}
    try:
        seats = int(payload.get("seats", 0))
    except Exception:
        return jsonify(error="seats inválido"), 400
    if seats < 0:
        return jsonify(error="seats inválido"), 400

    try:
        # merge con public_metadata existente
        org = requests.get(f"{_base()}/organizations/{org_id}", headers=_headers_json(), timeout=10).json()
        public_md = org.get("public_metadata") or {}
        public_md["seats"] = seats
        res = requests.patch(
            f"{_base()}/organizations/{org_id}",
            headers=_headers_json(),
            json={"public_metadata": public_md},
            timeout=10,
        )
        res.raise_for_status()
        return jsonify({"ok": True}), 200
    except requests.HTTPError:
        try:
            return jsonify(res.json()), res.status_code
        except Exception:
            return jsonify(error="clerk org update failed"), res.status_code
    except Exception as e:
        current_app.logger.exception("[enterprise] set seats failed: %s", e)
        return jsonify(error="clerk org update failed"), 502
