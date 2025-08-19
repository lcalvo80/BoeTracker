# app/__init__.py
from flask import Flask, jsonify, request
from flask_cors import CORS

ALLOWED_ORIGINS = [
    "http://localhost:3000",
    # añade tu dominio de producción del frontend cuando lo tengas:
    # "https://tu-frontend.com",
]

def create_app():
    app = Flask(__name__)
    app.url_map.strict_slashes = False

    # CORS
    CORS(
        app,
        resources={r"/api/*": {
            "origins": ALLOWED_ORIGINS,
            "supports_credentials": True,
            "allow_headers": ["Content-Type", "Authorization"],
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
        }},
    )

    # Asegura CORS también en respuestas de error
    @app.after_request
    def add_cors_headers(resp):
        origin = request.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
        return resp

    # Blueprints
    from app.routes.items import bp as items_bp
    app.register_blueprint(items_bp, url_prefix="/api/items")

    # Health simple
    @app.get("/api/health")
    def health():
        return jsonify(ok=True), 200

    # Handlers de error (mantienen JSON + CORS)
    @app.errorhandler(404)
    def not_found(e):
        return jsonify(detail="Not found"), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify(detail="Internal server error"), 500

    return app
