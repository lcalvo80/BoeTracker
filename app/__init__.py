from flask import Flask
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    CORS(app)

    # 📦 Importamos y registramos los blueprints de rutas
    from app.routes import items, comments

    # ✅ Las rutas del blueprint 'items' empiezan por /api/items/
    app.register_blueprint(items.bp, url_prefix="/api/items")

    # ✅ Las rutas del blueprint 'comments' empiezan por /api/comments/
    app.register_blueprint(comments.bp_comments, url_prefix="/api/comments")

    return app
