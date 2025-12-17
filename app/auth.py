# app/auth.py
from __future__ import annotations

from functools import wraps
from typing import Callable, Dict, Any, Optional, List

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
    """
    Convierte CLERK_AUDIENCE (p. ej. 'boe-backend' o 'a,b,c') en lista depurada.
    """
    if not v:
        return []
    return [x.strip() for x in str(v).split(",") if x and x.strip()]

def decode_and_verify_clerk_jwt(token: str) -> Dict[str, Any]:
    """
    Verifica el JWT emitido por Clerk.

    - Si CLERK_JWKS_URL NO está definido:
        * En DEBUG: acepta token sin firma (solo desarrollo local).
        * En no-DEBUG: error de configuración.
    - Si CLERK_AUDIENCE está configurado, verifica 'aud'.
    - Si CLERK_ISSUER está configurado, verifica 'iss'.
    """
    jwks_url = (current_app.config.get("CLERK_JWKS_URL") or "").strip()
    issuer   = (current_app.config.get("CLERK_ISSUER") or "").rstrip("/")
    audience_env = current_app.config.get("CLERK_AUDIENCE")
    audiences = _aud_list_from_env(audience_env)

    if not jwks_url:
        if current_app.config.get("DEBUG"):
            return jwt.decode(token, options={"verify_signature": False})
        raise RuntimeError("CLERK_JWKS_URL no configurado")

    signing_key = _get_jwk_client(jwks_url).get_signing_key_from_jwt(token).key

    options = {
        "verify_aud": bool(audiences),
    }

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

    claims = jwt.decode(token, **kwargs)
    return claims


def _extract_org_from_claims(claims: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Extrae org_id/org_role/org_slug soportando:
    - Clerk session claims v2: claims["o"] = { id, rol, slg }
    - Claims planos legacy: org_id, org_role, org_slug
    - Estructuras anidadas viejas: organization.id, organization_membership.role
    """
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

    # Normaliza rol a admin/member
    raw_role = (org_role or "").strip().lower()
    if raw_role in {"admin", "org:admin", "owner"}:
        norm_role = "admin"
    elif raw_role in {"basic_member", "member", "org:member"}:
        norm_role = "member"
    else:
        norm_role = None

    return {"org_id": org_id, "org_role": norm_role, "org_slug": org_slug}


def _normalize_g_from_claims(claims: Dict[str, Any]) -> None:
    """
    Rellena flask.g con info normalizada y segura.
    Regla de seguridad MVP:
      - Si llega X-Org-Id y NO coincide con la org del token, 403 (org_mismatch).
      - Si el token no trae org, permitimos header (para flows puntuales),
        pero require_org se encargará de exigir org cuando aplique.
    """
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

    # Header org (hint del FE)
    org_from_hdr = _safe_str(request.headers.get("X-Org-Id") or request.headers.get("x-org-id"))

    # Seguridad: si token trae org y header trae otra, es mismatch
    if token_org_id and org_from_hdr and org_from_hdr != token_org_id:
        # Guardamos para debugging interno si lo necesitas
        g.org_id = token_org_id
        g.org_role = token_org_role
        g.org_slug = token_org_slug
        g._org_mismatch = {"token_org_id": token_org_id, "header_org_id": org_from_hdr}
        # No lanzamos aquí excepción; el decorator responderá.
        return

    # Si no hay mismatch: org_id efectiva = token_org_id o header (si token no trae)
    g.org_id = token_org_id or org_from_hdr
    g.org_role = token_org_role
    g.org_slug = token_org_slug


# ───────────────── Fallback server-to-server con Clerk ─────────────────
def _clerk_is_org_admin(org_id: str, user_id: str) -> bool:
    """
    Comprueba en Clerk si user_id es admin de org_id (server-to-server).
    Requiere CLERK_SECRET_KEY (y opcionalmente CLERK_API_BASE).
    """
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

        # Si hubo mismatch, bloqueamos aquí (regla MVP)
        mismatch = getattr(g, "_org_mismatch", None)
        if mismatch:
            return jsonify({"ok": False, "error": "org_mismatch", **mismatch}), 403

        return fn(*args, **kwargs)
    return _wrap


def require_org(fn: Callable) -> Callable:
    """
    Exige contexto de organización (pero no rol admin).
    """
    @wraps(fn)
    def _wrap(*args, **kwargs):
        if not getattr(g, "org_id", None):
            return jsonify({"ok": False, "error": "Missing X-Org-Id or org in token"}), 400
        return fn(*args, **kwargs)
    return _wrap


def require_org_admin(fn: Callable) -> Callable:
    """
    Requiere rol admin en la organización:
    - Si el token trae org_role=admin, OK.
    - Si no, fallback consultando a Clerk (server-to-server).
    """
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


# ───────────────── Blueprint INT (solo DEBUG) ─────────────────
int_bp = Blueprint("int", __name__)

@int_bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)

@int_bp.route("/claims", methods=["GET", "OPTIONS"])
@require_auth
def debug_claims():
    """
    Endpoint de diagnóstico (solo DEBUG).
    """
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
            },
            "claims": getattr(g, "clerk_claims", {}),
        }
    )
