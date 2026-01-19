# app/auth.py
from __future__ import annotations

from functools import wraps
from typing import Callable, Dict, Any, Optional, List, Tuple
import time
import threading

import requests
import jwt
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


# ───────────────── JWT / Clerk (JWKS cache robusto) ─────────────────

class _JwksCache:
    def __init__(self) -> None:
        self._jwks: Optional[Dict[str, Any]] = None
        self._exp: float = 0.0
        self._lock = threading.Lock()

    def get(self) -> Optional[Dict[str, Any]]:
        if self._jwks and time.time() < self._exp:
            return self._jwks
        return None

    def set(self, jwks: Dict[str, Any], ttl: int) -> None:
        self._jwks = jwks
        self._exp = time.time() + max(5, int(ttl))

    def clear(self) -> None:
        self._jwks = None
        self._exp = 0.0


_jwks_cache = _JwksCache()


def _get_bearer_token() -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    return auth.split(" ", 1)[1].strip() or None


def _aud_list_from_env(v: Optional[str]) -> List[str]:
    if not v:
        return []
    return [x.strip() for x in str(v).split(",") if x and x.strip()]


def _jwks_ttl_s() -> int:
    # TTL corto para producción durante estabilización (puedes subirlo luego)
    try:
        return int(current_app.config.get("CLERK_JWKS_CACHE_TTL_SECONDS", 300))
    except Exception:
        return 300


def _get_issuer_and_jwks_url() -> Tuple[str, str]:
    issuer = (current_app.config.get("CLERK_ISSUER") or "").strip().rstrip("/")
    jwks_url = (current_app.config.get("CLERK_JWKS_URL") or "").strip()

    if not issuer and not jwks_url:
        if current_app.config.get("DEBUG"):
            return "", ""
        raise RuntimeError("CLERK_ISSUER/CLERK_JWKS_URL no configurados")

    if not jwks_url:
        # Derivar JWKS del issuer
        jwks_url = f"{issuer}/.well-known/jwks.json"

    return issuer, jwks_url


