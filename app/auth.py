# app/auth.py
from __future__ import annotations

from functools import wraps
from typing import Callable, Dict, Any, Optional, List, Tuple
import time

import requests
import jwt
from jwt import PyJWKClient
from flask import Blueprint, current_app, request, jsonify, g

# ───────────────── Placeholders de Clerk (evitar falsos positivos) ─────────────────
_PLACEHOLDER_STRINGS = {
    "organization.id",
    "organization_membership.role",
    "organization.slug",
    "user.id",
    "user.email_address",
    "user.primary_email_address",
}

def _is_placeholder(v: Any) -> bool:
    if not isinstance(v, str):
        return False
    s = v.strip()
    s_low = s.lower()
    return (s_low.startswith("{{") and s_low.endswith("}}")) or (s_low in _PLACEHOLDER_STRINGS)

def _safe_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    try:
        s = str(v).strip()
        if not s:
            return None
        if _is_placeholder(s):
            return None
        return s
    except Exception:
        return None

def _lower(s: Optional[str]) -> Optional[str]:
    return s.lower() if isinstance(s, str) else None

# ───────────────── JWT / Clerk ─────────────────
_jwk_client_cache: Dict[str, PyJWKClient] = {}

def _get_bearer_token() -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    return auth.split(" ", 1)[1].strip() or None

def _get_jwk_client(jwks_url: str) -> PyJWKClient:
    client = _jwk_client_cache.get(jwks_url)
    if client is None:
        client = PyJWKClient(jwks_url)
        _jwk_client_cache[jwks_url] = client
    return client

def _aud_list_from_env(v: Optional[str]) -> List[str]:
    if not v:
        return []
    return [x.strip() for x in str(v).split(",") if x and x.strip()]

def decode_and_verify_clerk_jwt(token: str) -> Dict[str, Any]:
    jwks_url = (current_app.config.get("CLERK_JWKS_URL") or "").strip()
    issuer   = (current_app.config.get("CLERK_ISSUER") or "").rstrip("/")
    audience_env = current_app.config.get("CLERK_AUDIENCE")
    audiences = _aud_list_from_env(audience_env)

    if not jwks_url:
        if current_app.config.get("DEBUG"):
            return jwt.decode(token, options={"verify_signature": False})
        raise RuntimeError("CLERK_JWKS_URL no configurado")

    signing_key = _get_jwk_client(jwks_url).get_signing_key_from_jwt(token).key

    options = {"verify_aud": bool(audiences)}
    kwargs: Dict[str, Any] = {
        "key": signing_key,
        "algorithms": ["RS256"],
        "options": options,
        "leeway": 10,
    }
    if issuer:
        kwargs["issuer"] = issuer
    if audiences:
        kwargs["audience"] = audiences if len(audiences) > 1 else audiences[0]

    return jwt.decode(token, **kwargs)

def _extract_org_from_claims(claims: Dict[str, Any]) -> Dict[str, Optional[str]]:
    o = claims.get("o") if isinstance(claims.get("o"), dict) else {}

    org_id = (
        _safe_str(o.get("id"))
        or _safe_str(claims.get("org_id"))
        or _safe_str((claims.get("organization") or {}).get("id"))
        or _safe_str(claims.get("organization_id"))
    )

    org_role = (
        _safe_str(o.get("rol"))
        or _safe_str(claims.get("org_role"))
        or _safe_str((claims.get("organization_membership") or {}).get("role"))
        or _safe_str(claims.get("organization_role"))
    )

    org_slug = (
        _safe_str(o.get("slg"))
        or _safe_str(claims.get("org_slug"))
        or _safe_str((claims.get("organization") or {}).get("slug"))
    )

    raw_role = (org_role or "").strip().lower()
    if raw_role in {"admin", "org:admin", "owner"}:
        norm_role = "admin"
    elif raw_role in {"basic_member", "member", "org:member"}:
        norm_role = "member"
    else:
        norm_role = None

    return {"org_id": org_id, "org_role": norm_role, "org_slug": org_slug}

