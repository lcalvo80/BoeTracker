# app/__init__.py
from flask import Flask, jsonify
from flask_cors import CORS

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
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    }})

    # Importa y registra blueprints SOLO aquí (runtime del servidor)
    from app.routes.items import bp as items_bp
    from app.routes.comments import bp as comments_bp

    app.register_blueprint(items_bp,    url_prefix="/api/items")
    app.register_blueprint(comments_bp, url_prefix="/api/comments")

    # Ruta de salud
    @app.get("/api/health")
    def health():
        return jsonify(ok=True)

    return app
