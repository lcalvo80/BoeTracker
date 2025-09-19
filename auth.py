# app/auth.py
from __future__ import annotations

import os
import time
import threading
from functools import wraps
from typing import Any, Dict, Optional

import requests
from flask import request, g, abort, current_app, has_app_context
from jose import jwt

# ───────────────────────── Config helpers ─────────────────────────

def _cfg(key: str, default: Optional[str] = None) -> str:
    """
    Lee primero de Flask config (si hay app_context), si no de ENV.
    """
    if has_app_context():
        val = current_app.config.get(key)
        if val is not None:
            return str(val)
    return str(os.getenv(key, default if default is not None else ""))

def _truthy(v: Any) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")

# ───────────────────────── JWKS cache (por URL) ─────────────────────────

class _JWKSCache:
    """
    Cache thread-safe por URL con TTL. Intenta revalidar si no encuentra el kid.
    """
    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, url: str, ttl: int = 3600) -> Dict[str, Any]:
        now = int(time.time())
        with self._lock:
            ent = self._store.get(url)
            if ent and now - ent["at"] < ttl:
                return ent["jwks"]
        jwks = self._fetch(url)
        with self._lock:
            self._store[url] = {"jwks": jwks, "at": now}
        return jwks

    def refresh(self, url: str) -> Dict[str, Any]:
        jwks = self._fetch(url)
        with self._lock:
            self._store[url] = {"jwks": jwks, "at": int(time.time())}
        return jwks

    @staticmethod
    def _fetch(url: str) -> Dict[str, Any]:
        timeout = float(_cfg("CLERK_JWKS_TIMEOUT", "5") or "5")
        try:
            res = requests.get(url, timeout=timeout)
            res.raise_for_status()
            data = res.json()
            if not isinstance(data, dict) or "keys" not in data:
                raise ValueError("JWKS payload missing 'keys'")
            return data
        except Exception as e:
            raise RuntimeError(f"Cannot fetch JWKS from {url}: {e}") from e

_JWKS = _JWKSCache()

# ───────────────────────── Token helpers ─────────────────────────

def _get_bearer_token() -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.split(" ", 1)[1].strip() or None
    return None

def _pick_key_for_kid(jwks: Dict[str, Any], kid: str) -> Optional[Dict[str, Any]]:
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            return k
    return None

def _decode_token(token: str, key: Dict[str, Any], leeway: int,
                  audience: Optional[str], issuer: Optional[str], alg: Optional[str]) -> Dict[str, Any]:
    options = {
        "verify_aud": bool(audience),  # desactiva aud si no hay audience
        "verify_iat": True,
        "verify_exp": True,
        "verify_nbf": True,
        "verify_iss": bool(issuer),
    }
    kwargs: Dict[str, Any] = {
        "key": key,
        "algorithms": [alg] if alg else ["RS256", "RS512", "ES256", "ES512"],
        "options": options,
        "leeway": leeway,
    }
    if audience:
        kwargs["audience"] = audience
    if issuer:
        kwargs["issuer"] = issuer
    return jwt.decode(token, **kwargs)

def _bypass_enabled() -> bool:
    # DISABLE_AUTH puede estar en config o env
    return _truthy(_cfg("DISABLE_AUTH", "0"))

# ───────────────────────── Decorator ─────────────────────────

def require_clerk_auth(fn):
    """
    Valida un JWT de Clerk usando JWKS, con cache y reintento ante kid desconocido.
    Si DISABLE_AUTH=1 → bypass (para diagnóstico/desarrollo).
    Expone en g.clerk: user_id, org_id, email, name.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Bypass
        if _bypass_enabled():
            g.clerk = {
                "user_id": "dev_user",
                "org_id": None,
                "email": "dev@example.com",
                "name": "Dev User",
            }
            return fn(*args, **kwargs)

        token = _get_bearer_token()
        if not token:
            abort(401, "Missing bearer token")

        jwks_url = _cfg("CLERK_JWKS_URL", "")
        if not jwks_url:
            abort(500, "CLERK_JWKS_URL not configured")

        # Parámetros opcionales
        leeway = int(_cfg("CLERK_LEEWAY", "30") or "30")
        audience = _cfg("CLERK_AUDIENCE", "") or None
        issuer = _cfg("CLERK_ISSUER", "") or None
        ttl = int(_cfg("CLERK_JWKS_TTL", "3600") or "3600")

        try:
            headers = jwt.get_unverified_header(token)
        except Exception as e:
            abort(401, f"Invalid token header: {e}")

        kid = headers.get("kid")
        alg = headers.get("alg", "RS256")
        if not kid:
            abort(401, "Missing kid header")

        # 1) JWKS desde cache (+refresh si no aparece el kid)
        try:
            jwks = _JWKS.get(jwks_url, ttl=ttl)
            key = _pick_key_for_kid(jwks, kid)
            if key is None:
                jwks = _JWKS.refresh(jwks_url)
                key = _pick_key_for_kid(jwks, kid)
            if key is None:
                abort(401, "Unknown token kid")
        except Exception as e:
            abort(503, f"JWKS fetch error: {e}")

        # 2) Decodificar y validar claims
        try:
            claims = _decode_token(token, key, leeway, audience, issuer, alg)
        except jwt.ExpiredSignatureError:
            abort(401, "Token expired")
        except jwt.JWTClaimsError as e:
            abort(401, f"Invalid claims: {e}")
        except Exception as e:
            abort(401, f"Invalid token: {e}")

        # 3) Mapear claims
        user_id = claims.get("sub") or claims.get("user_id")
        org_id = claims.get("org_id")
        email = claims.get("email") or claims.get("primary_email_address")
        name = claims.get("name") or claims.get("full_name")

        if not user_id:
            abort(401, "Missing user id in token")

        # 4) Sanear org_id si viene placeholder/valor inválido
        if isinstance(org_id, str):
            s = org_id.strip()
            if not s or s.startswith("{{"):
                org_id = None
            elif not s.startswith("org_"):  # quita si tu tenant no usa prefijo org_
                org_id = None

        # 5) Log básico para debug
        try:
            current_app.logger.info("AUTH claims: iss=%s sub=%s org_id=%s",
                                    claims.get("iss"), user_id, org_id)
        except Exception:
            pass

        g.clerk = {
            "user_id": user_id,
            "org_id": org_id,
            "email": email,
            "name": name,
            "raw_claims": claims if _truthy(_cfg("EXPOSE_CLAIMS_DEBUG", "0")) else None,
        }

        return fn(*args, **kwargs)
    return wrapper
