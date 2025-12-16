# app/services/clerk_svc.py
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

import httpx
from flask import current_app

API_BASE = (
    os.getenv("CLERK_API_BASE")
    or os.getenv("CLERK_API_URL")
    or os.getenv("CLERK_API_URL_BASE")
    or "https://api.clerk.com/v1"
).rstrip("/")

DEFAULT_PENDING_ENTERPRISE_TTL_MINUTES = int(
    os.getenv("PENDING_ENTERPRISE_TTL_MINUTES")
    or os.getenv("PENDING_ORG_TTL_MINUTES")
    or "10"
)

CLERK_ROLE_TO_API = {
    "admin": "org:admin",
    "owner": "org:admin",
    "org:admin": "org:admin",
    "member": "org:member",
    "org:member": "org:member",
    "basic_member": "org:member",
}
CLERK_ROLE_FROM_API = {
    "admin": "admin",
    "owner": "admin",
    "org:admin": "admin",
    "basic_member": "member",
    "member": "member",
    "org:member": "member",
}


class ClerkHttpError(RuntimeError):
    def __init__(self, status_code: int, method: str, path: str, body: str):
        self.status_code = status_code
        self.method = method
        self.path = path
        self.body = body
        super().__init__(f"Clerk {method} {path} -> {status_code}: {body}")


def _headers() -> Dict[str, str]:
    sk = (
        (getattr(current_app, "config", {}) or {}).get("CLERK_SECRET_KEY")
        or os.getenv("CLERK_SECRET_KEY")
        or ""
    )
    if not sk:
        raise RuntimeError("CLERK_SECRET_KEY not configured")
    return {"Authorization": f"Bearer {sk}", "Content-Type": "application/json"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_dt(s: str | None) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    ss = s.strip()
    if not ss:
        return None
    try:
        if ss.endswith("Z"):
            ss = ss[:-1] + "+00:00"
        dt = datetime.fromisoformat(ss)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _iso_utc(dt: datetime | None = None) -> str:
    d = (dt or _now_utc()).astimezone(timezone.utc)
    return d.isoformat()


def _req(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
    timeout: int = 20,
) -> Any:
    url = f"{API_BASE}{path}"
    r = httpx.request(method, url, headers=_headers(), params=params, json=json, timeout=timeout)
    if r.status_code >= 400:
        body = ""
        try:
            body = r.text or ""
        except Exception:
            body = "<no body>"
        raise ClerkHttpError(r.status_code, method.upper(), path, body)

    if not r.text or not r.text.strip():
        return None

    try:
        return r.json()
    except Exception:
        return r.text


def _normalize_role(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    return CLERK_ROLE_FROM_API.get(str(v).strip().lower(), None)


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
            return ea.get("email_address")
    return None


# ──────────────────────────────────────────────────────────────
# Frontend URL helpers
# ──────────────────────────────────────────────────────────────

def frontend_base() -> str:
    return (current_app.config.get("FRONTEND_BASE_URL") or "http://localhost:3000").rstrip("/")


def append_query(url: str, extra: Dict[str, str]) -> str:
    u = urlparse(url)
    q = dict(parse_qsl(u.query))
    q.update({k: v for k, v in extra.items() if v is not None})
    return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(q), u.fragment))


def build_invite_redirect_url(org_id: str, redirect_url: Optional[str] = None) -> str:
    fb = frontend_base()
    base_redirect = f"{fb}/accept-invitation"
    safe_default = append_query(base_redirect, {"org_id": org_id})

    ru = (redirect_url or "").strip()
    if not ru:
        return safe_default
    if not ru.startswith(fb):
        return safe_default
    return append_query(ru, {"org_id": org_id})


# ─────────────────────────
# Read / Lists (raw)
# ─────────────────────────

def get_user(user_id: str, *, expand: str | None = None) -> dict:
    params = {"expand": expand} if expand else None
    return _req("GET", f"/users/{user_id}", params=params)


