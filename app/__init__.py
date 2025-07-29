from flask import Flask
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    CORS(app)

    from app.routes import items, comments
    app.register_blueprint(items.bp, url_prefix="/api/items")
    app.register_blueprint(comments.bp_comments, url_prefix="/api")

    return app
