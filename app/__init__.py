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


def _import_optional(module_path: str, attr: str | None = None):
    """
    Importa módulo/atributo opcionalmente.
    Si falla, devuelve None sin romper el arranque.
    """
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, attr) if attr else mod
    except Exception as e:
        # No usar print en prod, usa logger cuando esté disponible
        # pero aquí todavía no tenemos app.logger
        return None


def create_app(config: dict | None = None):
    # ===== Env & App =====
    load_dotenv()
    app = Flask(__name__)
    app.url_map.strict_slashes = False  # evita 308 por trailing slash

    # ===== Preflight universal /api =====
    @app.before_request
    def _short_circuit_preflight():
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
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

    # ===== Blueprints “core” (si existen) =====
    items_bp    = _import_optional("app.routes.items", "bp") or _import_optional("app.blueprints.items", "bp")
    comments_bp = _import_optional("app.routes.comments", "bp") or _import_optional("app.blueprints.comments", "bp")
    compat_bp   = _import_optional("app.routes.compat", "bp")   or _import_optional("app.blueprints.compat", "bp")

    if items_bp:
        app.register_blueprint(items_bp, url_prefix="/api/items")
    if comments_bp:
        app.register_blueprint(comments_bp, url_prefix="/api")
    if compat_bp:
        app.register_blueprint(compat_bp, url_prefix="/api")

    # ===== Billing (fallback automático) =====
    billing_bp = _import_optional("app.routes.billing", "bp") or _import_optional("app.blueprints.billing", "bp")
    if billing_bp:
        app.register_blueprint(billing_bp, url_prefix="/api/billing")
    else:
        # Evita crashear al arrancar si falta el módulo
        @app.get("/api/billing/health")
        def _billing_placeholder():
            return jsonify({"billing": "not-installed"}), 200

    # ===== Webhooks (fallback automático) =====
    webhooks_bp = _import_optional("app.routes.webhooks", "bp") or _import_optional("app.blueprints.webhooks", "bp")
    if webhooks_bp:
        app.register_blueprint(webhooks_bp, url_prefix="/api/webhooks")
    else:
        @app.post("/api/webhooks/stripe")
        def _webhook_placeholder():
            return jsonify({"webhooks": "not-installed"}), 200

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
