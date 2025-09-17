# app/__init__.py
import os
import json
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
    return ["http://localhost:3000", "http://localhost:5173"]


def create_app(config: dict | None = None):
    load_dotenv()
    app = Flask(__name__)
    app.url_map.strict_slashes = False  # evita 308 por trailing slash

    # --- corta el preflight antes de cualquier auth/blueprint ---
    @app.before_request
    def _short_circuit_preflight():
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            return make_response(("", 204))

    # Config
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
        JSON_SORT_KEYS=False,
    )
    if config:
        app.config.update(config)

    # CORS sólo en /api/*
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

    # Blueprints
    from app.routes.items import bp as items_bp
    from app.routes.comments import bp as comments_bp
    from app.routes.compat import bp as compat_bp
    app.register_blueprint(items_bp,    url_prefix="/api/items")
    app.register_blueprint(comments_bp, url_prefix="/api")
    app.register_blueprint(compat_bp,   url_prefix="/api")

    # Billing
    from app.routes.billing import bp as billing_bp
    app.register_blueprint(billing_bp, url_prefix="/api/billing")

    # Webhooks
    from app.routes.webhooks import bp as webhooks_bp
    app.register_blueprint(webhooks_bp, url_prefix="/api/webhooks")

    # Health
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"}), 200

    # WS (opcional)
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
