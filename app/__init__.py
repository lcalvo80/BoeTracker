# app/__init__.py
import os
import json
from flask import Flask, jsonify
from flask_cors import CORS
from flask_sock import Sock
from urllib.parse import parse_qs
from dotenv import load_dotenv  # <-- carga .env en local

def create_app(config: dict | None = None):
    # ================= Env & App =================
    load_dotenv()  # no afecta en prod si ya tienes envs
    app = Flask(__name__)

    # ================= App Config =================
    # Valores útiles para servicios (Clerk/Stripe) disponibles vía current_app.config
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
    )
    if config:
        app.config.update(config)

    # ================= CORS (solo /api/*) =================
    # Define FRONTEND_ORIGIN en Railway con el dominio exacto del frontend
    # ej: https://boefrontend-production.up.railway.app  (sin / final)
    FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")

    CORS(
        app,
        resources={r"/api/.*": {"origins": FRONTEND_ORIGIN}},  # <- regex correcto
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization", "X-Debug-Filters"],
        expose_headers=["X-Total-Count"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        max_age=86400,
    )

    # ================= Blueprints existentes =================
    # Ajusta estos imports a tu estructura real
    from app.routes.items import bp as items_bp
    from app.routes.comments import bp as comments_bp
    from app.routes.compat import bp as compat_bp  # alias para FE

    # Rutas canónicas existentes
    app.register_blueprint(items_bp,    url_prefix="/api/items")
    app.register_blueprint(comments_bp, url_prefix="/api")
    app.register_blueprint(compat_bp,   url_prefix="/api")

    # ================= Nuevos Blueprints: Billing & Webhooks =================
    # Montamos bajo /api para que CORS los permita sin cambios.
    # /api/billing/checkout, /api/billing/portal
    # /api/webhooks/stripe  (y opcional /api/webhooks/clerk)
    try:
        from app.routes.billing import bp as billing_bp
        app.register_blueprint(billing_bp, url_prefix="/api/billing")
    except Exception as e:
        app.logger.info(f"Billing blueprint no encontrado (app.routes.billing): {e}")

    try:
        from app.routes.webhooks import bp as webhooks_bp
        app.register_blueprint(webhooks_bp, url_prefix="/api/webhooks")
    except Exception as e:
        app.logger.info(f"Webhooks blueprint no encontrado (app.routes.webhooks): {e}")

    # ================= Healthcheck =================
    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"}), 200

    # ================= Preflight universal /api (cinturón y tirantes) =================
    @app.route("/api/<path:_any>", methods=["OPTIONS"])
    def api_options(_any):
        # flask-cors debería responder automáticamente; este 204 asegura
        # que el proxy/WAF no bloquee los preflights.
        return ("", 204)

    # ================= WebSocket =================
    # Tu frontend conectará a wss://<backend>.railway.app/ws
    # El cliente envía {"type":"ping"} regularmente; aquí respondemos con {"type":"pong"}
    sock = Sock(app)

    @sock.route("/ws")
    def ws_handler(ws):
        # (Opcional) extraer token de la query string si tu cliente lo envía como ?token=...
        # Flask-Sock expone el environ con la QUERY_STRING
        try:
            qs = ws.environ.get("QUERY_STRING", "")
            token = parse_qs(qs).get("token", [None])[0]
            # TODO: validar token si aplica; si no es válido -> return para cerrar
            # if not token_is_valid(token): return
        except Exception:
            token = None  # noqa: F841  # mantener por si lo usas luego

        # Saludo inicial
        ws.send(json.dumps({"type": "hello", "msg": "ws up"}))

        # Bucle principal
        while True:
            data = ws.receive()
            if data is None:
                # Cliente cerró
                break

            # Intentar parsear JSON
            try:
                payload = json.loads(data)
            except Exception:
                payload = data

            # Responder ping/pong esperado por tu cliente
            if isinstance(payload, dict) and payload.get("type") == "ping":
                ws.send(json.dumps({"type": "pong"}))
                continue
            if payload == "ping":
                ws.send("pong")
                continue

            # TODO: tu lógica real de mensajes (broadcast, notificaciones, etc.)
            # De momento: eco tipado para pruebas
            ws.send(json.dumps({"type": "echo", "data": payload}))

    return app
