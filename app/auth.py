from __future__ import annotations

from functools import wraps
from typing import Callable, Dict, Any, Optional

import jwt
from jwt import PyJWKClient
from flask import Blueprint, current_app, request, jsonify, g

# ───────────────── Placeholders Clerk ─────────────────
_PLACEHOLDER_STRINGS = {
    "organization.id",
    "organization_membership.role",
    "organization.slug",
    "user.id",
    "user.email_address",
    "user.primary_email_address",
}

def _is_placeholder(v: str) -> bool:
    if not isinstance(v, str):
        return False
    s = v.strip()
    s_low = s.lower()
    return (s_low.startswith("{{") and s_low.endswith("}}")) or (s_low in _PLACEHOLDER_STRINGS)


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

def decode_and_verify_clerk_jwt(token: str) -> Dict[str, Any]:
    """
    Verifica el JWT emitido por Clerk:
      - Si CLERK_JWKS_URL no está, en DEBUG aceptamos token sin firma (para dev local).
      - Si CLERK_AUDIENCE está configurado, se verifica aud; si no, se omite.
      - Si CLERK_ISSUER está configurado, se verifica issuer.
    """
    jwks_url = current_app.config.get("CLERK_JWKS_URL", "")
    issuer = (current_app.config.get("CLERK_ISSUER") or "").rstrip("/")
    audience = current_app.config.get("CLERK_AUDIENCE")  # opcional: str o coma-sep

    verify_aud = bool(audience)
    options = {
        "verify_aud": verify_aud,
    }

    if not jwks_url:
        if current_app.config.get("DEBUG"):
            # Modo permisivo SOLO en debug
            return jwt.decode(token, options={"verify_signature": False})
        raise RuntimeError("CLERK_JWKS_URL no configurado")

    signing_key = _get_jwk_client(jwks_url).get_signing_key_from_jwt(token).key

    kwargs: Dict[str, Any] = {
        "key": signing_key,
        "algorithms": ["RS256"],
        "options": options,
    }
    if issuer:
        kwargs["issuer"] = issuer
    if verify_aud:
        # soporta múltiples audiences separadas por coma
        aud_list = [a.strip() for a in str(audience).split(",") if a.strip()]
        kwargs["audience"] = aud_list if len(aud_list) > 1 else aud_list[0]

    claims = jwt.decode(token, **kwargs)
    return claims

def _normalize_g_from_claims(claims: Dict[str, Any]) -> None:
    """Rellena g con info normalizada y segura."""
    g.clerk_claims = claims or {}
    g.user_id = claims.get("sub") or claims.get("user_id")
    g.email = claims.get("email") or (claims.get("user") or {}).get("email_address")
    g.name = (
        claims.get("name")
        or (claims.get("user") or {}).get("full_name")
        or ""
    )

    # Header tiene prioridad y evita placeholders
    org_from_hdr = request.headers.get("X-Org-Id") or request.headers.get("x-org-id")

    claim_org_id = (
        claims.get("org_id")
        # algunos templates lo ponen anidado:
        or (claims.get("organization") or {}).get("id")
        or claims.get("organization_id")
    )
    claim_org_role = (
        claims.get("org_role")
        or (claims.get("organization_membership") or {}).get("role")
        or claims.get("organization_role")
    )

    g.org_id = org_from_hdr or (None if _is_placeholder(str(claim_org_id)) else claim_org_id)

    raw_role = ("" if _is_placeholder(str(claim_org_role)) else str(claim_org_role)).strip().lower()
    # Aceptamos equivalentes comunes
    if raw_role in {"admin", "org:admin", "owner"}:
        g.org_role = "admin"
    elif raw_role in {"basic_member", "member", "org:member"}:
        g.org_role = "member"
    else:
        g.org_role = None


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
        if not g.user_id:
            return jsonify({"ok": False, "error": "Invalid claims"}), 401
        return fn(*args, **kwargs)

    return _wrap


def require_org(fn: Callable) -> Callable:
    """
    Exige contexto de organización (pero no rol admin).
    Útil para endpoints tipo GET /enterprise/org o /enterprise/users.
    """
    @wraps(fn)
    def _wrap(*args, **kwargs):
        if not getattr(g, "org_id", None):
            return jsonify({"ok": False, "error": "Missing X-Org-Id or org in token"}), 400
        return fn(*args, **kwargs)
    return _wrap


def require_org_admin(fn: Callable) -> Callable:
    @wraps(fn)
    def _wrap(*args, **kwargs):
        if not getattr(g, "org_id", None):
            return jsonify({"ok": False, "error": "Missing X-Org-Id or org in token"}), 400
        if getattr(g, "org_role", None) != "admin":
            return jsonify({"ok": False, "error": "Admin role required"}), 403
        return fn(*args, **kwargs)

    return _wrap


# ───────────────── Blueprint INT (DEBUG) ─────────────────
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
            },
            "claims": getattr(g, "clerk_claims", {}),
        }
    )
