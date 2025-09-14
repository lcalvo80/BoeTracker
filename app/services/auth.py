# app/services/auth.py
from jose import jwt
import httpx
from flask import request, current_app, g
from functools import wraps
from werkzeug.exceptions import Unauthorized

_JWKS_CACHE = None

def _load_jwks():
    global _JWKS_CACHE
    if _JWKS_CACHE is None:
        jwks_url = current_app.config.get("CLERK_JWKS_URL")
        if not jwks_url:
            raise RuntimeError("CLERK_JWKS_URL no configurado")
        r = httpx.get(jwks_url, timeout=10)
        r.raise_for_status()
        _JWKS_CACHE = r.json()
    return _JWKS_CACHE

def require_auth():
    """Valida el Bearer JWT de Clerk y deja identidad en g.clerk"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                raise Unauthorized("Missing bearer token")
            token = auth.split(" ",1)[1]

            jwks = _load_jwks()
            header = jwt.get_unverified_header(token)
            key = next((k for k in jwks["keys"] if k["kid"] == header.get("kid")), None)
            if not key:
                raise Unauthorized("Invalid KID")
            try:
                payload = jwt.decode(
                    token=token,
                    key=key,
                    algorithms=["RS256"],
                    options={"verify_aud": False}
                )
            except Exception:
                raise Unauthorized("Invalid token")

            g.clerk = {
                "user_id": payload.get("sub"),
                "org_id": payload.get("org_id"),  # si pusiste claim en el JWT Template
                "plan": payload.get("plan"),      # si mapeaste plan en el JWT Template
            }
            return f(*args, **kwargs)
        return wrapper
    return decorator

def require_plan(required: str):
    """Gating backend rápido leyendo claim de plan en JWT.
       Si no viaja el claim, mejor consultar Admin API de Clerk (ver clerk_svc).
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            plan = (getattr(g, "clerk", {}) or {}).get("plan") or "free"
            ok = (
                (required == "free") or
                (required == "pro" and plan in {"pro","enterprise"}) or
                (required == "enterprise" and plan == "enterprise")
            )
            if not ok:
                raise Unauthorized(f"Plan {required} requerido")
            return f(*args, **kwargs)
        return wrapper
    return decorator
