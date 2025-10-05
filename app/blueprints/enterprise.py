# app/blueprints/enterprise.py
from __future__ import annotations
import os, requests
from typing import Any, Dict, List, Optional
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
    if r in ("admin", "owner"):
        return "admin"
    return "member"

def _map_role_in(role: str) -> str:
    return "admin" if (role or "").strip().lower() == "admin" else "basic_member"

def _current_user_ids() -> tuple[str, Optional[str]]:
    c = getattr(g, "clerk", {}) or {}
    # override opcional (útil para Postman y casos sin org activa todavía)
    org_from_req = request.headers.get("X-Org-Id") or request.args.get("org_id")
    org_id = org_from_req or c.get("org_id")
    return c.get("user_id"), org_id

def _is_enterprise_admin(user_id: str, org_id: str) -> bool:
    try:
        url = f"{_base()}/organizations/{org_id}/memberships?limit=1&user_id={user_id}"
        res = requests.get(url, headers=_headers_json(), timeout=10)
        res.raise_for_status()
        data = res.json()
        arr = data if isinstance(data, list) else data.get("data") or []
        role = (arr[0].get("role") or "").lower() if arr else ""
        return role in ("admin", "owner")
    except Exception as e:
        current_app.logger.warning(f"[enterprise] membership check skipped: {e}")
        return False

def _get_org(org_id: str) -> dict:
    r = requests.get(f"{_base()}/organizations/{org_id}", headers=_headers_json(), timeout=10)
    r.raise_for_status()
    return r.json()

def _list_memberships(org_id: str) -> List[dict]:
    r = requests.get(f"{_base()}/organizations/{org_id}/memberships?limit=200", headers=_headers_json(), timeout=10)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("data") or []

def _list_invitations(org_id: str) -> List[dict]:
    try:
        r = requests.get(f"{_base()}/organizations/{org_id}/invitations?limit=200", headers=_headers_json(), timeout=10)
        if r.status_code not in (200, 201):
            return []
        data = r.json()
        return data if isinstance(data, list) else data.get("data") or []
    except Exception:
        return []

# ───────── endpoints ─────────

# Alias doble por compatibilidad (/org/create y /create-org)
@bp.post("/org/create")
@bp.post("/create-org")
@require_clerk_auth
def create_org():
    """
    Crea la organización en Clerk y asigna al usuario actual como owner/admin.
    Body: { name: string, seats?: number }
    """
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify(error="name requerido"), 400
    try:
        seats = int(body.get("seats") or 0)
    except Exception:
        seats = 0

    try:
        user_id, _ = _current_user_ids()

        # 1) Crear org con created_by => el caller queda como owner automáticamente
        r = requests.post(
            f"{_base()}/organizations",
            headers=_headers_json(),
            json={"name": name, "created_by": user_id},
            timeout=10,
        )
        r.raise_for_status()
        org = r.json()
        org_id = org.get("id")

        # 2) Metadata inicial (plan draft y seats tentativos)
        public_md = {"plan": "enterprise_draft"}
        if seats > 0:
            public_md["seats"] = seats
        try:
            requests.patch(
                f"{_base()}/organizations/{org_id}",
                headers=_headers_json(),
                json={"public_metadata": public_md},
                timeout=10,
            )
        except Exception:
            pass

        return jsonify({"id": org_id, "name": org.get("name"), "seats": public_md.get("seats", 0)}), 201

    except requests.HTTPError as e:
        # devolvemos el cuerpo real del fallo de Clerk si existe
        try:
            return jsonify(error="clerk error", detail=e.response.json()), e.response.status_code  # type: ignore[attr-defined]
        except Exception:
            return jsonify(error="clerk error", detail=str(e)), 502
    except Exception as e:
        current_app.logger.exception("[enterprise] create_org failed: %s", e)
        return jsonify(error="create_org failed", detail=str(e)), 500


