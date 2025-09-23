# app/blueprints/enterprise.py
from __future__ import annotations

import os
from typing import Any, Dict, List

import requests
from flask import Blueprint, jsonify, request, g, current_app

from app.auth import require_clerk_auth
from app.integrations import clerk_admin as clerk

bp = Blueprint("enterprise", __name__, url_prefix="/api/enterprise")

# ────────── helpers ──────────

def _cfg(k: str, default: str | None = None) -> str | None:
    """Config lookup: Flask config first, then ENV."""
    try:
        v = current_app.config.get(k)  # type: ignore[attr-defined]
    except Exception:
        v = None
    if v is None or str(v).strip() == "":
        v = os.getenv(k, default)
    return v

def _is_truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")

def _headers_json() -> Dict[str, str]:
    sk = _cfg("CLERK_SECRET_KEY", "")
    if not sk:
        raise RuntimeError("Missing CLERK_SECRET_KEY")
    return {
        "Authorization": f"Bearer {sk}",
        "Content-Type": "application/json",
    }

def _base() -> str:
    return "https://api.clerk.com/v1"

def _map_role_out(role: str) -> str:
    """Role -> UI. Clerk usa 'admin' | 'basic_member'."""
    r = (role or "").strip().lower()
    if r in ("basic_member", "member"):
        return "member"
    if r == "admin":
        return "admin"
    # fallback
    return r or "member"

def _map_role_in(role: str) -> str:
    """UI -> Clerk API."""
    r = (role or "").strip().lower()
    return "admin" if r == "admin" else "basic_member"

def _require_enterprise_admin() -> tuple[bool, tuple]:
    """
    Verifica que el usuario actual (g.clerk.user_id) tenga:
      - org_id presente
      - public_metadata.subscription == 'enterprise'
      - public_metadata.role in {'admin', 'owner'}
      - (extra recomendado) membership.role == 'admin' dentro de esa org
    """
    c = getattr(g, "clerk", {}) or {}
    user_id = c.get("user_id")
    org_id = c.get("org_id")
    if not user_id:
        return False, (jsonify(error="missing user in context"), 401)
    if not org_id:
        return False, (jsonify(error="organization required"), 403)

    # Validación por metadata del usuario
    try:
        user = clerk.get_user(user_id)  # requiere CLERK_SECRET_KEY
    except Exception as e:
        current_app.logger.exception("[enterprise] get_user failed: %s", e)
        return False, (jsonify(error="clerk user fetch failed"), 502)

    pm = (user.get("public_metadata") or {}) if isinstance(user, dict) else {}
    subscription = (pm.get("plan") or pm.get("subscription") or "free")
    role = (pm.get("role") or "").lower()
    if subscription != "enterprise" or role not in ("admin", "owner"):
        return False, (jsonify(error="forbidden: enterprise admin required"), 403)

    # Validación por membership en la organización (recomendada)
    try:
        url = f"{_base()}/organizations/{org_id}/memberships?limit=1&user_id={user_id}"
        res = requests.get(url, headers=_headers_json(), timeout=10)
        res.raise_for_status()
        arr = res.json()
        arr = arr if isinstance(arr, list) else arr.get("data") or []
        if arr:
            mrole = (arr[0].get("role") or "").lower()
            if mrole != "admin":
                return False, (jsonify(error="forbidden: organization admin required"), 403)
    except Exception as e:
        current_app.logger.warning("[enterprise] membership role check skipped: %s", e)

    return True, ()

# ────────── endpoints ──────────

