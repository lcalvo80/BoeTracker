# app/__init__.py
from flask import Flask, jsonify
from flask_cors import CORS
import os

def create_app():
    app = Flask(__name__)

    # === CORS ===
    # En Railway (servicio backend), define FRONTEND_ORIGIN con tu dominio de frontend.
    # p.ej. https://boefrontend-production.up.railway.app
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

    # items: expone /api/items, /api/items/secciones, /api/items/epigrafes, /api/items/<id>...
    app.register_blueprint(items_bp, url_prefix="/api/items")

    # comments: dentro define /items/<id>/comments → con este prefijo queda /api/items/<id>/comments
    app.register_blueprint(comments_bp, url_prefix="/api")

    # === Health ===
    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"}), 200

    return app
