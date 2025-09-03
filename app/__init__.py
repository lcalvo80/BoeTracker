# app/__init__.py
from flask import Flask, jsonify
from flask_cors import CORS
import os

def create_app(config: dict | None = None):
    app = Flask(__name__)

    # === Configuración base ===
    app.config.update(
        DEBUG=False,
        TESTING=False,
        DEBUG_FILTERS_ENABLED=False,
        LOG_FILTERS=False,
    )
    if config:
        app.config.update(config)

    # === CORS ===
    FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
    CORS(
        app,
        resources={r"/api/*": {"origins": [FRONTEND_ORIGIN]}},
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )

    # === Blueprints ===
    from app.routes.items import bp as items_bp
    from app.routes.comments import bp as comments_bp

    app.register_blueprint(items_bp, url_prefix="/api/items")
    app.register_blueprint(comments_bp, url_prefix="/api")

    # === Health ===
    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"}), 200

    return app

# Opción: exportar una instancia global para CLI/uwsgi/gunicorn
# app = create_app()