@bp.get("/users")
@require_clerk_auth
def list_users():
    ok, err = _require_enterprise_admin()
    if not ok:
        return err

    org_id = g.clerk["org_id"]
    # 1) listar memberships
    url = f"{_base()}/organizations/{org_id}/memberships?limit=200"
    try:
        res = requests.get(url, headers=_headers_json(), timeout=10)
        res.raise_for_status()
    except Exception as e:
        current_app.logger.exception("[enterprise] list memberships failed: %s", e)
        return jsonify(error="clerk memberships fetch failed"), 502

    data = res.json()
    memberships = data if isinstance(data, list) else data.get("data") or data  # tolerante
    out: List[Dict[str, Any]] = []

    # 2) enriquecer con nombre/email
    for m in memberships:
        try:
            membership_id = m.get("id")
            user_id = (m.get("public_user_data") or {}).get("user_id") or m.get("user_id")
            role = _map_role_out(m.get("role", "member"))
            name = None
            email = None

            pud = m.get("public_user_data") or {}
            if pud:
                first = pud.get("first_name") or ""
                last = pud.get("last_name") or ""
                name = (first + " " + last).strip() or None
                email = pud.get("identifier") or None

            if not email or not name:
                # fallback: /users/{id}
                if user_id:
                    try:
                        u = clerk.get_user(user_id)
                        if not name:
                            name = ((u.get("first_name") or "") + " " + (u.get("last_name") or "")).strip() or None
                        if not email:
                            emails = u.get("email_addresses") or []
                            if emails:
                                email = emails[0].get("email_address")
                    except Exception:
                        pass

            out.append({
                "id": membership_id,     # usamos membership_id para editar/eliminar
                "user_id": user_id,
                "name": name or "—",
                "email": email or "",
                "role": role,
            })
        except Exception:
            continue

    return jsonify(out), 200


@bp.post("/users/invite")
@require_clerk_auth
def invite_user():
    ok, err = _require_enterprise_admin()
    if not ok:
        return err

    org_id = g.clerk["org_id"]
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    role_ui = (payload.get("role") or "member")
    role = _map_role_in(role_ui)

    if not email:
        return jsonify(error="email requerido"), 400

    url = f"{_base()}/organizations/{org_id}/invitations"
    body = {"email_address": email, "role": role}

    try:
        res = requests.post(url, headers=_headers_json(), json=body, timeout=10)
        res.raise_for_status()
    except requests.HTTPError as he:
        try:
            return jsonify(res.json()), res.status_code
        except Exception:
            return jsonify(error="clerk invite failed"), res.status_code
    except Exception as e:
        current_app.logger.exception("[enterprise] invite failed: %s", e)
        return jsonify(error="clerk invite failed"), 502

    return jsonify({"ok": True}), 201


@bp.patch("/users/<membership_id>")
@require_clerk_auth
def update_role(membership_id: str):
    ok, err = _require_enterprise_admin()
    if not ok:
        return err

    org_id = g.clerk["org_id"]
    payload = request.get_json(silent=True) or {}
    role_ui = (payload.get("role") or "").strip().lower()
    if role_ui not in ("member", "admin"):
        return jsonify(error="role inválido (member|admin)"), 400
    role = _map_role_in(role_ui)

    url = f"{_base()}/organizations/{org_id}/memberships/{membership_id}"
    body = {"role": role}
    try:
        res = requests.patch(url, headers=_headers_json(), json=body, timeout=10)
        res.raise_for_status()
    except requests.HTTPError:
        try:
            return jsonify(res.json()), res.status_code
        except Exception:
            return jsonify(error="clerk membership update failed"), res.status_code
    except Exception as e:
        current_app.logger.exception("[enterprise] update role failed: %s", e)
        return jsonify(error="clerk membership update failed"), 502

    return jsonify({"ok": True}), 200


@bp.delete("/users/<membership_id>")
@require_clerk_auth
def remove_user(membership_id: str):
    ok, err = _require_enterprise_admin()
    if not ok:
        return err

    org_id = g.clerk["org_id"]
    url = f"{_base()}/organizations/{org_id}/memberships/{membership_id}"
    try:
        res = requests.delete(url, headers=_headers_json(), timeout=10)
        res.raise_for_status()
    except requests.HTTPError:
        try:
            return jsonify(res.json()), res.status_code
        except Exception:
            return jsonify(error="clerk membership delete failed"), res.status_code
    except Exception as e:
        current_app.logger.exception("[enterprise] delete membership failed: %s", e)
        return jsonify(error="clerk membership delete failed"), 502

    return jsonify({"ok": True}), 200
