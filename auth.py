# app/auth.py
import os
import time
import threading
import requests
from functools import wraps
from flask import request, g, abort, current_app
from jose import jwt

class _JWKSCache:
    _jwks = None
    _fetched_at = 0
    _ttl = 3600
    _lock = threading.Lock()

    @classmethod
    def get(cls, url: str):
        now = int(time.time())
        with cls._lock:
            if cls._jwks and (now - cls._fetched_at) < cls._ttl:
                return cls._jwks
            res = requests.get(url, timeout=5)
            res.raise_for_status()
            cls._jwks = res.json()
            cls._fetched_at = now
            return cls._jwks

def _dev_bypass_enabled() -> bool:
    flag = (current_app.config.get("DISABLE_AUTH") or os.getenv("DISABLE_AUTH", "0")).lower()
    return flag in ("1", "true", "yes", "on")

def require_clerk_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Bypass para desarrollo controlado por ENV
        if _dev_bypass_enabled():
            g.clerk = {"user_id": "dev_user_123", "email": "dev@example.com", "name": "Dev User"}
            return fn(*args, **kwargs)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            abort(401, "Missing bearer token")
        token = auth.split(" ", 1)[1]

        jwks_url = current_app.config.get("CLERK_JWKS_URL")
        if not jwks_url:
            abort(500, "CLERK_JWKS_URL not configured")

        jwks = _JWKSCache.get(jwks_url)
        headers = jwt.get_unverified_header(token)
        kid = headers.get("kid")
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if not key:
            abort(401, "Invalid token kid")

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[headers.get("alg", "RS256")],
                options={"verify_aud": False},  # ajusta si necesitas aud
            )
        except Exception as e:
            abort(401, f"Invalid token: {e}")

        # Mapeo mínimo de identidad (adaptable según tu token)
        g.clerk = {
            "user_id": claims.get("sub") or claims.get("user_id"),
            "org_id": claims.get("org_id"),
            "email": claims.get("email") or claims.get("primary_email_address"),
            "name": claims.get("name") or claims.get("full_name"),
        }
        if not g.clerk["user_id"]:
            abort(401, "Missing user id in token")
        return fn(*args, **kwargs)
    return wrapper
