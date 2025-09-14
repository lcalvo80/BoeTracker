# app/services/clerk_svc.py
import httpx
from flask import current_app

def _headers():
    return {
        "Authorization": f"Bearer {current_app.config['CLERK_SECRET_KEY']}",
        "Content-Type": "application/json",
    }

def get_user(user_id: str):
    r = httpx.get(f"https://api.clerk.com/v1/users/{user_id}", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def get_org(org_id: str):
    r = httpx.get(f"https://api.clerk.com/v1/organizations/{org_id}", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def update_user_metadata(user_id: str, public: dict | None=None, private: dict | None=None):
    payload = {}
    if public is not None: payload["public_metadata"] = public
    if private is not None: payload["private_metadata"] = private
    r = httpx.patch(f"https://api.clerk.com/v1/users/{user_id}", headers=_headers(), json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

def update_org_metadata(org_id: str, public: dict | None=None, private: dict | None=None):
    payload = {}
    if public is not None: payload["public_metadata"] = public
    if private is not None: payload["private_metadata"] = private
    r = httpx.patch(f"https://api.clerk.com/v1/organizations/{org_id}", headers=_headers(), json=payload, timeout=10)
    r.raise_for_status()
    return r.json()
