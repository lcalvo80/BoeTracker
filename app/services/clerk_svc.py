# app/services/clerk_svc.py
import httpx
from flask import current_app

API_BASE = "https://api.clerk.com/v1"

def _headers():
    sk = current_app.config.get("CLERK_SECRET_KEY") or ""
    if not sk:
        raise RuntimeError("CLERK_SECRET_KEY not configured")
    return {
        "Authorization": f"Bearer {sk}",
        "Content-Type": "application/json",
    }

def get_user(user_id: str):
    r = httpx.get(f"{API_BASE}/users/{user_id}", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def get_org(org_id: str):
    r = httpx.get(f"{API_BASE}/organizations/{org_id}", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def update_user_metadata(user_id: str, public: dict | None=None, private: dict | None=None):
    """
    PATCH /v1/users/{id} con public_metadata/private_metadata.
    (Clerk también expone /metadata, pero este endpoint es válido y simple.)
    """
    payload = {}
    if public is not None: payload["public_metadata"] = public
    if private is not None: payload["private_metadata"] = private
    r = httpx.patch(f"{API_BASE}/users/{user_id}", headers=_headers(), json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

def update_org_metadata(org_id: str, public: dict | None=None, private: dict | None=None):
    payload = {}
    if public is not None: payload["public_metadata"] = public
    if private is not None: payload["private_metadata"] = private
    r = httpx.patch(f"{API_BASE}/organizations/{org_id}", headers=_headers(), json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

# Helpers de conveniencia
def set_user_plan(user_id: str, plan: str, status: str | None = None, extra_private: dict | None=None):
    pub = {"plan": plan}
    if status is not None:
        pub["status"] = status
    priv = extra_private or {}
    return update_user_metadata(user_id, public=pub, private=priv)

def set_org_plan(org_id: str, plan: str, status: str | None = None, extra_private: dict | None=None):
    pub = {"plan": plan}
    if status is not None:
        pub["status"] = status
    priv = extra_private or {}
    return update_org_metadata(org_id, public=pub, private=priv)
