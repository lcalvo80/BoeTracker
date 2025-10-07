# app/blueprints/debug.py
from __future__ import annotations

import os
from flask import Blueprint, jsonify, request, g, current_app
from app.auth import require_clerk_auth

# Este blueprint se monta con su propio prefix:
bp = Blueprint("debug", __name__, url_prefix="/api/debug")


# ───────────────────────── helpers ─────────────────────────

def _safe_claims(raw: dict | None) -> dict:
    """
    Devuelve un subconjunto seguro y útil de las claims del JWT para inspeccionar
    si están llegando org_id y org_role. No incluye el token ni campos sensibles.
    """
    rc = dict(raw or {})
    keep: dict = {}

    # Campos comunes de Clerk
    for k in ("sub", "sid", "org_id", "org_role", "exp", "iat", "iss", "azp"):
        if k in rc:
            keep[k] = rc.get(k)

    # A veces Clerk anida info de organización en una subestructura "org"
    if isinstance(rc.get("org"), dict):
        org = rc["org"]
        if "id" in org and "org_id" not in keep:
            keep["org_id"] = org.get("id")
        if "role" in org and "org_role" not in keep:
            keep["org_role"] = org.get("role")

    return keep


def _derive_identity():
    """Valores calculados por el guard de auth y accesibles en g.clerk."""
    c = getattr(g, "clerk", {}) or {}
    raw_claims = c.get("raw_claims") or {}

    user_id = c.get("user_id")
    email = c.get("email")
    name = c.get("name")

    # org_id y org_role pueden venir en g.clerk o solo en raw_claims
    org_id = c.get("org_id") or raw_claims.get("org_id") or (raw_claims.get("org") or {}).get("id")
    org_role = c.get("org_role") or raw_claims.get("org_role") or (raw_claims.get("org") or {}).get("role")

    return user_id, email, name, org_id, org_role, raw_claims


# ───────────────────────── endpoints ─────────────────────────

@bp.get("/auth-config")
def auth_config():
    """
    Config pública para verificar integración con Clerk/flags (sin secretos).
    Útil para descartar problemas de env vars.
    """
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


@bp.get("/whoami")
@require_clerk_auth
def whoami():
    """
    Muestra lo que el backend está recibiendo del JWT de Clerk.
    Verifica si el token incluye org_id y org_role según la organización activa del cliente.
    """
    try:
        user_id, email, name, org_id, org_role, raw = _derive_identity()
        authz = request.headers.get("Authorization", "")
        return jsonify({
            "userId": user_id,
            "email": email,
            "name": name,
            "orgId": org_id,
            "orgRole": org_role,
            "claims": _safe_claims(raw),
            "authHeaderPresent": bool(authz),
            "authHeaderPrefixOk": authz.startswith("Bearer "),
        }), 200
    except Exception as e:
        current_app.logger.exception("[debug] whoami failed: %s", e)
        return jsonify(error="whoami failed", detail=str(e)), 500


@bp.get("/claims")
@require_clerk_auth
def claims():
    """
    Muestra lo que dejó auth en g.clerk para esta request y un subconjunto de claims.
    Si se activa EXPOSE_CLAIMS_DEBUG=1/true, adjunta también raw_claims completos.
    """
    authz = request.headers.get("Authorization", "")
    user_id, email, name, org_id, org_role, raw = _derive_identity()

    payload = {
        "g_clerk": {
            "user_id": user_id,
            "org_id": org_id,
            "org_role": org_role,
            "email": email,
            "name": name,
        },
        "claims_subset": _safe_claims(raw),
        "auth_header_present": bool(authz),
        "auth_header_prefix_ok": authz.startswith("Bearer "),
        "auth_header_sample": (authz[:20] + "...") if authz else "",
    }

    if (os.getenv("EXPOSE_CLAIMS_DEBUG", "0") or "").lower() in ("1", "true", "yes", "on"):
        payload["raw_claims"] = raw  # Úsalo solo en dev

    return jsonify(payload), 200


@bp.get("/routes")
def routes_list():
    """Lista las rutas registradas (útil para validar montaje de blueprints)."""
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
