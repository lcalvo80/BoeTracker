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
from jose.exceptions import ExpiredSignatureError, JWTClaimsError


# ───────────────── helpers de config ─────────────────
def _cfg(key: str, default: Optional[str] = None) -> str:
    """
    Lee de current_app.config si hay app context, si no, de variables de entorno.
    """
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


# ───────────────── caché JWKS ─────────────────
class _JWKSCache:
    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, Any]] = {}
        a = {}  # just to keep lints quiet
        self._at: Dict[str, int] = {}
        self._lock = threading.Lock()

    def get(self, url: str, ttl: int = 3600) -> Dict[str, Any]:
        now = int(time.time())
        with self._lock:
            jwks = self._store.get(url)
            at = self._at.get(url, 0)
            if jwks and (now - at) < ttl:
                return jwks
        data = self._fetch(url)
        with self._lock:
            self._store[url] = data
            self._at[url] = now
        return data

    def refresh(self, url: str) -> Dict[str, Any]:
        data = self._fetch(url)
        with self._lock:
            self._store[url] = data
            self._at[url] = int(time.time())
        return data

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


# ───────────────── utilidades JWT ─────────────────
def _get_bearer_token() -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        t = auth.split(" ", 1)[1].strip()
        return t or None
    return None


def _pick_key_for_kid(jwks: Dict[str, Any], kid: str) -> Optional[Dict[str, Any]]:
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            return k
    return None


def _decode_token(
    token: str,
    key: Dict[str, Any],
    leeway: int,
    audience: Optional[str],
    issuer: Optional[str],
    alg: Optional[str],
) -> Dict[str, Any]:
    options = {
        "verify_aud": bool(audience),
        "verify_iat": True,
        "verify_exp": True,
        "verify_nbf": True,
        "verify_iss": bool(issuer),
    }
    kwargs: Dict[str, Any] = {
        "key": key,
        "algorithms": [alg] if alg else ["RS256", "RS512", "ES256", "ES512"],
        "options": options,
    }
    if audience:
        kwargs["audience"] = audience
    if issuer:
        kwargs["issuer"] = issuer

    try:
        return jwt.decode(token, leeway=leeway, **kwargs)  # python-jose 3.x
    except TypeError:
        return jwt.decode(token, **kwargs)


def _bypass_enabled() -> bool:
    return _truthy(_cfg("DISABLE_AUTH", "0"))


# ───────────────── helpers de claims ─────────────────
def _extract_org_id(claims: Dict[str, Any]) -> Optional[str]:
    """
    Clerk puede enviar la org activa en varias formas:
      - org_id / organization_id
      - o: { id, ... } / org: { id, ... } / organization: { id, ... }
      - orgs / organizations: [ { id, ... }, ... ]
    """
    if not isinstance(claims, dict):
        return None

    # directos
    for k in ("org_id", "organization_id"):
        v = claims.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()

    # objetos anidados
    for k in ("o", "org", "organization"):
        o = claims.get(k)
        if isinstance(o, dict):
            vid = o.get("id")
            if isinstance(vid, str) and vid.strip():
                return vid.strip()

    # listas
    for k in ("orgs", "organizations"):
        arr = claims.get(k)
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, dict):
                    vid = it.get("id")
                    if isinstance(vid, str) and vid.strip():
                        return vid.strip()

    return None


def _extract_org_role(claims: Dict[str, Any]) -> Optional[str]:
    """
    Intenta extraer el rol de la organización activa.
    - Directo: org_role
    - Nested: organization.membership.role / org.membership.role
    """
    if not isinstance(claims, dict):
        return None

    direct = claims.get("org_role")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    for k in ("organization", "org"):
        obj = claims.get(k)
        if isinstance(obj, dict):
            mem = obj.get("membership") or {}
            role = mem.get("role")
            if isinstance(role, str) and role.strip():
                return role.strip()

    return None


# ───────────────── decorador ─────────────────
def require_clerk_auth(fn):
    """
    - Si DISABLE_AUTH=1 -> bypass (inyecta g.clerk mínimo).
    - Si no, valida Bearer JWT contra JWKS de Clerk.
    Entorno soportado:
      CLERK_JWKS_URL (obligatoria si no hay bypass)
      CLERK_AUDIENCE, CLERK_ISSUER (opcionales)
      CLERK_JWKS_TTL, CLERK_LEEWAY, CLERK_JWKS_TIMEOUT
      EXPOSE_CLAIMS_DEBUG
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
      if _bypass_enabled():
          g.clerk = {
              "user_id": "dev_user",
              "org_id": None,
              "org_role": None,
              "email": "dev@example.com",
              "name": "Dev User",
              "raw_claims": None,
          }
          return fn(*args, **kwargs)

      token = _get_bearer_token()
      if not token:
          abort(401, "Missing bearer token")

      jwks_url = _cfg("CLERK_JWKS_URL", "")
      if not jwks_url:
          abort(500, "CLERK_JWKS_URL not configured")

      try:
          leeway = int(_cfg("CLERK_LEEWAY", "30") or "30")
      except Exception:
          leeway = 30
      audience = _cfg("CLERK_AUDIENCE", "") or None  # ← aquí debe ser "backend"
      issuer = _cfg("CLERK_ISSUER", "") or None
      try:
          ttl = int(_cfg("CLERK_JWKS_TTL", "3600") or "3600")
      except Exception:
          ttl = 3600

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
      except ExpiredSignatureError:
          abort(401, "Token expired")
      except JWTClaimsError as e:
          abort(401, f"Invalid claims: {e}")
      except Exception as e:
          abort(401, f"Invalid token: {e}")

      # Identidad
      user_id = claims.get("sub") or claims.get("user_id")
      org_id = _extract_org_id(claims)
      org_role = _extract_org_role(claims)

      email = (
          claims.get("email")
          or claims.get("primary_email_address")
          or (claims.get("email_addresses") or [{}])[0].get("email_address")
      )
      name = (
          claims.get("name")
          or claims.get("full_name")
          or " ".join([claims.get("first_name") or "", claims.get("last_name") or ""]).strip()
      )

      if not user_id:
          abort(401, "Missing user id in token")

      # Sanitizar org_id
      if isinstance(org_id, str):
          s = org_id.strip()
          if not s or s.startswith("{{") or not s.startswith("org_"):
              org_id = None

      g.clerk = {
          "user_id": user_id,
          "org_id": org_id,
          "org_role": org_role,
          "email": email,
          "name": name,
          "raw_claims": claims if _truthy(_cfg("EXPOSE_CLAIMS_DEBUG", "0")) else None,
      }
      return fn(*args, **kwargs)
    return wrapper