def get_org(org_id: str) -> dict:
    return _req("GET", f"/organizations/{org_id}")


def get_user_memberships(user_id: str) -> List[Dict[str, Any]]:
    data = _req("GET", f"/users/{user_id}/organization_memberships")
    return data if isinstance(data, list) else (data.get("data") or [])


def list_org_memberships_raw(
    org_id: str,
    *,
    limit: int = 200,
    include_public_user_data: bool = True,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
    if include_public_user_data:
        params["include_public_user_data"] = "true"
    data = _req("GET", f"/organizations/{org_id}/memberships", params=params)
    return data if isinstance(data, list) else (data.get("data") or [])


def get_org_membership_by_id_raw(
    org_id: str,
    membership_id: str,
    *,
    expand_user: bool = False,
    include_public_user_data: bool = True,
) -> dict:
    params: Dict[str, Any] = {}
    if expand_user:
        params["expand"] = "user"
    if include_public_user_data:
        params["include_public_user_data"] = "true"
    return _req("GET", f"/organizations/{org_id}/memberships/{membership_id}", params=params or None) or {}


def get_membership_raw(user_id: str, org_id: str) -> Dict[str, Any]:
    if not user_id or not org_id:
        return {}
    mems = list_org_memberships_raw(org_id, include_public_user_data=True)
    for m in mems:
        uid = (m.get("public_user_data") or {}).get("user_id") or m.get("user_id")
        if uid == user_id:
            return m
    return {}


def find_membership_id(org_id: str, user_id: str) -> Optional[str]:
    mem = get_membership_raw(user_id=user_id, org_id=org_id) or {}
    return mem.get("id") or None


def is_user_member_of_org(org_id: str, user_id: str) -> bool:
    return bool(find_membership_id(org_id, user_id))


def get_user_primary_email(user_id: str) -> Optional[str]:
    """
    2.2 (hardening): Obtiene email primario de Clerk (para Stripe customer creation/metadata)
    sin depender de lo que venga en el token (g.email).
    """
    try:
        u = get_user(user_id, expand="email_addresses")
        return _extract_email_from_user(u or {})
    except Exception:
        return None


# ─────────── Metadata (merge seguro) ───────────

def update_user_metadata(user_id: str, public: dict | None = None, private: dict | None = None) -> dict:
    payload: Dict[str, Any] = {}
    if public is not None:
        payload["public_metadata"] = public
    if private is not None:
        payload["private_metadata"] = private
    if not payload:
        return get_user(user_id)
    return _req("PATCH", f"/users/{user_id}", json=payload)


def update_org_metadata(org_id: str, public: dict | None = None, private: dict | None = None) -> dict:
    payload: Dict[str, Any] = {}
    if public is not None:
        payload["public_metadata"] = public
    if private is not None:
        payload["private_metadata"] = private
    if not payload:
        return get_org(org_id)
    return _req("PATCH", f"/organizations/{org_id}", json=payload)


def merge_org_metadata(org_id: str, *, public_updates: dict | None = None, private_updates: dict | None = None) -> dict:
    """
    Mergea metadata sin pisar claves existentes (Clerk reemplaza el dict completo).
    """
    org = get_org(org_id)
    pub = dict((org.get("public_metadata") or {}))
    priv = dict((org.get("private_metadata") or {}))

    if public_updates:
        pub.update(public_updates)
    if private_updates:
        priv.update(private_updates)

    return update_org_metadata(org_id, public=pub, private=priv)


def merge_user_metadata(user_id: str, *, public_updates: dict | None = None, private_updates: dict | None = None) -> dict:
    """
    2.3 (alineación): helper simétrico para usuarios (evita pisar public/private_metadata).
    """
    u = get_user(user_id)
    pub = dict((u.get("public_metadata") or {}))
    priv = dict((u.get("private_metadata") or {}))

    if public_updates:
        pub.update(public_updates)
    if private_updates:
        priv.update(private_updates)

    return update_user_metadata(user_id, public=pub, private=priv)


def set_org_seats(org_id: str, seats: int) -> dict:
    seats_i = max(0, int(seats))
    return merge_org_metadata(org_id, public_updates={"seats": seats_i})


# ─────────── Org creation + admin ───────────

def create_org_for_user(
    user_id: str | None,
    name: str,
    public: dict | None = None,
    private: dict | None = None,
) -> dict:
    org = _req("POST", "/organizations", json={"name": name})
    org_id = (org or {}).get("id")

    if user_id and org_id:
        ensure_membership_admin(org_id, user_id)

    if org_id and (public is not None or private is not None):
        merge_org_metadata(org_id, public_updates=public, private_updates=private)

    return get_org(org_id)


def create_org_minimal(name: str) -> str:
    data = _req("POST", "/organizations", json={"name": name})
    return data.get("id") or data.get("organization_id")


def update_membership_role(org_id: str, membership_id: str, role: str) -> Dict[str, Any]:
    return _req("PATCH", f"/organizations/{org_id}/memberships/{membership_id}", json={"role": role}) or {}


def ensure_membership_admin(org_id: str, user_id: str) -> None:
    """
    Asegura que user_id es admin en org_id (idempotente):
      - Si ya es miembro -> PATCH role a org:admin.
      - Si no es miembro -> POST membership admin (409 OK) y reintenta promover.
    """
    if not org_id or not user_id:
        return

    # 1) Si ya es miembro, promover por PATCH
    try:
        mem = get_membership_raw(user_id=user_id, org_id=org_id) or {}
        mid = mem.get("id")
        if mid:
            if _normalize_role(mem.get("role")) == "admin":
                return
            for role in ("org:admin", "admin"):
                try:
                    update_membership_role(org_id, mid, role)
                    return
                except ClerkHttpError:
                    continue
            return
    except Exception:
        pass

    # 2) Intentar crear membership admin
    last_err: Optional[Exception] = None
    for role in ("admin", "org:admin"):
        try:
            _req("POST", f"/organizations/{org_id}/memberships", json={"user_id": user_id, "role": role})
            return
        except ClerkHttpError as e:
            if e.status_code == 409:
                # Existe: promover por PATCH
                try:
                    mem2 = get_membership_raw(user_id=user_id, org_id=org_id) or {}
                    mid2 = mem2.get("id")
                    if mid2:
                        for role2 in ("org:admin", "admin"):
                            try:
                                update_membership_role(org_id, mid2, role2)
                                return
                            except ClerkHttpError:
                                continue
                except Exception:
                    pass
                return
            last_err = e
            continue

    if last_err:
        raise last_err


def delete_membership(org_id: str, membership_id: str) -> None:
    _req("DELETE", f"/organizations/{org_id}/memberships/{membership_id}")


# ─────────── Plans / entitlements ───────────

def set_user_plan(
    user_id: str,
    plan: str,
    status: str | None = None,
    extra_private: dict | None = None,
    extra_public: dict | None = None,
) -> dict:
    """
    Importante: no pisa metadata existente; mergea.
    """
    pub_updates: Dict[str, Any] = {"plan": plan}
    if status is not None:
        pub_updates["status"] = status
    if extra_public:
        pub_updates.update(extra_public)

    priv_updates: Dict[str, Any] = {}
    if extra_private:
        priv_updates.update(extra_private)

    return merge_user_metadata(user_id, public_updates=pub_updates, private_updates=priv_updates)


def set_org_plan(
    org_id: str,
    plan: str,
    status: str | None = None,
    extra_private: dict | None = None,
    extra_public: dict | None = None,
) -> dict:
    pub_updates: Dict[str, Any] = {"plan": plan}
    if status is not None:
        pub_updates["status"] = status
    if extra_public:
        pub_updates.update(extra_public)

    priv_updates: Dict[str, Any] = {}
    if extra_private:
        priv_updates.update(extra_private)

    return merge_org_metadata(org_id, public_updates=pub_updates, private_updates=priv_updates)


def set_entitlement_for_org_members(org_id: str, entitlement: str | None):
    members = list_org_memberships_raw(org_id, include_public_user_data=True)
    for m in members:
        uid = (m.get("public_user_data") or {}).get("user_id") or m.get("user_id")
        if not uid:
            continue
        u = get_user(uid)
        pub = dict((u.get("public_metadata") or {}))
        if entitlement is None:
            pub.pop("entitlement", None)
        else:
            pub["entitlement"] = entitlement
        update_user_metadata(uid, public=pub)


# ─────────── Invitations ───────────

def list_org_invitations_raw(org_id: str, *, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
    if status:
        params["status"] = status
    data = _req("GET", f"/organizations/{org_id}/invitations", params=params)
    return data if isinstance(data, list) else (data.get("data") or [])


def create_org_invitation(
    org_id: str,
    *,
    inviter_user_id: str,
    email_address: str,
    role: str,
    redirect_url: str,
    expires_in_days: Optional[int] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "inviter_user_id": inviter_user_id,
        "email_address": email_address,
        "role": role,
        "redirect_url": redirect_url,
    }
    if expires_in_days is not None:
        payload["expires_in_days"] = int(expires_in_days)
    return _req("POST", f"/organizations/{org_id}/invitations", json=payload) or {}


def revoke_org_invitation(org_id: str, invitation_id: str, *, requesting_user_id: str) -> None:
    _req(
        "POST",
        f"/organizations/{org_id}/invitations/{invitation_id}/revoke",
        json={"requesting_user_id": requesting_user_id},
    )


# ─────────── Seats/admin metrics ───────────

def org_usage(org_id: str) -> Dict[str, int]:
    members = 0
    pending = 0
    try:
        members = len(list_org_memberships_raw(org_id, include_public_user_data=True))
    except Exception:
        pass
    try:
        pending = len(list_org_invitations_raw(org_id, status="pending"))
    except Exception:
        pass
    return {"members": members, "pending": pending, "used": members + pending}


def count_admins(org_id: str) -> int:
    try:
        mems = list_org_memberships_raw(org_id, include_public_user_data=False)
        return sum(1 for m in mems if _normalize_role(m.get("role")) == "admin")
    except Exception:
        return 0


def is_last_admin(org_id: str, membership_id: str) -> bool:
    try:
        mem = get_org_membership_by_id_raw(org_id, membership_id, expand_user=False, include_public_user_data=False) or {}
        if _normalize_role(mem.get("role")) != "admin":
            return False
    except Exception:
        return False
    return count_admins(org_id) <= 1


# ──────────────────────────────────────────────────────────────
# DTOs (listos para API)
# ──────────────────────────────────────────────────────────────

def normalize_member_dto(m: Dict[str, Any]) -> Dict[str, Any]:
    pud = (m.get("public_user_data") or {})
    user = (m.get("user") or {})
    uid = m.get("user_id") or pud.get("user_id") or user.get("id")

    email = pud.get("email_address") or _extract_email_from_user(user)
    name = " ".join(
        [
            (pud.get("first_name") or user.get("first_name") or "") or "",
            (pud.get("last_name") or user.get("last_name") or "") or "",
        ]
    ).strip()

    role = _normalize_role(m.get("role")) or "member"
    return {
        "id": m.get("id"),
        "membership_id": m.get("id"),
        "user_id": uid,
        "email": email,
        "name": name,
        "role": role,
        "organization_id": m.get("organization_id") or (m.get("organization") or {}).get("id"),
    }


def hydrate_member_dto(org_id: str, it: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(it)
    uid = out.get("user_id")

    if not (out.get("email") and uid):
        mid = out.get("membership_id")
        if mid:
            try:
                mem = get_org_membership_by_id_raw(org_id, mid, expand_user=True, include_public_user_data=True)
                n = normalize_member_dto(mem or {})
                for k in ("user_id", "email", "name"):
                    if not out.get(k) and n.get(k):
                        out[k] = n[k]
                uid = out.get("user_id")
            except Exception:
                pass

    if uid and (not out.get("email") or not out.get("name")):
        try:
            u = get_user(uid, expand="email_addresses")
            out["email"] = out.get("email") or _extract_email_from_user(u or {})
            fn = (u or {}).get("first_name") or ""
            ln = (u or {}).get("last_name") or ""
            full = (fn + " " + ln).strip()
            if full:
                out["name"] = out.get("name") or full
        except Exception:
            pass

    return out


def list_users_dto(org_id: str) -> Dict[str, Any]:
    raw = list_org_memberships_raw(org_id, include_public_user_data=True)
    items = [normalize_member_dto(m) for m in (raw or [])]
    items = [hydrate_member_dto(org_id, it) for it in items]
    return {"items": items, "total": len(items)}


def normalize_invitation_dto(it: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": it.get("id"),
        "email": it.get("email_address"),
        "status": it.get("status"),
        "role": CLERK_ROLE_FROM_API.get((it.get("role") or "").lower(), it.get("role")),
        "created_at": it.get("created_at"),
        "updated_at": it.get("updated_at"),
        "expires_at": it.get("expires_at"),
    }


def list_invitations_dto(org_id: str, *, status: Optional[str] = None) -> Dict[str, Any]:
    arr = list_org_invitations_raw(org_id, status=status, limit=200)
    items = [normalize_invitation_dto(x) for x in (arr or [])]
    return {"items": items, "total": len(items)}


def get_org_info_dto(org_id: str, *, user_id: str, token_role: Optional[str]) -> Dict[str, Any]:
    org = get_org(org_id)

    current_role = token_role
    try:
        mem = get_membership_raw(user_id=user_id, org_id=org_id)
        nr = _normalize_role((mem or {}).get("role"))
        if nr:
            current_role = nr
    except Exception:
        pass

    seats = int((org.get("public_metadata") or {}).get("seats") or 0)
    usage = org_usage(org_id)
    used = usage["used"]
    free = max(0, seats - used)

    return {
        "id": org.get("id"),
        "name": org.get("name"),
        "slug": org.get("slug"),
        "seats": seats,
        "used_seats": used,
        "pending_invites": usage["pending"],
        "current_user_role": current_role,
        "used": used,
        "free_seats": free,
        "free": free,
    }


# ─────────── Pending enterprise org idempotencia ───────────

def find_recent_pending_enterprise_org_for_user(
    user_id: str,
    ttl_minutes: int | None = None,
) -> Optional[Tuple[str, str]]:
    ttl = int(ttl_minutes or DEFAULT_PENDING_ENTERPRISE_TTL_MINUTES)
    cutoff = _now_utc() - timedelta(minutes=ttl)

    memberships = get_user_memberships(user_id) or []
    org_ids: List[str] = []
    for m in memberships:
        oid = (
            (m.get("organization") or {}).get("id")
            or m.get("organization_id")
            or m.get("organization")
        )
        if oid and isinstance(oid, str):
            org_ids.append(oid)

    for org_id in org_ids:
        try:
            org = get_org(org_id)
            priv = org.get("private_metadata") or {}
            pending = bool(priv.get("pending_enterprise_checkout"))
            created_by = (priv.get("pending_enterprise_created_by") or "").strip()
            created_at = _parse_iso_dt(priv.get("pending_enterprise_created_at"))

            if not pending:
                continue
            if created_by != user_id:
                continue
            if not created_at or created_at < cutoff:
                continue

            return (org.get("id") or org_id, org.get("name") or "")
        except Exception:
            continue

    return None


def create_pending_enterprise_org_for_user(
    user_id: str,
    name: str,
    seats_default: int = 0,
) -> Dict[str, Any]:
    attempt = str(uuid.uuid4())
    now = _iso_utc()

    public = {"plan": "free", "seats": int(seats_default or 0)}
    private = {
        "created_from": "billing_pricing_page",
        "creator_user_id": user_id,
        "pending_enterprise_checkout": True,
        "pending_enterprise_created_by": user_id,
        "pending_enterprise_created_at": now,
        "pending_enterprise_attempt": attempt,
    }

    return create_org_for_user(user_id=user_id, name=name, public=public, private=private)


# ──────────────────────────────────────────────────────────────
# ✅ ENTERPRISE "Use-cases" (Blueprint 100% orquestación)
# ──────────────────────────────────────────────────────────────

def enterprise_create_org_idempotent(*, user_id: str, name: str) -> Dict[str, Any]:
    name2 = (name or "").strip()
    if not name2 or len(name2) < 2:
        raise ValueError("El campo 'name' es obligatorio (mínimo 2 caracteres).")

    found = find_recent_pending_enterprise_org_for_user(user_id)
    if found:
        org_id, org_name = found
        return {"org_id": org_id, "org_name": org_name}

    org = create_pending_enterprise_org_for_user(user_id=user_id, name=name2, seats_default=0)
    org_id = org.get("id")
    if not org_id:
        raise RuntimeError("No se pudo crear la organización (org_id vacío).")

    return {"org_id": org_id, "org_name": org.get("name") or name2}


def enterprise_get_org_info(*, org_id: str, user_id: str, token_role: Optional[str]) -> Dict[str, Any]:
    if not org_id:
        raise ValueError("Debes indicar organización (X-Org-Id o en el token).")
    return get_org_info_dto(org_id, user_id=user_id, token_role=token_role)


def enterprise_list_users(*, org_id: str) -> Dict[str, Any]:
    if not org_id:
        raise ValueError("Missing org (X-Org-Id).")
    return list_users_dto(org_id)


def enterprise_list_invitations(*, org_id: str, status: Optional[str]) -> Dict[str, Any]:
    if not org_id:
        raise ValueError("Missing org (X-Org-Id).")
    st = (status or "").strip().lower() or None
    if st in ("all", "*"):
        st = None
    return list_invitations_dto(org_id, status=st)


def enterprise_revoke_invitations(
    *,
    org_id: str,
    requesting_user_id: str,
    ids: List[str],
    emails: List[str],
) -> Dict[str, Any]:
    if not org_id:
        raise ValueError("Missing org (X-Org-Id).")

    ids2 = [x for x in (ids or []) if isinstance(x, str) and x.strip()]
    emails2 = [x.strip() for x in (emails or []) if isinstance(x, str) and x.strip()]

    if emails2 and not ids2:
        pending = list_org_invitations_raw(org_id, status="pending", limit=200)
        email_to_id = {it.get("email_address"): it.get("id") for it in (pending or [])}
        ids2 = [email_to_id[e] for e in emails2 if e in email_to_id]

    if not ids2:
        raise ValueError("Debes indicar 'ids' o 'emails'.")

    revoked: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for inv_id in ids2:
        try:
            revoke_org_invitation(org_id, inv_id, requesting_user_id=requesting_user_id)
            revoked.append({"id": inv_id, "revoked": True})
        except Exception as ex:
            errors.append({"id": inv_id, "error": str(ex)})

    return {
        "results": revoked,
        "errors": errors,
        "revoked": len(revoked),
        "failed": len(errors),
    }


def enterprise_invite_users(
    *,
    org_id: str,
    inviter_user_id: str,
    emails: List[str],
    role: str,
    allow_overbook: bool,
    redirect_url: Optional[str],
    expires_in_days: Optional[int],
) -> Dict[str, Any]:
    if not org_id:
        raise ValueError("Missing org (X-Org-Id).")

    emails2 = [e.strip() for e in (emails or []) if isinstance(e, str) and e.strip()]
    if not emails2:
        raise ValueError("Debes indicar 'emails'.")

    role_in = (role or "member").strip().lower()
    role_api = CLERK_ROLE_TO_API.get(role_in)
    if role_api not in ("org:member", "org:admin"):
        raise ValueError("role debe ser 'admin' o 'member'.")

    if expires_in_days is not None:
        ex = int(expires_in_days)
        if not (1 <= ex <= 30):
            raise ValueError("'expires_in_days' debe estar entre 1 y 30.")
    else:
        ex = None

    safe_redirect = build_invite_redirect_url(org_id, redirect_url)

    try:
        org = get_org(org_id)
        seats = int((org.get("public_metadata") or {}).get("seats") or 0)
    except Exception:
        seats = 0

    usage = org_usage(org_id)
    used = usage["used"]
    free = max(0, seats - used)
    needed = len(emails2)

    if not bool(allow_overbook) and needed > free:
        raise ClerkHttpError(
            409,
            "INVITE",
            f"/organizations/{org_id}/invitations",
            f'not_enough_seats: seats={seats} used={used} free={free} needed={needed}',
        )

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for email in emails2:
        try:
            r = create_org_invitation(
                org_id,
                inviter_user_id=inviter_user_id,
                email_address=email,
                role=role_api,
                redirect_url=safe_redirect,
                expires_in_days=ex,
            )
            results.append({"id": r.get("id"), "email": r.get("email_address"), "status": r.get("status")})
        except Exception as e:
            errors.append({"email": email, "error": str(e)})

    return {"results": results, "errors": errors}


def enterprise_update_role(
    *,
    org_id: str,
    membership_id: Optional[str],
    user_id: Optional[str],
    role: str,
) -> Dict[str, Any]:
    if not org_id:
        raise ValueError("Missing org (X-Org-Id).")

    role_in = (role or "").lower().strip()
    role_api = CLERK_ROLE_TO_API.get(role_in)
    if role_api not in ("org:admin", "org:member"):
        raise ValueError("role debe ser 'admin' o 'member'.")

    mid = (membership_id or "").strip() or None
    uid = (user_id or "").strip() or None

    if not mid and uid:
        mid = find_membership_id(org_id, uid)
    if not mid:
        raise ValueError("membership_id o user_id requeridos.")

    if role_api != "org:admin" and is_last_admin(org_id, mid):
        raise ClerkHttpError(409, "PATCH", f"/organizations/{org_id}/memberships/{mid}", "cannot_demote_last_admin")

    try:
        res = update_membership_role(org_id, mid, role_api)
        return normalize_member_dto(res or {})
    except ClerkHttpError as ce:
        if ce.status_code == 404:
            raise ClerkHttpError(404, "PATCH", f"/organizations/{org_id}/memberships/{mid}", "membership_not_found")
        raise


def enterprise_remove_user(
    *,
    org_id: str,
    membership_id: Optional[str],
    user_id: Optional[str],
) -> Dict[str, Any]:
    if not org_id:
        raise ValueError("Missing org (X-Org-Id).")

    mid = (membership_id or "").strip() or None
    uid = (user_id or "").strip() or None

    if not mid and uid:
        mid = find_membership_id(org_id, uid)
    if not mid:
        raise ValueError("membership_id o user_id requeridos.")

    if is_last_admin(org_id, mid):
        raise ClerkHttpError(409, "DELETE", f"/organizations/{org_id}/memberships/{mid}", "cannot_remove_last_admin")

    try:
        delete_membership(org_id, mid)
        return {"removed": True, "membership_id": mid}
    except ClerkHttpError as ce:
        if ce.status_code == 404:
            raise ClerkHttpError(404, "DELETE", f"/organizations/{org_id}/memberships/{mid}", "membership_not_found")
        raise


def enterprise_set_seat_limit(*, org_id: str, seats: int) -> Dict[str, Any]:
    if not org_id:
        raise ValueError("Missing org (X-Org-Id).")
    return {"org_id": org_id, "seats": int(seats), "org": set_org_seats(org_id, int(seats))}


# ──────────────────────────────────────────────────────────────
# ✅ 5) CLEANUP: pending enterprise orgs cuando NO se completa el pago
# ──────────────────────────────────────────────────────────────

def enterprise_list_pending_orgs_for_user(user_id: str) -> List[str]:
    """
    Devuelve org_ids del usuario con private_metadata.pending_enterprise_checkout == True.
    """
    org_ids: List[str] = []
    memberships = get_user_memberships(user_id) or []
    for m in memberships:
        oid = (
            (m.get("organization") or {}).get("id")
            or m.get("organization_id")
            or m.get("organization")
        )
        if oid and isinstance(oid, str):
            org_ids.append(oid)

    pending: List[str] = []
    for org_id in org_ids:
        try:
            org = get_org(org_id)
            priv = org.get("private_metadata") or {}
            if bool(priv.get("pending_enterprise_checkout")):
                pending.append(org.get("id") or org_id)
        except Exception:
            continue

    # unique (preserva orden)
    seen = set()
    out: List[str] = []
    for oid in pending:
        if oid not in seen:
            seen.add(oid)
            out.append(oid)
    return out


def enterprise_cleanup_org(
    org_id: str,
    *,
    seats: int = 0,
    plan: str = "free",
    mark_canceled_at: bool = True,
    canceled_reason: Optional[str] = None,
    stripe_customer_id: Optional[str] = None,
    stripe_subscription_id: Optional[str] = None,
) -> dict:
    """
    Limpia una org colgada en estado pending enterprise checkout:
      - public_metadata.plan = free
      - public_metadata.seats = seats (por defecto 0)
      - private_metadata.pending_enterprise_checkout = False
      - opcional: private_metadata.pending_canceled_at
      - opcional: guarda stripe_customer_id / stripe_subscription_id si llegan
    Idempotente: si ya está limpio, no rompe.
    """
    if not org_id:
        raise ValueError("org_id required")

    pub_updates = {"plan": plan, "seats": max(0, int(seats))}
    priv_updates: Dict[str, Any] = {"pending_enterprise_checkout": False}

    if mark_canceled_at:
        priv_updates["pending_canceled_at"] = _iso_utc()
    if canceled_reason:
        priv_updates["pending_canceled_reason"] = str(canceled_reason)[:120]
    if stripe_customer_id:
        priv_updates["stripe_customer_id"] = stripe_customer_id
    if stripe_subscription_id:
        priv_updates["stripe_subscription_id"] = stripe_subscription_id

    return merge_org_metadata(org_id, public_updates=pub_updates, private_updates=priv_updates)


def enterprise_checkout_cancel_cleanup(user_id: str) -> Dict[str, Any]:
    """
    Endpoint MVP: limpia TODAS las orgs del usuario que estén pending_enterprise_checkout=true.
    Devuelve: { cleaned: N, org_ids: [...], errors:[...] }
    """
    if not user_id:
        raise ValueError("user_id required")

    org_ids = enterprise_list_pending_orgs_for_user(user_id)

    cleaned = 0
    cleaned_orgs: List[str] = []
    errors: List[Dict[str, Any]] = []

    for oid in org_ids:
        try:
            enterprise_cleanup_org(
                oid,
                seats=0,
                plan="free",
                mark_canceled_at=True,
                canceled_reason="checkout_cancel",
            )
            cleaned += 1
            cleaned_orgs.append(oid)
        except Exception as e:
            errors.append({"org_id": oid, "error": str(e)})

    return {"cleaned": cleaned, "org_ids": cleaned_orgs, "errors": errors}
