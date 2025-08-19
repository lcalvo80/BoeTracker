# app/__init__.py
from flask import Flask, jsonify
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    app.url_map.strict_slashes = False

    # CORS: adapta dominios de producción cuando despliegues el frontend
    CORS(
        app,
        resources={r"/api/*": {
            "origins": [
                "http://localhost:3000",
                "https://tu-frontend-prod.vercel.app"  # <-- AJUSTA
            ],
            "supports_credentials": True,
            "allow_headers": ["Content-Type", "Authorization"],
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
        }}
    )

    # Registra blueprints
    from app.routes.items import bp as items_bp
    from app.routes.comments import bp as comments_bp  # si los usas

    app.register_blueprint(items_bp,    url_prefix="/api/items")
    app.register_blueprint(comments_bp, url_prefix="/api/comments")

    @app.get("/api/health")
    def health():
        return jsonify(ok=True), 200

    return app