def _fetch_jwks(jwks_url: str) -> Dict[str, Any]:
    r = requests.get(jwks_url, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or "keys" not in data:
        raise RuntimeError("Invalid JWKS payload")
    return data


def _get_jwks(*, force_refresh: bool = False) -> Dict[str, Any]:
    with _jwks_cache._lock:
        if force_refresh:
            _jwks_cache.clear()
        cached = _jwks_cache.get()
        if cached:
            return cached

        _, jwks_url = _get_issuer_and_jwks_url()
        jwks = _fetch_jwks(jwks_url)
        _jwks_cache.set(jwks, _jwks_ttl_s())
        return jwks


def _select_jwk(jwks: Dict[str, Any], kid: str) -> Optional[Dict[str, Any]]:
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            return k
    return None


def decode_and_verify_clerk_jwt(token: str) -> Dict[str, Any]:
    issuer, jwks_url = _get_issuer_and_jwks_url()
    audience_env = current_app.config.get("CLERK_AUDIENCE")
    audiences = _aud_list_from_env(audience_env)

    # En DEBUG permitimos inspección sin firma si NO hay jwks_url, pero en prod no.
    if current_app.config.get("DEBUG") and not jwks_url:
        return jwt.decode(token, options={"verify_signature": False})

    try:
        header = jwt.get_unverified_header(token)
    except Exception:
        raise RuntimeError("malformed_header")

    kid = header.get("kid")
    if not kid:
        raise RuntimeError("missing_kid")

    # 1) Intento con cache
    jwks = _get_jwks(force_refresh=False)
    jwk = _select_jwk(jwks, kid)

    # 2) Si no está el kid, refrescamos y reintentamos 1 vez
    if not jwk:
        current_app.logger.warning("[auth] kid not in cached JWKS; refreshing. kid=%s jwks_url=%s", kid, jwks_url)
        jwks = _get_jwks(force_refresh=True)
        jwk = _select_jwk(jwks, kid)

    if not jwk:
        raise RuntimeError(f'Unable to find a signing key that matches: "{kid}"')

    try:
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
    except Exception:
        raise RuntimeError("cannot_build_public_key")

    options = {"verify_aud": bool(audiences)}
    kwargs: Dict[str, Any] = {
        "key": public_key,
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
    g.name = _safe_str(claims.get("name")) or _safe_str((claims.get("user") or {}).get("full_name")) or ""

    extracted = _extract_org_from_claims(claims)
    token_org_id = extracted["org_id"]
    token_org_role = extracted["org_role"]
    token_org_slug = extracted["org_slug"]

    org_from_hdr = _safe_str(request.headers.get("X-Org-Id") or request.headers.get("x-org-id"))

    # Seguridad MVP: si token trae org y el header trae otra -> mismatch
    if token_org_id and org_from_hdr and org_from_hdr != token_org_id:
        g.org_id = token_org_id
        g.org_role = token_org_role
        g.org_slug = token_org_slug
        g._org_mismatch = {"token_org_id": token_org_id, "header_org_id": org_from_hdr}
        return

    g.org_id = token_org_id or org_from_hdr
    g.org_role = token_org_role
    g.org_slug = token_org_slug


# ───────────────── Fallback server-to-server con Clerk ─────────────────
def _clerk_is_org_admin(org_id: str, user_id: str) -> bool:
    base = (current_app.config.get("CLERK_API_BASE") or "https://api.clerk.com/v1").rstrip("/")
    sk = current_app.config.get("CLERK_SECRET_KEY", "")
    if not sk:
        return False
    try:
        r = requests.get(
            f"{base}/organizations/{org_id}/memberships",
            headers={"Authorization": f"Bearer {sk}"},
            params={"limit": 200},
            timeout=20,
        )
        if r.status_code >= 400:
            current_app.logger.warning("Clerk memberships %s -> %s %s", org_id, r.status_code, r.text)
            return False
        data = r.json().get("data", [])
        for m in data:
            uid = m.get("user_id") or (m.get("public_user_data") or {}).get("user_id")
            role = (m.get("role") or "").strip().lower()
            if uid == user_id and role in {"admin", "org:admin", "owner"}:
                return True
    except Exception:
        current_app.logger.exception("clerk_is_org_admin error")
    return False


# ───────────────── Suscripción activa (Stripe live) ─────────────────
def _sub_cache_ttl_s() -> int:
    try:
        return max(5, int(current_app.config.get("SUB_CACHE_TTL_S", 60)))
    except Exception:
        return 60

_sub_cache: Dict[str, Tuple[float, bool, Dict[str, Any]]] = {}
_sub_locks: Dict[str, threading.Lock] = {}

def _sub_cache_get(key: str) -> Optional[Tuple[bool, Dict[str, Any]]]:
    hit = _sub_cache.get(key)
    if not hit:
        return None
    ts, ok, meta = hit
    if (time.time() - ts) > _sub_cache_ttl_s():
        _sub_cache.pop(key, None)
        return None
    return ok, meta

def _sub_cache_set(key: str, ok: bool, meta: Dict[str, Any]) -> None:
    _sub_cache[key] = (time.time(), bool(ok), meta or {})

def _lock_for(key: str) -> threading.Lock:
    lk = _sub_locks.get(key)
    if lk is None:
        lk = threading.Lock()
        _sub_locks[key] = lk
    return lk

def _is_active_from_summary(summary: Any) -> Tuple[bool, Dict[str, Any]]:
    if summary is None:
        return False, {"reason": "no_summary"}
    if not isinstance(summary, dict):
        return False, {"reason": "unknown_format", "type": str(type(summary))}

    status = (summary.get("status") or summary.get("subscription_status") or "").strip().lower()
    plan = (summary.get("plan") or summary.get("tier") or summary.get("entitlement") or "").strip().lower()

    if isinstance(summary.get("is_active"), bool):
        return (summary["is_active"] is True), {"reason": "is_active_flag", "status": status, "plan": plan}

    if isinstance(summary.get("active"), bool):
        return (summary["active"] is True), {"reason": "active_flag", "status": status, "plan": plan}

    if status in {"active", "trialing"} and plan not in {"", "free", "none", "basic"}:
        return True, {"reason": "status_plan", "status": status, "plan": plan}

    return False, {"reason": "not_active", "status": status, "plan": plan}

def _check_user_subscription_live(user_id: str, email: Optional[str]) -> Tuple[bool, Dict[str, Any]]:
    cache_key = f"user:{user_id}"
    cached = _sub_cache_get(cache_key)
    if cached:
        ok, meta = cached
        return ok, {**meta, "cached": True}

    lk = _lock_for(cache_key)
    with lk:
        cached2 = _sub_cache_get(cache_key)
        if cached2:
            ok, meta = cached2
            return ok, {**meta, "cached": True}

        try:
            from app.services import stripe_svc
            summary = stripe_svc.get_billing_summary_v1_for_user(user_id=user_id, email=email)
            ok, meta = _is_active_from_summary(summary)
            _sub_cache_set(cache_key, ok, meta)
            return ok, meta
        except Exception as e:
            current_app.logger.warning("[auth] user subscription check failed: %s", e)
            _sub_cache_set(cache_key, False, {"reason": "exception"})
            return False, {"reason": "exception"}

def _check_org_subscription_live(org_id: str) -> Tuple[bool, Dict[str, Any]]:
    cache_key = f"org:{org_id}"
    cached = _sub_cache_get(cache_key)
    if cached:
        ok, meta = cached
        return ok, {**meta, "cached": True}

    lk = _lock_for(cache_key)
    with lk:
        cached2 = _sub_cache_get(cache_key)
        if cached2:
            ok, meta = cached2
            return ok, {**meta, "cached": True}

        try:
            from app.services import stripe_svc
            summary = stripe_svc.get_billing_summary_v1_for_org(org_id=org_id)
            ok, meta = _is_active_from_summary(summary)
            _sub_cache_set(cache_key, ok, meta)
            return ok, meta
        except Exception as e:
            current_app.logger.warning("[auth] org subscription check failed: %s", e)
            _sub_cache_set(cache_key, False, {"reason": "exception"})
            return False, {"reason": "exception"}


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
            # En prod, devuelve motivo acotado pero útil
            msg = str(e)
            current_app.logger.warning("[auth] invalid token: %s", msg)
            if current_app.config.get("DEBUG") or current_app.config.get("AUTH_DEBUG"):
                return jsonify({"ok": False, "error": f"Invalid token: {msg}"}), 401
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
    @wraps(fn)
    def _wrap(*args, **kwargs):
        if not bool(current_app.config.get("REQUIRE_ACTIVE_SUBSCRIPTION", False)):
            g.subscription = {"scope": "none", "bypass": True, "reason": "subscriptions_disabled"}
            return fn(*args, **kwargs)

        user_id = getattr(g, "user_id", None)
        if not user_id:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

        org_id = getattr(g, "org_id", None)

        if org_id:
            ok, meta = _check_org_subscription_live(org_id)
            if ok:
                g.subscription = {"scope": "org", "org_id": org_id, **meta}
                return fn(*args, **kwargs)

            payload = {"ok": False, "error": "subscription_required", "scope": "org", "org_id": org_id}
            if current_app.config.get("DEBUG") or current_app.config.get("DEBUG_SUBSCRIPTION"):
                payload["debug"] = meta
            return jsonify(payload), 403

        ok, meta = _check_user_subscription_live(user_id, getattr(g, "email", None))
        if ok:
            g.subscription = {"scope": "user", "user_id": user_id, **meta}
            return fn(*args, **kwargs)

        payload = {"ok": False, "error": "subscription_required", "scope": "user"}
        if current_app.config.get("DEBUG") or current_app.config.get("DEBUG_SUBSCRIPTION"):
            payload["debug"] = meta
        return jsonify(payload), 403
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
                "subscription": getattr(g, "subscription", None),
            },
            "claims": getattr(g, "clerk_claims", {}),
        }
    )
