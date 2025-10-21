# app/services/entitlements.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional, List, Tuple

import requests
from flask import g, current_app

# Stripe es opcional: sólo se usa si hay SECRET_KEY configurada
try:
    import stripe  # type: ignore
except Exception:  # pragma: no cover
    stripe = None  # fallback si no está instalado

# Clerk service esperado en el proyecto:
#   clerk_svc.get_user(user_id) -> dict
#   clerk_svc.get_org(org_id) -> dict
#   clerk_svc.update_user_metadata(user_id, public: dict | None, private: dict | None)
# (Si no tuvieras update_org_metadata, este módulo hace PATCH directo a la Admin API)
from app.services import clerk_svc

CLERK_API_BASE = os.getenv("CLERK_API_BASE", "https://api.clerk.com/v1").rstrip("/")
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")


# ────────────────────────────── HTTP helpers ──────────────────────────────

def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {CLERK_SECRET_KEY}", "Content-Type": "application/json"}


def _admin_api_get(path: str, timeout: int = 20) -> Dict[str, Any]:
    r = requests.get(f"{CLERK_API_BASE}{path}", headers=_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json() if r.text.strip() else {}


def _admin_api_patch(path: str, json: Dict[str, Any], timeout: int = 20) -> Dict[str, Any]:
    r = requests.patch(f"{CLERK_API_BASE}{path}", headers=_headers(), json=json, timeout=timeout)
    r.raise_for_status()
    return r.json() if r.text.strip() else {}


# ────────────────────────────── Normalización ──────────────────────────────

def _norm_str(v: Any) -> str:
    return ("" if v is None else str(v)).strip()


def _org_is_enterprise(org: Optional[Dict[str, Any]]) -> bool:
    """
    Consideramos Enterprise si:
      - public_metadata.subscription / plan / tier ∈ {'enterprise','enterprise_draft'}
      - o seats > 0 (heurística útil)
    """
    pub = (org or {}).get("public_metadata") or {}
    plan = _norm_str(pub.get("subscription") or pub.get("plan") or pub.get("tier") or "free").lower()
    if plan in {"enterprise", "enterprise_draft"}:
        return True
    try:
        seats = int(pub.get("seats") or 0)
        if seats > 0:
            return True
    except Exception:
        pass
    return False


def _wants_enterprise_member(entitlement: Optional[str]) -> bool:
    return _norm_str(entitlement).lower() in {"enterprise", "enterprise_member"}


# ─────────────────────────── Clerk metadata updates ───────────────────────────

def _update_user_metadata(user_id: str, public: Optional[Dict[str, Any]] = None,
                          private: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Usa clerk_svc.update_user_metadata si existe; si no, Admin API PATCH /users/{id}.
    """
    try:
        if hasattr(clerk_svc, "update_user_metadata"):
            return clerk_svc.update_user_metadata(user_id, public=public, private=private)  # type: ignore
    except Exception:
        pass  # fallback a Admin API

    if not CLERK_SECRET_KEY:
        return {}
    payload: Dict[str, Any] = {}
    if public is not None:
        payload["public_metadata"] = public
    if private is not None:
        payload["private_metadata"] = private
    try:
        return _admin_api_patch(f"/users/{user_id}", json=payload)
    except Exception:
        return {}


def _get_org(org_id: str) -> Dict[str, Any]:
    try:
        return clerk_svc.get_org(org_id) or {}
    except Exception:
        pass
    if not CLERK_SECRET_KEY:
        return {}
    try:
        return _admin_api_get(f"/organizations/{org_id}")
    except Exception:
        return {}


def _update_org_public_metadata(org_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Realiza un merge simple sobre public_metadata de la organización (GET+PATCH).
    """
    if not CLERK_SECRET_KEY:
        return {}
    try:
        org = _get_org(org_id)
        current_pub = (org.get("public_metadata") or {}) if isinstance(org, dict) else {}
        merged = {**current_pub, **(patch or {})}
        return _admin_api_patch(f"/organizations/{org_id}", json={"public_metadata": merged})
    except Exception:
        return {}


# ───────────────────────────── Stripe helpers ─────────────────────────────

def _stripe_init() -> bool:
    """
    Inicializa stripe si hay secret; devuelve True si está listo.
    """
    key = STRIPE_SECRET_KEY or (getattr(current_app, "config", {}) or {}).get("STRIPE_SECRET_KEY", "")
    if not key or stripe is None:
        return False
    stripe.api_key = key
    return True


def _get_active_org_subscription(org_id: str) -> Optional[Dict[str, Any]]:
    """
    Busca una suscripción ACTIVA con metadata.org_id = org_id.
    """
    if not _stripe_init():
        return None
    try:
        subs = stripe.Subscription.search(
            query=f'metadata["org_id"]:"{org_id}" AND status:"active"'
        ).data
        return subs[0] if subs else None
    except Exception:
        return None


def _get_active_user_subscription(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Busca una suscripción ACTIVA con metadata.clerk_user_id = user_id (flujo Pro).
    """
    if not _stripe_init():
        return None
    try:
        subs = stripe.Subscription.search(
            query=f'metadata["clerk_user_id"]:"{user_id}" AND status:"active"'
        ).data
        return subs[0] if subs else None
    except Exception:
        return None


def _sub_seats(sub: Dict[str, Any]) -> int:
    try:
        items = (sub.get("items") or {}).get("data") or []
        if items:
            q = int(items[0].get("quantity") or 0)
            return max(0, q)
    except Exception:
        pass
    return 0


# ────────────────────────── Entitlements (idempotentes) ──────────────────────────

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
    except Exception:
        u = {}
    pub = (u.get("public_metadata") or {}) if isinstance(u, dict) else {}
    ent = pub.get("entitlement") or pub.get("plan")

    if _wants_enterprise_member(ent):
        return False  # ya correcto

    _update_user_metadata(user_id, public={**pub, "plan": "enterprise", "entitlement": "enterprise_member"})
    return True


def maybe_sync_current_user_entitlement(org_id: str) -> Optional[bool]:
    """
    Atajo para usar desde endpoints org: intenta subir el entitlement del usuario actual.
    No lanza excepciones; devuelve True si actualiza, False si no, None si no hay user.
    """
    uid = getattr(g, "user_id", None)
    if not uid:
        return None
    try:
        org = _get_org(org_id) or {}
    except Exception:
        org = {}
    try:
        return ensure_user_enterprise_member(uid, org)
    except Exception:
        return False


def _list_org_memberships(org_id: str) -> List[Dict[str, Any]]:
    """
    Listado simple de memberships usando la Admin API (requiere CLERK_SECRET_KEY).
    """
    if not CLERK_SECRET_KEY:
        return []
    try:
        data = _admin_api_get(f"/organizations/{org_id}/memberships?limit=200")
        return data if isinstance(data, list) else (data.get("data") or [])
    except Exception:
        return []


# ───────────────────── Sincronizaciones de alto nivel ─────────────────────

def _sync_org_seats_from_stripe(org_id: str) -> Tuple[bool, int]:
    """
    Lee seats desde Stripe (subscription.quantity) y los aplica en Clerk org.public_metadata.seats.
    Devuelve (actualizado, seats).
    """
    sub = _get_active_org_subscription(org_id)
    if not sub:
        # No hay sub activa → no tocamos seats (otra política posible: poner 0)
        return (False, 0)

    seats = _sub_seats(sub)
    # Marca la org como enterprise y actualiza seats
    updated_obj = _update_org_public_metadata(
        org_id,
        {"seats": seats, "subscription": "enterprise", "plan": "enterprise", "tier": "enterprise"},
    )
    updated = bool(updated_obj)
    return (updated, seats)


def sync_entitlements_for_org(org_id: str) -> Dict[str, int | bool]:
    """
    Recorre los miembros de la org y asegura enterprise_member para todos.
    Además, si hay Stripe, sincroniza seats (quantity) -> org.public_metadata.seats y marca plan=enterprise.
    Silencioso ante errores.
    Retorna: { org_is_enterprise, updated_members, total_members, seats, seats_updated }
    """
    try:
        org = _get_org(org_id) or {}
    except Exception:
        org = {}

    seats_updated = False
    seats = 0
    # Si hay Stripe, sincroniza seats primero (no bloquea si falla)
    try:
        ok, seats = _sync_org_seats_from_stripe(org_id)
        seats_updated = bool(ok)
        if ok:
            # refresca org local para que _org_is_enterprise lo detecte incluso si venía "free"
            org = _get_org(org_id) or org
    except Exception:
        pass

    if not _org_is_enterprise(org):
        return {"org_is_enterprise": False, "updated_members": 0, "total_members": 0, "seats": seats, "seats_updated": seats_updated}

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

    return {
        "org_is_enterprise": True,
        "updated_members": updated,
        "total_members": len(members),
        "seats": seats,
        "seats_updated": seats_updated,
    }


def sync_entitlements_for_user(user_id: str) -> Dict[str, Any]:
    """
    Flujo Pro (usuario individual):
      - Si hay suscripción activa con metadata.clerk_user_id == user_id → plan=pro / entitlement=pro
      - En caso contrario, no modificamos (evita degradar automáticamente).
    Retorna: { has_active_pro, updated }
    """
    has_active_pro = False
    updated = False

    try:
        sub = _get_active_user_subscription(user_id)
        has_active_pro = bool(sub)
    except Exception:
        has_active_pro = False

    if not has_active_pro:
        return {"has_active_pro": False, "updated": False}

    # set plan/entitlement pro si no estaban ya
    try:
        u = clerk_svc.get_user(user_id) or {}
    except Exception:
        u = {}
    pub = (u.get("public_metadata") or {}) if isinstance(u, dict) else {}
    ent = _norm_str(pub.get("entitlement") or pub.get("plan")).lower()

    if ent not in {"pro"}:
        _update_user_metadata(user_id, public={**pub, "plan": "pro", "entitlement": "pro"})
        updated = True

    return {"has_active_pro": True, "updated": updated}
