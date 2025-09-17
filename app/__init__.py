# app/__init__.py
import os
import json
from urllib.parse import parse_qs

from flask import Flask, jsonify
from flask_cors import CORS
from flask_sock import Sock
from dotenv import load_dotenv


def _parse_origins():
    """
    Lee orígenes permitidos desde:
      - ALLOWED_ORIGINS (separados por comas)  → producción
      - FRONTEND_ORIGIN (uno solo)             → compat
      - fallback local: http://localhost:3000, http://localhost:5173
    """
    raw = os.getenv("ALLOWED_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]

    single = os.getenv("FRONTEND_ORIGIN", "").strip()
    if single:
        return [single]

    # fallback sólo para desarrollo local
    return ["http://localhost:3000", "http://localhost:5173"]


def create_app(config: dict | None = None):
    # ===== Env & App =====
    load_dotenv()  # en prod no molesta; en local carga .env
    app = Flask(__name__)

    # ===== App Config =====
    app.config.update(
        FRONTEND_URL=os.getenv("FRONTEND_URL", "http://localhost:5173"),
        # Clerk
        CLERK_PUBLISHABLE_KEY=os.getenv("CLERK_PUBLISHABLE_KEY", ""),
        CLERK_SECRET_KEY=os.getenv("CLERK_SECRET_KEY", ""),
        CLERK_JWKS_URL=os.getenv("CLERK_JWKS_URL", ""),
        CLERK_WEBHOOK_SECRET=os.getenv("CLERK_WEBHOOK_SECRET", ""),
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
        resources={r"/api/.*": {"origins": origins}},   # regex
        supports_credentials=True,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type", "Authorization", "X-Requested-With", "X-Debug-Filters"
        ],
        expose_headers=["X-Total-Count", "Content-Range"],
        max_age=86400,
    )

    # ===== Blueprints =====
    from app.routes.items import bp as items_bp
    from app.routes.comments import bp as comments_bp
    from app.routes.compat import bp as compat_bp  # alias compatibles para FE

    # Canonical
    app.register_blueprint(items_bp,    url_prefix="/api/items")
    app.register_blueprint(comments_bp, url_prefix="/api")
    app.register_blueprint(compat_bp,   url_prefix="/api")

    # Opcionales: billing & webhooks
    try:
        from app.routes.billing import bp as billing_bp
        app.register_blueprint(billing_bp, url_prefix="/api/billing")
    except Exception as e:
        app.logger.info(f"[init] billing no cargado: {e}")

    try:
        from app.routes.webhooks import bp as webhooks_bp
        app.register_blueprint(webhooks_bp, url_prefix="/api/webhooks")
    except Exception as e:
        app.logger.info(f"[init] webhooks no cargado: {e}")

    # ===== Healthcheck =====
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"}), 200

    # ===== Preflight universal /api (cinturón y tirantes) =====
    @app.route("/api/<path:_any>", methods=["OPTIONS"])
    def api_options(_any):
        # flask-cors responde; devolvemos 204 por si un proxy/WAF interfiere
        return ("", 204)

    # ===== WebSocket =====
    sock = Sock(app)

    @sock.route("/ws")
    def ws_handler(ws):
        try:
            qs = ws.environ.get("QUERY_STRING", "")
            token = parse_qs(qs).get("token", [None])[0]  # noqa: F841
        except Exception:
            token = None  # noqa: F841

        # saludo inicial
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
                continue
            if payload == "ping":
                ws.send("pong")
                continue

            # eco por defecto
            ws.send(json.dumps({"type": "echo", "data": payload}))

    return app
