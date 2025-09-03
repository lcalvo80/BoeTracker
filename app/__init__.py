# app/__init__.py
from flask import Flask, jsonify
from flask_cors import CORS
import os

def create_app(config: dict | None = None):
    app = Flask(__name__)

    if config:
        app.config.update(config)

    # === CORS ===
    # Define FRONTEND_ORIGIN en Railway con el dominio exacto del frontend
    # ej: https://boefrontend-production.up.railway.app
    FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")

    CORS(
        app,
        resources={r"/api/*": {"origins": [FRONTEND_ORIGIN]}},
        supports_credentials=True,
        # Opción 1 (más estricta): lista explícita de cabeceras permitidas
        allow_headers=["Content-Type", "Authorization", "X-Debug-Filters"],
        # Si necesitas exponer cabeceras a JS:
        expose_headers=["X-Total-Count"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    # --- Si prefieres permitir cualquier cabecera, cambia la línea de allow_headers por: ---
    # allow_headers="*",

    # === Blueprints ===
    from app.routes.items import bp as items_bp
    from app.routes.comments import bp as comments_bp

    # items: /api/items...
    app.register_blueprint(items_bp, url_prefix="/api/items")
    # comments: montado en /api
    app.register_blueprint(comments_bp, url_prefix="/api")

    # === Health ===
    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"}), 200

    return app