@bp.get("/org")
@require_clerk_auth
def org_info():
    user_id, org_id = _current_user_ids()
    if not org_id:
        return jsonify({"id": None, "name": None, "seats": 0, "used_seats": 0, "pending_invites": 0, "current_user_role": None})

    try:
        org = _get_org(org_id)
        members = _list_memberships(org_id)
        invites = _list_invitations(org_id)
    except Exception as e:
        current_app.logger.exception("[enterprise] fetch org/members failed: %s", e)
        return jsonify(error="clerk org fetch failed"), 502

    seats = 0
    try:
        seats = int(((org.get("public_metadata") or {}).get("seats") or 0))
    except Exception:
        seats = 0

    cur_role = "member"
    try:
        m = [x for x in members if (x.get("public_user_data") or {}).get("user_id") == user_id or x.get("user_id") == user_id]
        if m:
            cur_role = _map_role_out(m[0].get("role"))
    except Exception:
        pass

    return jsonify({
        "id": org.get("id"),
        "name": org.get("name"),
        "seats": seats,
        "used_seats": len(members),
        "pending_invites": len(invites),
        "current_user_role": cur_role,
    }), 200


@bp.get("/users")
@require_clerk_auth
def list_users():
    user_id, org_id = _current_user_ids()
    if not org_id:
        return jsonify({"data": []})
    try:
        memberships = _list_memberships(org_id)
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
                "id": membership_id,
                "user_id": uid,
                "name": name or "—",
                "email": email,
                "role": role,
                "licensed": True,
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
    emails_raw = payload.get("emails")
    if isinstance(emails_raw, str):
        emails = [e.strip().lower() for e in emails_raw.replace(";", ",").replace("\n", ",").split(",") if e.strip()]
    else:
        emails = [str(e).strip().lower() for e in (emails_raw or []) if str(e).strip()]
    if not emails:
        return jsonify(error="emails requerido"), 400

    role_ui = (payload.get("role") or "member").strip().lower()
    role = _map_role_in(role_ui)
    redirect_url = (payload.get("redirect_url") or "").strip() or None
    allow_overbook = bool(payload.get("allow_overbook"))

    # guard de seats
    try:
        org = _get_org(org_id)
        seats = int(((org.get("public_metadata") or {}).get("seats") or 0))
        members = _list_memberships(org_id)
        invites = _list_invitations(org_id)
        used = len(members) + len(invites)
        if seats and not allow_overbook and (used + len(emails) > seats):
            return jsonify(
                error="seat_limit_exceeded",
                detail=f"Usados (miembros + invitaciones): {used}, intentas {len(emails)}, límite seats={seats}"
            ), 409
    except Exception:
        pass

    results: List[Dict[str, Any]] = []
    for email in emails:
        body = {"email_address": email, "role": role}
        if redirect_url:
            body["redirect_url"] = redirect_url
        try:
            res = requests.post(
                f"{_base()}/organizations/{org_id}/invitations",
                headers=_headers_json(),
                json=body,
                timeout=10,
            )
            ok = res.status_code in (200, 201)
            item: Dict[str, Any] = {"email": email, "ok": ok}
            if not ok:
                try:
                    item["error"] = res.json()
                except Exception:
                    item["error"] = res.text
            results.append(item)
        except Exception as e:
            results.append({"email": email, "ok": False, "error": str(e)})

    return jsonify({"results": results}), 200


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
    except requests.HTTPError as e:
        try:
            return jsonify(e.response.json()), e.response.status_code  # type: ignore[attr-defined]
        except Exception:
            return jsonify(error="clerk membership delete failed"), 502
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
    except requests.HTTPError as e:
        try:
            return jsonify(e.response.json()), e.response.status_code  # type: ignore[attr-defined]
        except Exception:
            return jsonify(error="clerk membership update failed"), 502
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
        org = _get_org(org_id)
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
    except requests.HTTPError as e:
        try:
            return jsonify(e.response.json()), e.response.status_code  # type: ignore[attr-defined]
        except Exception:
            return jsonify(error="clerk org update failed"), 502
    except Exception as e:
        current_app.logger.exception("[enterprise] set seats failed: %s", e)
        return jsonify(error="clerk org update failed"), 502
