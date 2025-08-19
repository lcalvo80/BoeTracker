# app/__init__.py
from flask import Flask, jsonify
from flask_cors import CORS
from app.routes import items, comments  # blueprints

def create_app():
    app = Flask(__name__)
    app.url_map.strict_slashes = False  # evita 404 por barra final

    # CORS solo para /api/*
    CORS(app, resources={r"/api/*": {
        "origins": [
            "http://localhost:3000",        # dev
            # Añade aquí tu dominio de frontend en prod, por ejemplo:
            # "https://boe-tracker.vercel.app",
        ],
        "supports_credentials": True,
        "allow_headers": ["Content-Type", "Authorization"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    }})

    # ----- Blueprints -----
    # Items bajo /api/items → p.ej. /api/items, /api/items/departamentos, /api/items/<id>
    app.register_blueprint(items.bp, url_prefix="/api/items")

    # Comments bajo /api → p.ej. /api/comments/<identificador>, /api/comments
    app.register_blueprint(comments.bp, url_prefix="/api")

    # Healthcheck
    @app.get("/api/health")
    def health():
        return jsonify(ok=True)

    return app