def _normalize_g_from_claims(claims: Dict[str, Any]) -> None:
    g.clerk_claims = claims or {}

    g.user_id = _safe_str(claims.get("sub")) or _safe_str(claims.get("user_id"))
    g.email = _safe_str(claims.get("email")) or _safe_str((claims.get("user") or {}).get("email_address"))
    g.name = (
        _safe_str(claims.get("name"))
        or _safe_str((claims.get("user") or {}).get("full_name"))
        or ""
    )

    extracted = _extract_org_from_claims(claims)
    token_org_id = extracted["org_id"]
    token_org_role = extracted["org_role"]
    token_org_slug = extracted["org_slug"]

    org_from_hdr = _safe_str(request.headers.get("X-Org-Id") or request.headers.get("x-org-id"))

    if token_org_id and org_from_hdr and org_from_hdr != token_org_id:
        g.org_id = token_org_id
        g.org_role = token_org_role
        g.org_slug = token_org_slug
        g._org_mismatch = {"token_org_id": token_org_id, "header_org_id": org_from_hdr}
        return

    g.org_id = token_org_id or org_from_hdr
    g.org_role = token_org_role
    g.org_slug = token_org_slug

# ───────────────── Clerk server-to-server helpers ─────────────────

def _clerk_api_base() -> str:
    return (current_app.config.get("CLERK_API_BASE") or "https://api.clerk.com/v1").rstrip("/")

def _clerk_secret_key() -> str:
    return (current_app.config.get("CLERK_SECRET_KEY") or "").strip()

def _clerk_get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    sk = _clerk_secret_key()
    if not sk:
        return None
    base = _clerk_api_base()
    try:
        r = requests.get(
            f"{base}{path}",
            headers={"Authorization": f"Bearer {sk}"},
            params=params or {},
            timeout=20,
        )
        if r.status_code >= 400:
            current_app.logger.warning("Clerk GET %s -> %s %s", path, r.status_code, r.text)
            return None
        return r.json()
    except Exception:
        current_app.logger.exception("Clerk GET failed: %s", path)
        return None

def _clerk_is_org_admin(org_id: str, user_id: str) -> bool:
    data = _clerk_get(f"/organizations/{org_id}/memberships", params={"limit": 200})
    if not data:
        return False
    for m in data.get("data", []) or []:
        uid = m.get("user_id") or (m.get("public_user_data") or {}).get("user_id")
        role = (m.get("role") or "").strip().lower()
        if uid == user_id and role in {"admin", "org:admin", "owner"}:
            return True
    return False

# ───────────────── Subscription gating ─────────────────

# Cache TTL corto para no pedir a Clerk en cada request
_SUB_CACHE: Dict[Tuple[str, Optional[str]], Tuple[float, bool, str]] = {}
_SUB_CACHE_TTL_S = 45.0

def _pick_meta(obj: dict) -> dict:
    if not isinstance(obj, dict):
        return {}
    # Clerk suele exponer: public_metadata / private_metadata / unsafe_metadata
    return {
        "public": obj.get("public_metadata") if isinstance(obj.get("public_metadata"), dict) else {},
        "private": obj.get("private_metadata") if isinstance(obj.get("private_metadata"), dict) else {},
        "unsafe": obj.get("unsafe_metadata") if isinstance(obj.get("unsafe_metadata"), dict) else {},
    }

def _meta_get(meta_bundle: dict, *keys: str) -> Optional[Any]:
    for space in ("public", "private", "unsafe"):
        d = meta_bundle.get(space) or {}
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
    return None

def _normalize_plan(val: Any) -> Optional[str]:
    s = _safe_str(val)
    if not s:
        return None
    s = s.strip().lower()
    # normalización mínima
    if s in {"pro", "premium"}:
        return "pro"
    if s in {"enterprise", "ent"}:
        return "enterprise"
    if s in {"free", "basic"}:
        return "free"
    return s

def _is_active_status(val: Any) -> bool:
    s = _safe_str(val)
    if not s:
        return False
    s = s.strip().lower()
    return s in {"active", "trialing", "trial", "paid"}  # MVP: tratamos trial como activo

