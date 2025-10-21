from __future__ import annotations
import os
import sys
import json
import importlib
from pathlib import Path
from functools import wraps

from flask import Flask, jsonify, request, g
from flask_cors import CORS
from flask_sock import Sock
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import HTTPException

def _normalize_origin(o: str) -> str:
  o = (o or "").strip()
  return o[:-1] if o.endswith("/") else o

def _parse_origins():
  raw = os.getenv("ALLOWED_ORIGINS", "").strip()
  if raw:
    return [_normalize_origin(o) for o in raw.split(",") if o.strip()]
  cand = [os.getenv("FRONTEND_ORIGIN", "").strip(), os.getenv("FRONTEND_URL", "").strip()]
  out = [_normalize_origin(o) for o in cand if o]
  return out or ["http://localhost:3000", "http://localhost:5173"]

def create_app(config: dict | None = None):
  load_dotenv()
  app = Flask(__name__)
  app.url_map.strict_slashes = False

  app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

  app.logger.info(f"[init] cwd={os.getcwd()} root_path={app.root_path} sys.path[0]={sys.path[0]}")
  for rel in [
    "app/__init__.py",
    "app/enterprise.py",
    "app/billing.py",
    "app/blueprints/enterprise.py",
    "app/blueprints/billing.py",
    "app/routes/billing.py",
    "app/routes/enterprise.py",
  ]:
    p = Path(app.root_path).parent / rel
    app.logger.info(f"[init] exists {rel}? {'YES' if p.exists() else 'NO'} -> {p}")

  app.config.update(
    FRONTEND_URL=os.getenv("FRONTEND_URL", "http://localhost:5173"),
    CLERK_PUBLISHABLE_KEY=os.getenv("CLERK_PUBLISHABLE_KEY", ""),
    CLERK_SECRET_KEY=os.getenv("CLERK_SECRET_KEY", ""),
    CLERK_JWKS_URL=os.getenv("CLERK_JWKS_URL", ""),
    CLERK_WEBHOOK_SECRET=os.getenv("CLERK_WEBHOOK_SECRET", ""),
    CLERK_ISSUER=os.getenv("CLERK_ISSUER", ""),
    CLERK_AUDIENCE=os.getenv("CLERK_AUDIENCE", "backend"),
    CLERK_LEEWAY=os.getenv("CLERK_LEEWAY", "30"),
    CLERK_JWKS_TTL=os.getenv("CLERK_JWKS_TTL", "3600"),
    CLERK_JWKS_TIMEOUT=os.getenv("CLERK_JWKS_TIMEOUT", "5"),
    DISABLE_AUTH=os.getenv("DISABLE_AUTH", "0"),
    EXPOSE_CLAIMS_DEBUG=os.getenv("EXPOSE_CLAIMS_DEBUG", "0"),
    JSON_SORT_KEYS=False,
  )
  if config:
    app.config.update(config)

  origins = _parse_origins()
  app.logger.info(f"[init] CORS origins = {origins}")
  app.logger.info("[init] FRONTEND_URL=%s", app.config.get("FRONTEND_URL"))

  CORS(
    app,
    resources={r"/api/*": {"origins": origins}},
    supports_credentials=True,
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Org-Id", "X-Requested-With"],
    expose_headers=["X-Total-Count", "Content-Range", "Link", "Content-Length"],
    max_age=86400,
  )

  @app.before_request
  def _allow_cors_preflight():
    if request.method == "OPTIONS" and request.path.startswith("/api/"):
      return ("", 204)

  MODULE_ROOTS = ["app.blueprints", "app.routes", "app"]

  def register_bp(module_name: str, attr: str) -> bool:
    for root in MODULE_ROOTS:
      try:
        mod = importlib.import_module(f"{root}.{module_name}")
        bp = getattr(mod, attr)
        app.register_blueprint(bp)
        app.logger.info(f"[init] Registrado BP '{bp.name}' de {root}.{module_name}")
        return True
      except Exception as e:
        app.logger.warning(f"[init] {root}.{module_name} falló: {e}")
        continue
    app.logger.warning(f"[init] No se pudo registrar {module_name}.{attr}")
    return False

  register_bp("enterprise", "bp")
  register_bp("billing", "bp")

  from flask import Blueprint
  intdbg = Blueprint("int_debug", __name__, url_prefix="/api/_int")

  @intdbg.get("/routes")
  def _int_routes():
    rules = []
    for r in app.url_map.iter_rules():
      methods = sorted(m for m in r.methods if m in {"GET","POST","PUT","PATCH","DELETE","OPTIONS"})
      rules.append({"endpoint": r.endpoint, "methods": methods, "rule": str(r.rule)})
    rules.sort(key=lambda x: x["rule"])
    return jsonify(rules), 200

  try:
    from .auth import require_clerk_auth as _auth_deco
    app.logger.info("[init] Auth decorator cargado")
  except Exception as e:
    app.logger.warning(f"[init] Auth no disponible ({e}); claims será 501")
    def _auth_deco(fn):
      @wraps(fn)
      def wrapper(*args, **kwargs):
        return jsonify({"error": "auth not configured"}), 501
      return wrapper

  @intdbg.get("/auth-config")
  def _int_auth_cfg():
    cfg = {
      "CLERK_ISSUER": app.config.get("CLERK_ISSUER", ""),
      "CLERK_JWKS_URL": app.config.get("CLERK_JWKS_URL", ""),
      "CLERK_AUDIENCE": app.config.get("CLERK_AUDIENCE", ""),
      "CLERK_LEEWAY": app.config.get("CLERK_LEEWAY", ""),
      "DISABLE_AUTH": app.config.get("DISABLE_AUTH", ""),
    }
    return jsonify(cfg), 200

  @intdbg.get("/claims")
  @_auth_deco
  def _int_claims():
    authz = request.headers.get("Authorization", "")
    authz_short = (authz[:24] + "...") if authz else ""
    clerk = getattr(g, "clerk", None) or {}
    payload = {
      "g_clerk": {
        "user_id": clerk.get("user_id"),
        "org_id": clerk.get("org_id"),
        "email": clerk.get("email"),
        "name": clerk.get("name"),
        "raw_claims": clerk.get("raw_claims"),
      },
      "auth_header_present": bool(authz),
      "auth_header_prefix_ok": authz.startswith("Bearer "),
      "auth_header_sample": authz_short,
    }
    return jsonify(payload), 200

  @intdbg.route("/echo", methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"])
  def _int_echo():
    return jsonify({
      "method": request.method,
      "path": request.path,
      "headers": {k: v for k, v in request.headers.items() if k.lower() != "authorization"},
      "json": request.get_json(silent=True),
      "args": request.args.to_dict(flat=True),
    }), 200

  app.register_blueprint(intdbg)
  app.logger.info("[init] Fallback interno montado en /api/_int")

  @app.get("/api/health")
  def health():
    return jsonify({"status": "ok"}), 200

  @app.errorhandler(Exception)
  def handle_any_error(e):
    if isinstance(e, HTTPException):
      return {"message": e.description}, e.code
    app.logger.exception("Unhandled error")
    return {"message": "internal_error"}, 500

  sock = Sock(app)

  @sock.route("/ws")
  def ws_handler(ws):
    ws.send(json.dumps({"type": "hello", "msg": "ws up"}))
    while True:
      data = ws.receive()
      if data is None:
        break
      try:
        payload = json.loads(data)
      except Exception:
        payload = data
      if isinstance(payload, dict) and payload.get("type") == "ping":
        ws.send(json.dumps({"type": "pong"}))
      elif payload == "ping":
        ws.send("pong")
      else:
        ws.send(json.dumps({"type": "echo", "data": payload}))

  return app

app = create_app()
