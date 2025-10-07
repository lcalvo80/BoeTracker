# app/blueprints/debug.py
from __future__ import annotations

import os
from flask import Blueprint, jsonify, request, g, current_app
from app.auth import require_clerk_auth

# Este blueprint se monta con su propio prefix:
bp = Blueprint("debug", __name__, url_prefix="/api/debug")


@bp.get("/auth-config")
def auth_config():
    """Config pública para verificar integración con Clerk/flags (sin secretos)."""
    cfg = {
        "CLERK_ISSUER": os.getenv("CLERK_ISSUER", ""),
        "CLERK_JWKS_URL": os.getenv("CLERK_JWKS_URL", ""),
        "CLERK_AUDIENCE": os.getenv("CLERK_AUDIENCE", ""),
        "CLERK_LEEWAY": os.getenv("CLERK_LEEWAY", ""),
        "CLERK_JWKS_TTL": os.getenv("CLERK_JWKS_TTL", ""),
        "CLERK_JWKS_TIMEOUT": os.getenv("CLERK_JWKS_TIMEOUT", ""),
        "DISABLE_AUTH": os.getenv("DISABLE_AUTH", ""),
        "ENV": os.getenv("ENV", ""),
        "FLASK_ENV": os.getenv("FLASK_ENV", ""),
    }
    return jsonify(cfg), 200


@bp.get("/claims")
@require_clerk_auth
def claims():
    """Muestra lo que dejó auth en g.clerk para esta request."""
    authz = request.headers.get("Authorization", "")
    authz_short = (authz[:20] + "...") if authz else ""
    payload = {
        "g_clerk": {
            "user_id": getattr(g, "clerk", {}).get("user_id"),
            "org_id": getattr(g, "clerk", {}).get("org_id"),
            "org_role": getattr(g, "clerk", {}).get("org_role"),
            "email": getattr(g, "clerk", {}).get("email"),
            "name": getattr(g, "clerk", {}).get("name"),
        },
        "auth_header_present": bool(authz),
        "auth_header_prefix_ok": authz.startswith("Bearer "),
        "auth_header_sample": authz_short,
    }
    if (os.getenv("EXPOSE_CLAIMS_DEBUG", "0")).lower() in ("1", "true", "yes"):
        payload["raw_claims"] = getattr(g, "clerk", {}).get("raw_claims")
    return jsonify(payload), 200


@bp.get("/routes")
def routes_list():
    """Lista las rutas registradas."""
    rules = []
    for r in current_app.url_map.iter_rules():
        methods = sorted(m for m in r.methods if m in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
        rules.append({"endpoint": r.endpoint, "methods": methods, "rule": str(r.rule)})
    rules.sort(key=lambda x: x["rule"])
    return jsonify(rules), 200


@bp.route("/echo", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def echo():
    """Echo útil para probar CORS/headers/métodos rápidamente."""
    return jsonify({
        "method": request.method,
        "path": request.path,
        "headers": {k: v for k, v in request.headers.items()},
        "json": request.get_json(silent=True),
        "args": request.args.to_dict(flat=True),
    }), 200
