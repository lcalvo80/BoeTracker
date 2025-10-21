from __future__ import annotations

import time
from functools import wraps
from typing import Callable, Dict, Any, Optional

import jwt
from jwt import PyJWKClient
import requests
from flask import Blueprint, current_app, request, jsonify, g

# ───────────────── Utilidades JWT Clerk ─────────────────

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
    jwks_url = current_app.config.get("CLERK_JWKS_URL", "")
    issuer = (current_app.config.get("CLERK_ISSUER") or "").rstrip("/")
    options = {"verify_aud": False}  # normalmente no necesitamos 'aud' si usamos template backend
    if not jwks_url:
        # Modo permisivo si no hay JWKS (solo DEBUG). No recomendado en prod.
        if current_app.config.get("DEBUG"):
            return jwt.decode(token, options={"verify_signature": False})
        raise RuntimeError("CLERK_JWKS_URL no configurado")

    signing_key = _get_jwk_client(jwks_url).get_signing_key_from_jwt(token).key
    claims = jwt.decode(
        token,
        key=signing_key,
        algorithms=["RS256"],
        options=options,
        issuer=issuer if issuer else None,
    )
    return claims


def _normalize_g_from_claims(claims: Dict[str, Any]) -> None:
    """Rellena g.<...> con info útil para el backend."""
    g.clerk_claims = claims or {}

    # Campos típicos (depende del template de JWT)
    g.user_id = claims.get("sub") or claims.get("user_id")
    g.email = claims.get("email") or claims.get("user", {}).get("email_address")
    g.name = (
        claims.get("name")
        or (claims.get("user", {}) or {}).get("full_name")
        or ""
    )

    # Organización: priorizamos header X-Org-Id (frontend decide el scope)
    org_from_hdr = request.headers.get("X-Org-Id") or request.headers.get("x-org-id")
    claim_org_id = (
        claims.get("org_id")
        or claims.get("organization", {}).get("id")
        or claims.get("organization_id")
    )
    claim_org_role = (
        claims.get("org_role")
        or claims.get("organization_membership", {}).get("role")
        or claims.get("organization_role")
    )

    g.org_id = org_from_hdr or claim_org_id
    # Normalizamos role → 'admin' | 'member'
    raw_role = (claim_org_role or "").strip().lower()
    if raw_role in {"admin"}:
        g.org_role = "admin"
    elif raw_role in {"basic_member", "member"}:
        g.org_role = "member"
    else:
        g.org_role = None


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
                # En DEBUG devolvemos info de error explícita
                return jsonify({"ok": False, "error": f"Invalid token: {e}"}), 401
            return jsonify({"ok": False, "error": "Invalid token"}), 401

        _normalize_g_from_claims(claims)
        if not g.user_id:
            return jsonify({"ok": False, "error": "Invalid claims"}), 401
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


# ───────────────── Blueprint de INT/DEBUG ─────────────────

int_bp = Blueprint("int", __name__)


@int_bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)


@int_bp.route("/claims", methods=["GET", "OPTIONS"])
@require_auth
def debug_claims():
    """Centralizado en /api/_int/claims; sólo registrado en DEBUG desde create_app()."""
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
