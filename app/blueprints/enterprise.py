# app/enterprise.py
from __future__ import annotations
import os, requests
from functools import wraps
from typing import Any, Dict, List, Optional, Sequence
from flask import Blueprint, jsonify, request, g, current_app
from app.auth import require_clerk_auth

bp = Blueprint("enterprise", __name__, url_prefix="/api/enterprise")

# ───────── helpers básicos ─────────
def _cfg(k: str, default: Optional[str] = None) -> str:
    try:
        v = current_app.config.get(k)  # type: ignore[attr-defined]
    except Exception:
        v = None
    if v is None or str(v).strip() == "":
        v = os.getenv(k, default or "")
    return str(v or "")

def _clerk_secret_present() -> bool:
    return bool(_cfg("CLERK_SECRET_KEY", ""))

def _require_clerk_secret(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _clerk_secret_present():
            return jsonify({
                "error": "clerk_secret_missing",
                "detail": "Configura CLERK_SECRET_KEY en el backend para usar endpoints de organización."
            }), 501
        return fn(*args, **kwargs)
    return wrapper

def _headers_json() -> Dict[str, str]:
    sk = _cfg("CLERK_SECRET_KEY", "")
    return {"Authorization": f"Bearer {sk}", "Content-Type": "application/json"}

def _base() -> str:
    return "https://api.clerk.com/v1"

# ───────── roles ─────────
def _is_admin_slug(role: str) -> bool:
    r = (role or "").strip().lower()
    return r in {
        "admin", "owner",
        "org:admin", "org_admin", "orgadmin", "organization_admin",
        "org:owner", "org_owner", "orgowner", "organization_owner",
    }

def _map_role_out(role: str) -> str:
    return "admin" if _is_admin_slug(role) else "member"

def _map_role_in(role: str) -> str:
    r = (role or "").strip().lower()
    if r in ("admin", "owner", "org:admin", "org_admin"):
        return "org:admin"
    return "org:member"

# ───────── contexto usuario/org ─────────
def _current_user_ids() -> tuple[str, Optional[str]]:
    """
    Lee user_id del JWT validado (guardado en g.clerk por @require_clerk_auth)
    y org_id de cabecera X-Org-Id, query ?org_id, o claims.
    """
    c = getattr(g, "clerk", {}) or {}
    org_from_req = request.headers.get("X-Org-Id") or request.args.get("org_id")
    org_id = (org_from_req or c.get("org_id")) or None
    return c.get("user_id"), org_id

def _is_enterprise_admin(user_id: str, org_id: str) -> bool:
    try:
        url = f"{_base()}/organizations/{org_id}/memberships?limit=1&user_id={user_id}"
        res = requests.get(url, headers=_headers_json(), timeout=10)
        res.raise_for_status()
        data = res.json()
        arr = data if isinstance(data, list) else data.get("data") or []
        role = (arr[0].get("role") or "").lower() if arr else ""
        return _is_admin_slug(role)
    except Exception as e:
        current_app.logger.warning(f"[enterprise] membership check skipped: {e}")
        return False

# ───────── Clerk helpers ─────────
def _get_org(org_id: str) -> dict:
    r = requests.get(f"{_base()}/organizations/{org_id}", headers=_headers_json(), timeout=10)
    r.raise_for_status()
    return r.json()

def _list_memberships(org_id: str) -> List[dict]:
    r = requests.get(f"{_base()}/organizations/{org_id}/memberships?limit=200", headers=_headers_json(), timeout=10)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("data") or []

def _list_invitations(org_id: str, *, statuses: Optional[Sequence[str]] = None, email: Optional[str] = None) -> List[dict]:
    """
    Lista invitaciones con filtro de estado y/o email.
    Por defecto SOLO 'pending' (para no sobre-contar seats).
    """
    params = []
    if statuses:
        for s in statuses:
            params.append(("status", s))
    else:
        params.append(("status", "pending"))
    if email:
        params.append(("query", email))
    # Clerk admite paginación; para 200 primeras suele ser suficiente.
    url = f"{_base()}/organizations/{org_id}/invitations?limit=200"
    if params:
        qs = "&".join([f"{k}={requests.utils.quote(v)}" for k, v in params])
        url += "&" + qs
    r = requests.get(url, headers=_headers_json(), timeout=10)
    if r.status_code not in (200, 201):
        return []
    data = r.json()
    arr = data if isinstance(data, list) else data.get("data") or []
    # Normalizamos `status` a minúsculas
    for it in arr:
        st = (it.get("status") or "").lower()
        it["status"] = st
    return arr

# ───────── endpoints ─────────

@bp.post("/org/create")
@bp.post("/create-org")
@require_clerk_auth
@_require_clerk_secret
def create_org():
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

        r = requests.post(
            f"{_base()}/organizations",
            headers=_headers_json(),
            json={"name": name, "created_by": user_id},
            timeout=10,
        )
        r.raise_for_status()
        org = r.json()
        org_id = org.get("id")

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
        try:
            return jsonify(error="clerk error", detail=e.response.json()), e.response.status_code  # type: ignore[attr-defined]
        except Exception:
            return jsonify(error="clerk error", detail=str(e)), 502
    except Exception as e:
        current_app.logger.exception("[enterprise] create_org failed: %s", e)
        return jsonify(error="create_org failed", detail=str(e)), 500


@bp.get("/org")
@require_clerk_auth
@_require_clerk_secret
def org_info():
    user_id, org_id = _current_user_ids()
    if not org_id:
        return jsonify({"id": None, "name": None, "seats": 0, "used_seats": 0, "pending_invites": 0, "current_user_role": None})

    try:
        org = _get_org(org_id)
        members = _list_memberships(org_id)
        # 👉 sólo pending para contabilidad de seats
        invites_pending = _list_invitations(org_id, statuses=["pending"])
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
        "used_seats": len(members) + len(invites_pending),  # 👈 ya no sumamos revoked/accepted/expired
        "pending_invites": len(invites_pending),
        "current_user_role": cur_role,
    }), 200


