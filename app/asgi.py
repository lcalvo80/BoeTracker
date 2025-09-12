# app/asgi.py
from hypercorn.middleware import wsgi
from app import create_app

flask_app = create_app()
app = wsgi.WSGIMiddleware(flask_app)  # ASGI app para Hypercorn
