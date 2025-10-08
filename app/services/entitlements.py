# app/services/entitlements.py
from __future__ import annotations
import os
import requests
from typing import Any, Dict, Optional, List
from flask import g
from app.services import clerk_svc  # get_user, get_org, update_user_metadata

CLERK_API_BASE = os.getenv("CLERK_API_BASE", "https://api.clerk.com/v1").rstrip("/")
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY", "")

def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {CLERK_SECRET_KEY}", "Content-Type": "application/json"}

# ---------- helpers de decisión ----------
def _org_is_enterprise(org: Dict[str, Any] | None) -> bool:
    """
    Consideramos Enterprise si la org tiene public_metadata.subscription/plan/tier = 'enterprise' o 'enterprise_draft'.
    """
    pub = (org or {}).get("public_metadata") or {}
    plan = (pub.get("subscription") or pub.get("plan") or pub.get("tier") or "free")
    return str(plan).strip().lower() in {"enterprise", "enterprise_draft"}

def _wants_enterprise_member(entitlement: Optional[str]) -> bool:
    return str(entitlement or "").strip().lower() in {"enterprise", "enterprise_member"}

# ---------- operaciones idempotentes ----------
def ensure_user_enterprise_member(user_id: str, org: Dict[str, Any]) -> bool:
    """
    Si la organización es Enterprise y el usuario NO tiene aún enterprise/enterprise_member,
    sube su public_metadata a { plan: 'enterprise', entitlement: 'enterprise_member' }.
    Devuelve True si hubo actualización, False si ya estaba bien o no aplica.
    """
    if not _org_is_enterprise(org):
        return False
    try:
        u = clerk_svc.get_user(user_id) or {}
        pub = u.get("public_metadata") or {}
        ent = pub.get("entitlement") or pub.get("plan")
        if _wants_enterprise_member(ent):
            return False  # ya correcto, no tocar
        clerk_svc.update_user_metadata(
            user_id,
            public={
                "plan": "enterprise",
                "entitlement": "enterprise_member",
            }
        )
        return True
    except Exception:
        # silencioso: nunca rompemos el flujo
        return False

def maybe_sync_current_user_entitlement(org_id: str) -> Optional[bool]:
    """
    Atajo para usar desde /org/info: intenta subir el entitlement del usuario actual.
    No lanza excepciones.
    """
    uid = (getattr(g, "clerk", {}) or {}).get("user_id")
    if not uid:
        return None
    try:
        org = clerk_svc.get_org(org_id) or {}
    except Exception:
        org = {}
    return ensure_user_enterprise_member(uid, org)

def _list_org_memberships(org_id: str) -> List[Dict[str, Any]]:
    """
    Listado simple de memberships usando la Admin API (requiere CLERK_SECRET_KEY).
    """
    if not CLERK_SECRET_KEY:
        return []
    try:
        r = requests.get(
            f"{CLERK_API_BASE}/organizations/{org_id}/memberships?limit=200",
            headers=_headers(),
            timeout=15
        )
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("data") or []
    except Exception:
        return []

def sync_entitlements_for_org(org_id: str) -> Dict[str, int | bool]:
    """
    Recorre los miembros de la org y asegura enterprise_member para todos.
    Úsalo tras /billing/sync o en el webhook post-checkout.
    Silencioso ante errores.
    """
    try:
        org = clerk_svc.get_org(org_id) or {}
    except Exception:
        org = {}

    if not _org_is_enterprise(org):
        return {"org_is_enterprise": False, "updated": 0, "total": 0}

    updated = 0
    members = _list_org_memberships(org_id)
    for m in members:
        uid = (m.get("public_user_data") or {}).get("user_id") or m.get("user_id") or m.get("id")
        if not uid:
            continue
        try:
            if ensure_user_enterprise_member(uid, org):
                updated += 1
        except Exception:
            pass

    return {"org_is_enterprise": True, "updated": updated, "total": len(members)}