@bp.get("/users")
@require_clerk_auth
@_require_clerk_secret
def list_users():
    _, org_id = _current_user_ids()
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


@bp.get("/invitations")
@require_clerk_auth
@_require_clerk_secret
def list_invitations():
    """
    Lista invitaciones con filtro opcional de estados y query (email).
    /api/enterprise/invitations?statuses=pending,revoked&query=foo@bar.com
    """
    _, org_id = _current_user_ids()
    if not org_id:
        return jsonify({"data": []})
    raw = request.args.get("statuses") or ""
    statuses = [s.strip().lower() for s in raw.split(",") if s.strip()] or ["pending"]
    query = (request.args.get("query") or "").strip() or None
    inv = _list_invitations(org_id, statuses=statuses, email=query)
    return jsonify({"data": inv}), 200


@bp.post("/invitations/revoke")
@require_clerk_auth
@_require_clerk_secret
def revoke_invitation():
    """
    Revoca una invitación por ID o por email (si hay una pending).
    """
    user_id, org_id = _current_user_ids()
    if not org_id:
        return jsonify(error="organization required"), 403
    if not _is_enterprise_admin(user_id, org_id):
        return jsonify(error="forbidden: organization admin required"), 403

    payload = request.get_json(silent=True) or {}
    invitation_id = (payload.get("invitation_id") or "").strip()
    email = (payload.get("email") or "").strip().lower()

    if not invitation_id and email:
        inv = _list_invitations(org_id, statuses=["pending"], email=email)
        if inv:
            invitation_id = inv[0].get("id") or ""

    if not invitation_id:
        return jsonify(error="invitation_id o email requerido"), 400

    try:
        url = f"{_base()}/organizations/{org_id}/invitations/{invitation_id}/revoke"
        r = requests.post(url, headers=_headers_json(), json={}, timeout=10)
        r.raise_for_status()
        return jsonify({"ok": True}), 200
    except requests.HTTPError as e:
        try:
            return jsonify(e.response.json()), e.response.status_code  # type: ignore[attr-defined]
        except Exception:
            return jsonify(error="clerk invitation revoke failed"), 502
    except Exception as e:
        current_app.logger.exception("[enterprise] revoke invitation failed: %s", e)
        return jsonify(error="clerk invitation revoke failed"), 502


