# app/__init__.py
from flask import Flask, jsonify
from flask_cors import CORS
from app.routes import items, comments  # tus blueprints

def create_app():
    app = Flask(__name__)
    app.url_map.strict_slashes = False  # evita 404 por barra final

    CORS(app, resources={r"/api/*": {
        "origins": [
            "http://localhost:3000",                 # dev
            "https://<TU-FRONTEND-PROD>"             # prod (Netlify/Vercel/etc)
        ],
        "supports_credentials": True,
        "allow_headers": ["Content-Type", "Authorization"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    }})

    app.register_blueprint(items.bp,    url_prefix="/api/items")  # ⬅️ antes: "/api"
    app.register_blueprint(comments.bp, url_prefix="/api")        # o el que toque para comments

    @app.get("/api/health")
    def health():
        return jsonify(ok=True)

    return app
