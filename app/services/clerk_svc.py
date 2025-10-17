from __future__ import annotations

import os
from typing import Any, Dict, List

import httpx
from flask import current_app

API_BASE = "https://api.clerk.com/v1"


def _headers() -> Dict[str, str]:
    """
    Lee el Secret de Clerk desde config o env. Lanza si falta.
    """
    sk = (getattr(current_app, "config", {}) or {}).get("CLERK_SECRET_KEY") or os.getenv("CLERK_SECRET_KEY") or ""
    if not sk:
        raise RuntimeError("CLERK_SECRET_KEY not configured")
    return {"Authorization": f"Bearer {sk}", "Content-Type": "application/json"}


# ─────────── Lectura ───────────

def get_user(user_id: str) -> dict:
    r = httpx.get(f"{API_BASE}/users/{user_id}", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def get_org(org_id: str) -> dict:
    r = httpx.get(f"{API_BASE}/organizations/{org_id}", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def get_user_memberships(user_id: str) -> List[Dict[str, Any]]:
    r = httpx.get(
        f"{API_BASE}/users/{user_id}/organization_memberships",
        headers=_headers(),
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else (data.get("data") or [])


def list_org_memberships(org_id: str) -> List[Dict[str, Any]]:
    """Lista los memberships de una organización."""
    r = httpx.get(
        f"{API_BASE}/organizations/{org_id}/memberships",
        headers=_headers(),
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else (data.get("data") or [])


def get_membership(user_id: str, org_id: str) -> Dict[str, Any]:
    """
    Devuelve la membership concreta de un usuario en una organización.
    Si no existe, {}.
    """
    if not user_id or not org_id:
        return {}
    r = httpx.get(
        f"{API_BASE}/organizations/{org_id}/memberships",
        params={"user_id": user_id, "limit": 1},
        headers=_headers(),
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    arr = data if isinstance(data, list) else (data.get("data") or [])
    return arr[0] if arr else {}


# ─────────── Metadata ───────────

def update_user_metadata(user_id: str, public: dict | None = None, private: dict | None = None) -> dict:
    payload: Dict[str, Any] = {}
    if public is not None:
        payload["public_metadata"] = public
    if private is not None:
        payload["private_metadata"] = private
    if not payload:
        return get_user(user_id)
    r = httpx.patch(f"{API_BASE}/users/{user_id}", headers=_headers(), json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def update_org_metadata(org_id: str, public: dict | None = None, private: dict | None = None) -> dict:
    payload: Dict[str, Any] = {}
    if public is not None:
        payload["public_metadata"] = public
    if private is not None:
        payload["private_metadata"] = private
    if not payload:
        return get_org(org_id)
    r = httpx.patch(f"{API_BASE}/organizations/{org_id}", headers=_headers(), json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


# ─────────── Creación organización + admin ───────────

def create_org_for_user(user_id: str | None, name: str, public: dict | None = None, private: dict | None = None) -> dict:
    """
    Crea la organización y (si user_id) añade al usuario como admin.
    """
    r = httpx.post(f"{API_BASE}/organizations", headers=_headers(), json={"name": name}, timeout=10)
    r.raise_for_status()
    org = r.json()
    org_id = org.get("id")

    if user_id:
        r2 = httpx.post(
            f"{API_BASE}/organizations/{org_id}/memberships",
            headers=_headers(),
            json={"user_id": user_id, "role": "admin"},
            timeout=10,
        )
        r2.raise_for_status()

    if public or private:
        update_org_metadata(org_id, public, private)

    return org


# ─────────── Helpers de plan ───────────

def set_user_plan(
    user_id: str,
    plan: str,
    status: str | None = None,
    extra_private: dict | None = None,
    extra_public: dict | None = None,
) -> dict:
    curr = get_user(user_id)
    pub = dict((curr.get("public_metadata") or {}))
    pub.update({"plan": plan})
    if status is not None:
        pub["status"] = status
    if extra_public:
        pub.update(extra_public)
    priv = extra_private or {}
    return update_user_metadata(user_id, public=pub, private=priv)


def set_org_plan(
    org_id: str,
    plan: str,
    status: str | None = None,
    extra_private: dict | None = None,
    extra_public: dict | None = None,
) -> dict:
    curr = get_org(org_id)
    pub = dict((curr.get("public_metadata") or {}))
    pub.update({"plan": plan})
    if status is not None:
        pub["status"] = status
    if extra_public:
        pub.update(extra_public)
    priv = extra_private or {}
    return update_org_metadata(org_id, public=pub, private=priv)


# ─────────── Propagación de entitlement a miembros ───────────

def set_entitlement_for_org_members(org_id: str, entitlement: str | None):
    """
    Propaga public_metadata.entitlement a todos los usuarios miembros de la org.
    Mantiene el resto de claves de public_metadata (merge).
    """
    members = list_org_memberships(org_id)
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


# ─────────── Invitado enterprise (opcional) ───────────

def find_users_by_email(email: str) -> List[Dict[str, Any]]:
    r = httpx.get(f"{API_BASE}/users", params={"email_address": email}, headers=_headers(), timeout=10)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else (data.get("data") or [])


def create_user_skeleton(email: str) -> Dict[str, Any]:
    payload = {"email_address": [email], "skip_password_requirement": True}
    r = httpx.post(f"{API_BASE}/users", headers=_headers(), json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def ensure_user_by_email(email: str) -> Dict[str, Any]:
    found = find_users_by_email(email)
    if found:
        return found[0] if isinstance(found, list) else found
    return create_user_skeleton(email)
