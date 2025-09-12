# app/__init__.py
import os
import json
from flask import Flask, jsonify
from flask_cors import CORS
from flask_sock import Sock
from urllib.parse import parse_qs

def create_app(config: dict | None = None):
    app = Flask(__name__)

    # ================= App Config =================
    if config:
        app.config.update(config)

    # ================= CORS (solo /api/*) =================
    # Define FRONTEND_ORIGIN en Railway con el dominio exacto del frontend
    # ej: https://boefrontend-production.up.railway.app
    FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")

    CORS(
        app,
        resources={r"/api/*": {"origins": [FRONTEND_ORIGIN]}},
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization", "X-Debug-Filters"],
        expose_headers=["X-Total-Count"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )

    # ================= Blueprints =================
    # Ajusta estos imports a tu estructura real
    from app.routes.items import bp as items_bp
    from app.routes.comments import bp as comments_bp
    from app.routes.compat import bp as compat_bp  # alias para FE

    # Rutas canónicas
    app.register_blueprint(items_bp,    url_prefix="/api/items")
    # Otros endpoints existentes montados en /api
    app.register_blueprint(comments_bp, url_prefix="/api")
    # Compatibilidad con el FE actual: /api/filters, /api/filtros, /api/meta/filters, /api/boe/<id>
    app.register_blueprint(compat_bp,   url_prefix="/api")

    # ================= Healthcheck =================
    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"}), 200

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
