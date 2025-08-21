# app/__init__.py
from flask import Flask, jsonify, request
from flask_cors import CORS
import os

# Usa env para orígenes permitidos en prod
DEFAULT_ORIGINS = {
    "http://localhost:3000",
}
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN")  # p.ej. https://tu-frontend.com
if FRONTEND_ORIGIN:
    DEFAULT_ORIGINS.add(FRONTEND_ORIGIN)

ALLOWED_ORIGINS = DEFAULT_ORIGINS

def create_app():
    app = Flask(__name__)
    app.url_map.strict_slashes = False

    # CORS sólo para /api/*
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
        # Refuerza CORS también en errores
        origin = request.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
        return resp

    # ====== Blueprints ======
    # items: expone /api/items/... (ej: /api/items/<ident>/impacto)
    from app.routes.items import bp as items_bp
    app.register_blueprint(items_bp, url_prefix="/api/items")

    # comments: expone /api/items/<ident>/comments
    # OJO: este blueprint define rutas con prefijo "/items/..."; por eso lo montamos en "/api"
    from app.routes.comments import bp as comments_bp
    app.register_blueprint(comments_bp, url_prefix="/api")

    # ====== Healthcheck ======
    @app.get("/api/health")
    def health():
        return jsonify(ok=True), 200

    # ====== Error handlers ======
    @app.errorhandler(404)
    def not_found(e):
        return jsonify(detail="Not found"), 404

    @app.errorhandler(500)
    def server_error(e):
        # En prod no exponemos traza
        return jsonify(detail="Internal server error"), 500

    return app
