# app/__init__.py
import os
import json
import importlib
from urllib.parse import parse_qs

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
    # ===== Env & App =====
    load_dotenv()
    app = Flask(__name__)
    app.url_map.strict_slashes = False  # evita 308 por trailing slash

    # ===== Preflight universal /api =====
    @app.before_request
    def _short_circuit_preflight():
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            # Devolvemos 204 para que CORS preflight no llegue a blueprints
            return make_response(("", 204))

    # ===== App Config =====
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

    # ===== CORS (solo bajo /api/*) =====
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

    # ===== Helper para registrar blueprints con logs =====
    def _try_register(module_path: str, attr: str, url_prefix: str):
        try:
            mod = importlib.import_module(module_path)
            bp = getattr(mod, attr)
            app.register_blueprint(bp, url_prefix=url_prefix)
            app.logger.info(f"[init] registrado {module_path}.{attr} en {url_prefix}")
            return True
        except Exception as e:
            app.logger.error(f"[init] NO se pudo registrar {module_path}.{attr}: {e}")
            return False

    # ===== Debug (lista rutas / eco) =====
    _try_register("app.routes.debug", "bp", "/api/_debug")

    # ===== Blueprints “core” (si existen) =====
    _try_register("app.routes.items", "bp", "/api/items")
    _try_register("app.routes.comments", "bp", "/api")
    _try_register("app.routes.compat", "bp", "/api")

    # ===== Billing & Webhooks =====
    # OJO: los módulos deben existir y exponer 'bp = Blueprint(...)'
    _try_register("app.routes.billing", "bp", "/api/billing")
    _try_register("app.routes.webhooks", "bp", "/api/webhooks")

    # ===== Healthcheck =====
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"}), 200

    # ===== WebSocket (opcional) =====
    sock = Sock(app)

    @sock.route("/ws")
    def ws_handler(ws):
        try:
            qs = ws.environ.get("QUERY_STRING", "")
            _ = qs  # no-op
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
