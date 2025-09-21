# app/__init__.py
from __future__ import annotations

from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__)

    # Carga de configuración adicional si la necesitas:
    # app.config.from_mapping(...)

    # Registro de blueprints (seguro por cambios en billing.py)
    from .blueprints.billing import bp as billing_bp
    app.register_blueprint(billing_bp)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}, 200

    return app
