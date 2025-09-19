# app/__init__.py
import os
import sys
import json
import importlib
from pathlib import Path
from functools import wraps

from flask import Flask, jsonify, make_response, request, g
from flask_cors import CORS
from flask_sock import Sock
from dotenv import load_dotenv


def _parse_origins():
    raw = os.getenv("ALLOWED_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    single = os.getenv("FRONTEND_ORIGIN", "").strip()
    if single:
        return [single]
    # fallback dev
    return ["http://localhost:3000", "http://localhost:5173"]


def create_app(config: dict | None = None):
    load_dotenv()
    app = Flask(__name__)
    app.url_map.strict_slashes = False

    # ── Logs de diagnóstico de archivos existentes ──
    app.logger.info(f"[init] cwd={os.getcwd()} root_path={app.root_path} sys.path[0]={sys.path[0]}")
    for rel in [
        "app/__init__.py",
        # preferidos
        "app/blueprints/debug.py",
        "app/blueprints/billing.py",
        "app/blueprints/webhooks.py",
        "app/blueprints/items.py",
        "app/blueprints/comments.py",
        "app/blueprints/compat.py",
        # legacy
        "app/routes/debug.py",
        "app/routes/billing.py",
        "app/routes/webhooks.py",
        "app/routes/items.py",
        "app/routes/comments.py",
        "app/routes/compat.py",
        # auth
        "app/auth.py",
    ]:
        p = Path(app.root_path).parent / rel
        app.logger.info(f"[init] exists {rel}? {'YES' if p.exists() else 'NO'} -> {p}")

    # ── Preflight universal /api/* ──
    @app.before_request
    def _short_circuit_preflight():
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            return make_response(("", 204))

    # ── Config ──
    app.config.update(
        FRONTEND_URL=os.getenv("FRONTEND_URL", "http://localhost:5173"),
        # Clerk
        CLERK_PUBLISHABLE_KEY=os.getenv("CLERK_PUBLISHABLE_KEY", ""),
        CLERK_SECRET_KEY=os.getenv("CLERK_SECRET_KEY", ""),
        CLERK_JWKS_URL=os.getenv("CLERK_JWKS_URL", ""),
        CLERK_WEBHOOK_SECRET=os.getenv("CLERK_WEBHOOK_SECRET", ""),
        CLERK_ISSUER=os.getenv("CLERK_ISSUER", ""),
        CLERK_AUDIENCE=os.getenv("CLERK_AUDIENCE", ""),
        CLERK_LEEWAY=os.getenv("CLERK_LEEWAY", "30"),
        CLERK_JWKS_TTL=os.getenv("CLERK_JWKS_TTL", "3600"),
        CLERK_JWKS_TIMEOUT=os.getenv("CLERK_JWKS_TIMEOUT", "5"),
        # Flags
        DISABLE_AUTH=os.getenv("DISABLE_AUTH", "0"),
        EXPOSE_CLAIMS_DEBUG=os.getenv("EXPOSE_CLAIMS_DEBUG", "0"),
        # Flask
        JSON_SORT_KEYS=False,
    )
    if config:
        app.config.update(config)

    # ── CORS (solo /api/*) ──
    origins = _parse_origins()
    CORS(
        app,
        resources={r"/api/.*": {"origins": origins}},
        supports_credentials=True,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Requested-With", "X-Debug-Filters"],
        expose_headers=["X-Total-Count", "Content-Range"],
        max_age=86400,
    )

    # ── Registro de blueprints de forma robusta ──
    MODULE_ROOTS = ["app.blueprints", "app.routes"]

    def register_bp(module_name: str, attr: str) -> bool:
        """
        Busca module_name en app.blueprints y app.routes; si lo encuentra,
        registra su atributo 'attr' (normalmente 'bp').
        """
        errors = []
        for root in MODULE_ROOTS:
            module_path = f"{root}.{module_name}"
            try:
                mod = importlib.import_module(module_path)
            except ModuleNotFoundError:
                continue
            except Exception as e:
                errors.append((module_path, f"import_error: {e}"))
                continue

            try:
                bp = getattr(mod, attr)
            except Exception as e:
                errors.append((module_path, f"missing_attr_{attr}: {e}"))
                continue

            try:
                app.register_blueprint(bp)  # respeta url_prefix del propio BP
                app.logger.info(f"[init] Registrado BP '{bp.name}' de {module_path}")
                return True
            except Exception as e:
                errors.append((module_path, f"register_error: {e}"))

        if errors:
            for where, msg in errors:
                app.logger.error(f"[init] Fallo registrando {where}.{attr}: {msg}")
        else:
            app.logger.info(f"[init] No se encontró módulo '{module_name}' en {MODULE_ROOTS}")
        return False

    # Intenta registrar debug/billing/webhooks y otros opcionales
    register_bp("debug", "bp")
    register_bp("billing", "bp")
    register_bp("webhooks", "bp")
    register_bp("items", "bp")
    register_bp("comments", "bp")
    register_bp("compat", "bp")

    # ── Fallback de debug GARANTIZADO en /api/_int ──
    from flask import Blueprint
    intdbg = Blueprint("int_debug", __name__, url_prefix="/api/_int")

    @intdbg.get("/routes")
    def _int_routes():
        rules = []
        for r in app.url_map.iter_rules():
            methods = sorted(m for m in r.methods if m in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
            rules.append({"endpoint": r.endpoint, "methods": methods, "rule": str(r.rule)})
        rules.sort(key=lambda x: x["rule"])
        return jsonify(rules), 200

    @intdbg.get("/auth-config")
    def _int_auth_cfg():
        cfg = {
            "CLERK_ISSUER": app.config.get("CLERK_ISSUER", ""),
            "CLERK_JWKS_URL": app.config.get("CLERK_JWKS_URL", ""),
            "CLERK_AUDIENCE": app.config.get("CLERK_AUDIENCE", ""),
            "CLERK_LEEWAY": app.config.get("CLERK_LEEWAY", ""),
            "CLERK_JWKS_TTL": app.config.get("CLERK_JWKS_TTL", ""),
            "CLERK_JWKS_TIMEOUT": app.config.get("CLERK_JWKS_TIMEOUT", ""),
            "DISABLE_AUTH": app.config.get("DISABLE_AUTH", ""),
        }
        return jsonify(cfg), 200

    # ── claims: SIEMPRE montado. Si no hay auth, responde 501 ──
    try:
        from app.auth import require_clerk_auth as _auth_deco
        app.logger.info("[init] Auth decorator cargado: app.auth.require_clerk_auth")
    except Exception as e:
        app.logger.warning(f"[init] Auth no disponible ({e}); /api/_int/claims devolverá 501")
        def _auth_deco(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                return jsonify({"error": "auth not configured"}), 501
            return wrapper

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

    @intdbg.route("/echo", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    def _int_echo():
        return jsonify({
            "method": request.method,
            "path": request.path,
            "headers": {k: v for k, v in request.headers.items()},
            "json": request.get_json(silent=True),
            "args": request.args.to_dict(flat=True),
        }), 200

    app.register_blueprint(intdbg)
    app.logger.info("[init] Fallback interno montado en /api/_int")

    # ── Health ──
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"}), 200

    # ── WebSocket (opcional) ──
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
