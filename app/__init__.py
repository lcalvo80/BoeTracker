# app/__init__.py
import os
import sys
import json
import importlib
import traceback
from pathlib import Path

from flask import Flask, jsonify, make_response, request
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

    # ── Logs de diagnóstico para paths y archivos ──
    app.logger.info(f"[init] cwd={os.getcwd()} root_path={app.root_path} sys.path[0]={sys.path[0]}")
    for rel in [
        # núcleo
        "app/__init__.py",
        # rutas clásicas
        "app/routes/__init__.py",
        "app/routes/billing.py",
        "app/routes/debug.py",
        "app/routes/webhooks.py",
        "app/routes/items.py",
        "app/routes/comments.py",
        "app/routes/compat.py",
        # nueva ubicación: blueprints
        "app/blueprints/__init__.py",
        "app/blueprints/billing.py",
        "app/blueprints/webhooks.py",
        "app/blueprints/items.py",
        "app/blueprints/comments.py",
        "app/blueprints/compat.py",
    ]:
        p = Path(app.root_path).parent / rel  # app.root_path suele ser /app/app
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
        # Stripe
        STRIPE_SECRET_KEY=os.getenv("STRIPE_SECRET_KEY", ""),
        STRIPE_WEBHOOK_SECRET=os.getenv("STRIPE_WEBHOOK_SECRET", ""),
        PRICE_PRO_MONTHLY_ID=os.getenv("PRICE_PRO_MONTHLY_ID", ""),
        PRICE_ENTERPRISE_SEAT_ID=os.getenv("PRICE_ENTERPRISE_SEAT_ID", ""),
        # Flask sane defaults
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

    # ── Helper: registrar blueprints con traceback en error ──
    def _try_register(module_path: str, attr: str, url_prefix: str) -> bool:
        try:
            mod = importlib.import_module(module_path)
            bp = getattr(mod, attr)
            app.register_blueprint(bp, url_prefix=url_prefix)
            app.logger.info(f"[init] Registrado BP '{bp.name}' de {module_path} en {url_prefix}")
            return True
        except Exception as e:
            app.logger.error(f"[init] NO se pudo registrar {module_path}.{attr}: {e}")
            app.logger.error("[init] traceback:\n" + traceback.format_exc())
            return False

    # ── Helper flexible: intenta varios roots de módulo ──
    MODULE_ROOTS = ["app.routes", "app.blueprints", "app.blueprinsts"]  # el último es temporal/compat

    def register_bp_flexible(module_name: str, attr: str, url_prefix: str) -> bool:
        for root in MODULE_ROOTS:
            if _try_register(f"{root}.{module_name}", attr, url_prefix):
                return True
        app.logger.error("[init] No se encontró módulo '%s' en %s", module_name, MODULE_ROOTS)
        return False

    # ── Debug (si falla, monta fallback mínimo) ──
    if not _try_register("app.routes.debug", "bp", "/api/_debug"):
        from flask import Blueprint, current_app
        debug_bp = Blueprint("debug_fallback", __name__)

        @debug_bp.get("/routes")
        def _routes_list():
            rules = []
            for r in current_app.url_map.iter_rules():
                rules.append({
                    "endpoint": r.endpoint,
                    "methods": sorted(m for m in r.methods if m in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}),
                    "rule": str(r.rule),
                })
            return jsonify(sorted(rules, key=lambda x: x["rule"]))

        @debug_bp.route("/echo", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
        def _echo():
            return jsonify({
                "method": request.method,
                "path": request.path,
                "headers": {k: v for k, v in request.headers.items()},
                "json": request.get_json(silent=True),
            })

        app.register_blueprint(debug_bp, url_prefix="/api/_debug")
        app.logger.warning("[init] debug fallback montado en /api/_debug")

    # ── Core (si existen; flexible entre routes/blueprints) ──
    register_bp_flexible("items", "bp", "/api/items")
    register_bp_flexible("comments", "bp", "/api")
    register_bp_flexible("compat", "bp", "/api")

    # ── Billing ──
    register_bp_flexible("billing", "bp", "/api/billing")

    # ── Webhooks ──
    register_bp_flexible("webhooks", "bp", "/api/webhooks")

    # ── Health ──
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"}), 200

    # ── WebSocket (opcional) ──
    sock = Sock(app)

    @sock.route("/ws")
    def ws_handler(ws):
        try:
            _ = ws.environ.get("QUERY_STRING", "")
        except Exception:
            pass

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
