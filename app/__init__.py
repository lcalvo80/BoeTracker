# app/__init__.py
from flask import Flask, jsonify
from flask_cors import CORS
from app.routes import items, comments  # blueprints

def create_app():
    app = Flask(__name__)
    app.url_map.strict_slashes = False  # evita 404 por barra final

    # Configuración de CORS
    CORS(app, resources={r"/api/*": {
        "origins": [
            "http://localhost:3000",                  # dev
            "https://<TU-FRONTEND-PROD>.vercel.app",  # prod (ajusta)
        ],
        "supports_credentials": True,
        "allow_headers": ["Content-Type", "Authorization"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    }})

    # Registrar Blueprints (url_prefix SOLO aquí)
    app.register_blueprint(items.bp, url_prefix="/api/items")
    app.register_blueprint(comments.bp, url_prefix="/api/comments")

    # Ruta de salud
    @app.get("/api/health")
    def health():
        return jsonify(ok=True)

    return app
