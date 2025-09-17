# app/integrations/clerk_admin.py
import requests
from flask import current_app

BASE = "https://api.clerk.com/v1"


def _headers():
    sk = current_app.config.get("CLERK_SECRET_KEY")
    if not sk:
        raise RuntimeError("Missing CLERK_SECRET_KEY")
    return {
        "Authorization": f"Bearer {sk}",
        "Content-Type": "application/json",
    }


def patch_user_public_metadata(user_id: str, data: dict):
    url = f"{BASE}/users/{user_id}"
    payload = {"public_metadata": data}
    res = requests.patch(url, headers=_headers(), json=payload, timeout=10)
    res.raise_for_status()
    return res.json()


def patch_org_public_metadata(org_id: str, data: dict):
    url = f"{BASE}/organizations/{org_id}"
    payload = {"public_metadata": data}
    res = requests.patch(url, headers=_headers(), json=payload, timeout=10)
    res.raise_for_status()
    return res.json()


def get_user(user_id: str):
    url = f"{BASE}/users/{user_id}"
    res = requests.get(url, headers=_headers(), timeout=10)
    res.raise_for_status()
    return res.json()


def get_org(org_id: str):
    url = f"{BASE}/organizations/{org_id}"
    res = requests.get(url, headers=_headers(), timeout=10)
    res.raise_for_status()
    return res.json()