def _check_active_subscription(user_id: str, org_id: Optional[str]) -> Tuple[bool, str]:
    """
    Regla MVP (robusta a cómo guardes metadata):
      - Activo si plan efectivo es pro o enterprise
      - O si hay status activo/trialing en metadata (user u org)
    """
    cache_key = (user_id, org_id)
    now = time.time()
    cached = _SUB_CACHE.get(cache_key)
    if cached and (now - cached[0] < _SUB_CACHE_TTL_S):
        return cached[1], cached[2]

    if not _clerk_secret_key():
        # Si no hay secret key, no podemos verificar suscripción con Clerk.
        # En producción es configuración obligatoria para gating.
        _SUB_CACHE[cache_key] = (now, False, "server_not_configured")
        return False, "server_not_configured"

    user = _clerk_get(f"/users/{user_id}")
    if not user:
        _SUB_CACHE[cache_key] = (now, False, "user_not_found")
        return False, "user_not_found"

    user_meta = _pick_meta(user)
    user_plan = _normalize_plan(_meta_get(user_meta, "plan", "subscription_plan", "entitlement"))
    user_status = _meta_get(user_meta, "subscription_status", "status", "subscriptionStatus")

    org_plan = None
    org_status = None
    if org_id:
        org = _clerk_get(f"/organizations/{org_id}")
        if org:
            org_meta = _pick_meta(org)
            org_plan = _normalize_plan(_meta_get(org_meta, "plan", "subscription_plan", "entitlement"))
            org_status = _meta_get(org_meta, "subscription_status", "status", "subscriptionStatus")

    # “Plan efectivo”: enterprise manda si existe
    effective_plan = org_plan or user_plan
    is_active = False

    if effective_plan in {"pro", "enterprise"}:
        is_active = True
    if _is_active_status(user_status) or _is_active_status(org_status):
        is_active = True

    reason = "active" if is_active else "subscription_required"
    _SUB_CACHE[cache_key] = (now, is_active, reason)
    return is_active, reason

# ───────────────── Decoradores ─────────────────

def require_auth(fn: Callable) -> Callable:
    @wraps(fn)
    def _wrap(*args, **kwargs):
        token = _get_bearer_token()
        if not token:
            return jsonify({"ok": False, "error": "Missing Bearer token"}), 401
        try:
            claims = decode_and_verify_clerk_jwt(token)
        except Exception as e:
            if current_app.config.get("DEBUG"):
                return jsonify({"ok": False, "error": f"Invalid token: {e}"}), 401
            return jsonify({"ok": False, "error": "Invalid token"}), 401

        _normalize_g_from_claims(claims)
        if not getattr(g, "user_id", None):
            return jsonify({"ok": False, "error": "Invalid claims"}), 401

        mismatch = getattr(g, "_org_mismatch", None)
        if mismatch:
            return jsonify({"ok": False, "error": "org_mismatch", **mismatch}), 403

        return fn(*args, **kwargs)
    return _wrap

def require_org(fn: Callable) -> Callable:
    @wraps(fn)
    def _wrap(*args, **kwargs):
        if not getattr(g, "org_id", None):
            return jsonify({"ok": False, "error": "Missing X-Org-Id or org in token"}), 400
        return fn(*args, **kwargs)
    return _wrap

def require_org_admin(fn: Callable) -> Callable:
    @wraps(fn)
    def _wrap(*args, **kwargs):
        org_id = getattr(g, "org_id", None)
        if not org_id:
            return jsonify({"ok": False, "error": "Missing X-Org-Id or org in token"}), 400

        if getattr(g, "org_role", None) == "admin":
            return fn(*args, **kwargs)

        user_id = getattr(g, "user_id", None)
        if user_id and _clerk_is_org_admin(org_id, user_id):
            g.org_role = "admin"
            return fn(*args, **kwargs)

        return jsonify({"ok": False, "error": "Admin role required"}), 403
    return _wrap

def require_active_subscription(fn: Callable) -> Callable:
    """
    Exige que el usuario esté suscrito (Pro o Enterprise).
    Si hay org activa, acepta plan enterprise en org.
    """
    @wraps(fn)
    def _wrap(*args, **kwargs):
        user_id = getattr(g, "user_id", None)
        if not user_id:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

        org_id = getattr(g, "org_id", None)
        ok, reason = _check_active_subscription(user_id, org_id)

        if not ok:
            # 402 = Payment Required (semánticamente útil para FE)
            return jsonify({"ok": False, "error": "subscription_required", "reason": reason}), 402

        g.subscription_ok = True
        return fn(*args, **kwargs)
    return _wrap

# ───────────────── Blueprint INT (solo DEBUG) ─────────────────
int_bp = Blueprint("int", __name__)

@int_bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)

@int_bp.route("/claims", methods=["GET", "OPTIONS"])
@require_auth
def debug_claims():
    return jsonify(
        {
            "ok": True,
            "auth_header_present": bool(_get_bearer_token()),
            "g": {
                "user_id": getattr(g, "user_id", None),
                "email": getattr(g, "email", None),
                "name": getattr(g, "name", None),
                "org_id": getattr(g, "org_id", None),
                "org_role": getattr(g, "org_role", None),
                "org_slug": getattr(g, "org_slug", None),
                "subscription_ok": getattr(g, "subscription_ok", None),
            },
            "claims": getattr(g, "clerk_claims", {}),
        }
    )
