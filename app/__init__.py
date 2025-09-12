# app/__init__.py
from flask import Flask, jsonify
from flask_cors import CORS
from flask_sock import Sock   # ★ NUEVO
import os

def create_app(config: dict | None = None):
    app = Flask(__name__)

    # ================= App Config =================
    if config:
        app.config.update(config)

    # Puedes activar este flag si quieres permitir el endpoint de debug en items.py
    # app.config.setdefault("DEBUG_FILTERS_ENABLED", False)

    # ================= CORS =================
    # Define FRONTEND_ORIGIN en Railway con el dominio exacto del frontend
    # ej: https://boefrontend-production-7205.up.railway.app
    FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")

    CORS(
        app,
        resources={r"/api/*": {"origins": [FRONTEND_ORIGIN]}},
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization", "X-Debug-Filters"],
        expose_headers=["X-Total-Count"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    # Si prefieres permitir cualquier cabecera:
    # CORS(app, resources={r"/api/*": {"origins": [FRONTEND_ORIGIN]}},
    #      supports_credentials=True, allow_headers="*", expose_headers=["X-Total-Count"],
    #      methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"])

    # ================= Blueprints =================
    from app.routes.items import bp as items_bp
    from app.routes.comments import bp as comments_bp
    from app.routes.compat import bp as compat_bp  # <— alias para FE

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

    # ================= WebSocket ================= ★ NUEVO
    sock = Sock(app)

    @sock.route("/ws")
    def ws_handler(ws):
        # Saludo inicial
        ws.send('{"type":"hello","msg":"ws up"}')
        # Loop simple (eco)
        while True:
            data = ws.receive()
            if data is None:
                break
            ws.send(data)

    return app
