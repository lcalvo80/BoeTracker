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

def _cfg(key: str, default: Optional[str] = None) -> str:
    if has_app_context():
        try:
            val = current_app.config.get(key)
            if val is not None:
                return str(val)
        except Exception:
            pass
    return str(os.getenv(key, default if default is not None else ""))

def _truthy(v: Any) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")

class _JWKSCache:
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
        res = requests.get(url, timeout=timeout)
        res.raise_for_status()
        data = res.json()
        if not isinstance(data, dict) or "keys" not in data:
            raise ValueError("JWKS payload missing 'keys'")
        return data

_JWKS = _JWKSCache()

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

def _decode_token(token: str, key: Dict[str, Any], leeway: int, audience: Optional[str], issuer: Optional[str], alg: Optional[str]) -> Dict[str, Any]:
    options = {
        "verify_aud": bool(audience),
        "verify_iat": True, "verify_exp": True, "verify_nbf": True,
        "verify_iss": bool(issuer),
    }
    kwargs: Dict[str, Any] = {
        "key": key,
        "algorithms": [alg] if alg else ["RS256", "RS512", "ES256", "ES512"],
        "options": options,
        "leeway": leeway,
    }
    if audience: kwargs["audience"] = audience
    if issuer: kwargs["issuer"] = issuer
    return jwt.decode(token, **kwargs)

def _bypass_enabled() -> bool:
    return _truthy(_cfg("DISABLE_AUTH", "0"))

def require_clerk_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if _bypass_enabled():
            g.clerk = {"user_id": "dev_user","org_id": None,"email": "dev@example.com","name": "Dev User","raw_claims": None}
            return fn(*args, **kwargs)

        token = _get_bearer_token()
        if not token:
            abort(401, "Missing bearer token")

        jwks_url = _cfg("CLERK_JWKS_URL", "")
        if not jwks_url:
            abort(500, "CLERK_JWKS_URL not configured")

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

        try:
            jwks = _JWKS.get(jwks_url, ttl=ttl)
            key = _pick_key_for_kid(jwks, kid) or _pick_key_for_kid(_JWKS.refresh(jwks_url), kid)
            if key is None:
                abort(401, "Unknown token kid")
        except Exception as e:
            abort(503, f"JWKS fetch error: {e}")

        try:
            claims = _decode_token(token, key, leeway, audience, issuer, alg)
        except jwt.ExpiredSignatureError:
            abort(401, "Token expired")
        except jwt.JWTClaimsError as e:
            abort(401, f"Invalid claims: {e}")
        except Exception as e:
            abort(401, f"Invalid token: {e}")

        user_id = claims.get("sub") or claims.get("user_id")
        org_id = claims.get("org_id")
        email = claims.get("email") or claims.get("primary_email_address")
        name = claims.get("name") or claims.get("full_name")
        if not user_id:
            abort(401, "Missing user id in token")

        if isinstance(org_id, str):
            s = org_id.strip()
            if not s or s.startswith("{{") or not s.startswith("org_"):
                org_id = None

        g.clerk = {
            "user_id": user_id,
            "org_id": org_id,
            "email": email,
            "name": name,
            "raw_claims": claims if _truthy(_cfg("EXPOSE_CLAIMS_DEBUG", "0")) else None,
        }
        return fn(*args, **kwargs)
    return wrapper
