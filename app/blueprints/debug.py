# app/blueprints/debug.py
from __future__ import annotations
import os
from flask import Blueprint, jsonify, request, g, current_app
from app.auth import require_clerk_auth

bp = Blueprint("debug", __name__, url_prefix="/api/debug")

@bp.get("/auth-config")
def auth_config():
    """No sensible data; solo para verificar que las envs están bien cargadas."""
    cfg = {
        "CLERK_ISSUER": os.getenv("CLERK_ISSUER", ""),
        "CLERK_JWKS_URL": os.getenv("CLERK_JWKS_URL", ""),
        "CLERK_AUDIENCE": os.getenv("CLERK_AUDIENCE", ""),
        "CLERK_LEEWAY": os.getenv("CLERK_LEEWAY", ""),
        "CLERK_JWKS_TTL": os.getenv("CLERK_JWKS_TTL", ""),
        "CLERK_JWKS_TIMEOUT": os.getenv("CLERK_JWKS_TIMEOUT", ""),
        "DISABLE_AUTH": os.getenv("DISABLE_AUTH", ""),
    }
    return jsonify(cfg), 200

@bp.get("/claims")
@require_clerk_auth
def claims():
    """Devuelve lo que el guard ha puesto en g.clerk."""
    authz = request.headers.get("Authorization", "")
    authz_short = (authz[:16] + "...") if authz else ""
    payload = {
        "g_clerk": {
            "user_id": getattr(g, "clerk", {}).get("user_id"),
            "org_id": getattr(g, "clerk", {}).get("org_id"),
            "email": getattr(g, "clerk", {}).get("email"),
            "name": getattr(g, "clerk", {}).get("name"),
        },
        "auth_header_present": bool(authz),
        "auth_header_prefix_ok": authz.startswith("Bearer "),
        "auth_header_sample": authz_short,
    }
    # Si quieres ver claims completos temporalmente:
    if (os.getenv("EXPOSE_CLAIMS_DEBUG", "0")).lower() in ("1", "true", "yes"):
        payload["raw_claims"] = getattr(g, "clerk", {}).get("raw_claims")
    return jsonify(payload), 200
