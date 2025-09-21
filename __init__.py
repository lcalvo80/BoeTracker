# app/__init__.py
from __future__ import annotations
from flask import Flask

def create_app() -> Flask:
    app = Flask(__name__)

    # Registra SOLO el blueprint de app/blueprints/billing.py
    from .blueprints.billing import bp as billing_bp
    app.register_blueprint(billing_bp)

    # (opcional) endpoint de claims interno si tienes uno:
    # intenta importar app.auth de forma lazy y si no, loguea y devuelve 501.
    @app.get("/api/_int/claims")
    def int_claims():
        try:
            from .auth import _truthy  # prueba de que existe el módulo
        except Exception:
            app.logger.warning("[init] Auth no disponible (No module named 'app.auth'); /api/_int/claims devolverá 501")
            return {"error": "auth_not_available"}, 501
        return {"ok": True}, 200

    @app.get("/healthz")
    def healthz():
        return {"ok": True}, 200

    return app
