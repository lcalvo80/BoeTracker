# app/__init__.py
from flask import Flask, jsonify, request
from flask_cors import CORS
import os

DEFAULT_ORIGINS = {"http://localhost:3000"}
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN")
if FRONTEND_ORIGIN:
    DEFAULT_ORIGINS.add(FRONTEND_ORIGIN)
ALLOWED_ORIGINS = DEFAULT_ORIGINS

def create_app():
    app = Flask(__name__)
    app.url_map.strict_slashes = False

    CORS(
        app,
        resources={r"/api/*": {
            "origins": list(ALLOWED_ORIGINS),
            "supports_credentials": True,
            "allow_headers": ["Content-Type", "Authorization"],
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        }},
    )

    @app.after_request
    def add_cors_headers(resp):
        origin = request.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            # 🔧 añade SIEMPRE estos (incluye preflights, errores, etc.)
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        return resp

    # Blueprints
    from app.routes.items import bp as items_bp
    app.register_blueprint(items_bp, url_prefix="/api/items")

    from app.routes.comments import bp as comments_bp
    app.register_blueprint(comments_bp, url_prefix="/api")

    @app.get("/api/health")
    def health():
        return jsonify(ok=True), 200

    @app.errorhandler(404)
    def not_found(e):
        return jsonify(detail="Not found"), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify(detail="Internal server error"), 500

    return app
