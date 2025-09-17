# app/auth.py
import time
import requests
from functools import wraps
from flask import request, g, abort, current_app
from jose import jwt
from jose.utils import base64url_decode


class JWKSCache:
    _jwks = None
    _fetched_at = 0
    _ttl = 3600

    @classmethod
    def get(cls, url: str):
        now = int(time.time())
        if cls._jwks and (now - cls._fetched_at) < cls._ttl:
            return cls._jwks
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        cls._jwks = res.json()
        cls._fetched_at = now
        return cls._jwks


def require_clerk_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            abort(401, "Missing bearer token")
        token = auth.split(" ", 1)[1]

        jwks_url = current_app.config.get("CLERK_JWKS_URL")
        if not jwks_url:
            abort(500, "CLERK_JWKS_URL not configured")

        jwks = JWKSCache.get(jwks_url)
        headers = jwt.get_unverified_header(token)
        kid = headers.get("kid")
        key = None
        for k in jwks.get("keys", []):
            if k.get("kid") == kid:
                key = k
                break
        if not key:
            abort(401, "Invalid token kid")

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[headers.get("alg", "RS256")],
                audience=None,  # ajusta si validas aud
                options={"verify_aud": False},
            )
        except Exception as e:
            abort(401, f"Invalid token: {e}")

        # Guarda identidad para la request
        g.clerk = {
            "user_id": claims.get("sub") or claims.get("user_id"),
            "org_id": (claims.get("org_id") or claims.get("org_id")) if isinstance(claims, dict) else None,
            "email": (claims.get("email") or claims.get("primary_email_address")) if isinstance(claims, dict) else None,
        }
        if not g.clerk["user_id"]:
            abort(401, "Missing user id in token")
        return fn(*args, **kwargs)
    return wrapper
