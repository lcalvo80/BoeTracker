from flask import Flask, jsonify
from flask_cors import CORS
from app.routes import items, comments

def create_app():
    app = Flask(__name__)

    # CORS: ajusta con tu dominio de frontend en prod
    CORS(app, resources={r"/api/*": {
        "origins": [
            "http://localhost:3000",
            "https://<TU-FRONTEND-DOMINIO>"
        ]
    }})

    # REGISTRO DE RUTAS: variante A (prefijo /api y rutas con /items dentro del blueprint)
    app.register_blueprint(items.bp,    url_prefix="/api")
    app.register_blueprint(comments.bp, url_prefix="/api")

    @app.get("/api/health")
    def health():
        return jsonify(ok=True)

    return app