@bp.post("/invite")
@require_clerk_auth
@_require_clerk_secret
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
        single = (payload.get("email") or "").strip().lower()
        if single:
            emails = [single]
    if not emails:
        return jsonify(error="emails requerido"), 400

    role_ui = (payload.get("role") or "member").strip().lower()
    role = _map_role_in(role_ui)
    redirect_url = (payload.get("redirect_url") or "").strip() or None
    allow_overbook = bool(payload.get("allow_overbook"))
    # nueva opción: caducidad (por defecto 7 días)
    try:
        expires_in_days = int(payload.get("expires_in_days")) if payload.get("expires_in_days") is not None else 7
    except Exception:
        expires_in_days = 7
    if expires_in_days < 1:
        expires_in_days = 7

    # Seat check: sólo PENDING
    try:
        org = _get_org(org_id)
        seats = int(((org.get("public_metadata") or {}).get("seats") or 0))
        members = _list_memberships(org_id)
        pending = _list_invitations(org_id, statuses=["pending"])
        used = len(members) + len(pending)
        if seats and not allow_overbook and (used + len(emails) > seats):
            return jsonify(
                error="seat_limit_exceeded",
                detail=f"Usados (miembros + invitaciones pendientes): {used}, intentas {len(emails)}, límite seats={seats}"
            ), 409
    except Exception:
        pass

    # Evita duplicados: si ya hay PENDING para ese email, lo reportamos
    results: List[Dict[str, Any]] = []
    for email in emails:
        # ¿ya existe pending para este email?
        existing = _list_invitations(org_id, statuses=["pending"], email=email)
        if existing:
            results.append({"email": email, "ok": False, "error": "invitation_exists_pending"})
            continue

        body = {"email_address": email, "role": role, "expires_in_days": expires_in_days}
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

    status = 200
    if any((not x.get("ok")) for x in results):
        # Si todas fallan por duplicado, 409; si hay mezcla, 207 (multi-status) va raro en CORS → usamos 200 con detalle.
        if all(x.get("error") == "invitation_exists_pending" for x in results):
            status = 409
    return jsonify({"results": results}), status


@bp.post("/remove")
@require_clerk_auth
@_require_clerk_secret
def remove_user():
    user_id, org_id = _current_user_ids()
    if not org_id:
        return jsonify(error="organization required"), 403
    if not _is_enterprise_admin(user_id, org_id):
        return jsonify(error="forbidden: organization admin required"), 403

    payload = request.get_json(silent=True) or {}
    membership_id = payload.get("membership_id") or payload.get("id")
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
            body = e.response.json()  # type: ignore[attr-defined]
        except Exception:
            body = {"error": "clerk membership delete failed"}
        code = getattr(e.response, "status_code", 502)  # type: ignore[attr-defined]
        return jsonify(body), code
    except Exception as e:
        current_app.logger.exception("[enterprise] delete membership failed: %s", e)
        return jsonify(error="clerk membership delete failed"), 502


@bp.post("/update-role")
@require_clerk_auth
@_require_clerk_secret
def update_role():
    user_id, org_id = _current_user_ids()
    if not org_id:
        return jsonify(error="organization required"), 403
    if not _is_enterprise_admin(user_id, org_id):
        return jsonify(error="forbidden: organization admin required"), 403

    payload = request.get_json(silent=True) or {}
    membership_id = payload.get("membership_id") or payload.get("id")
    target_user_id = payload.get("user_id")
    role_ui = (payload.get("role") or "").strip().lower()
    if role_ui not in ("member", "admin"):
        return jsonify(error="role inválido (member|admin)"), 400
    role_slug = _map_role_in(role_ui)

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
            json={"role": role_slug},
            timeout=10,
        )
        res.raise_for_status()
        return jsonify({"ok": True}), 200
    except requests.HTTPError as e:
        try:
            body = e.response.json()  # type: ignore[attr-defined]
        except Exception:
            body = {"error": "clerk membership update failed"}
        code = getattr(e.response, "status_code", 502)  # type: ignore[attr-defined]
        return jsonify(body), code
    except Exception as e:
        current_app.logger.exception("[enterprise] update role failed: %s", e)
        return jsonify(error="clerk membership update failed"), 502


@bp.post("/set-seat-limit")
@require_clerk_auth
@_require_clerk_secret
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
