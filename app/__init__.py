from __future__ import annotations

import os
import logging
from flask import Flask, jsonify
from flask_cors import CORS


def create_app() -> Flask:
    app = Flask(__name__)

    # ───────────────── Config ─────────────────
    app.config.update(
        DEBUG=os.getenv("FLASK_DEBUG", "0") == "1",
        JSON_SORT_KEYS=False,
        FRONTEND_ORIGIN=os.getenv("FRONTEND_ORIGIN", "http://localhost:3000"),
        FRONTEND_BASE_URL=os.getenv("FRONTEND_BASE_URL", os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")),
        # Stripe
        STRIPE_SECRET_KEY=os.getenv("STRIPE_SECRET_KEY", ""),
        STRIPE_PRICE_PRO=os.getenv("STRIPE_PRICE_PRO", ""),
        STRIPE_PRICE_ENTERPRISE=os.getenv("STRIPE_PRICE_ENTERPRISE", ""),
        STRIPE_WEBHOOK_SECRET=os.getenv("STRIPE_WEBHOOK_SECRET", ""),
        # Clerk
        CLERK_SECRET_KEY=os.getenv("CLERK_SECRET_KEY", ""),
        CLERK_JWKS_URL=os.getenv("CLERK_JWKS_URL", ""),   # ej: https://clerk.accounts.dev/.well-known/jwks.json
        CLERK_ISSUER=os.getenv("CLERK_ISSUER", ""),       # opcional (validación iss)
        CLERK_API_BASE=os.getenv("CLERK_API_BASE", "https://api.clerk.com/v1"),
    )

    # ───────────────── CORS ─────────────────
    CORS(
        app,
        resources={r"/api/*": {"origins": [app.config["FRONTEND_ORIGIN"], app.config["FRONTEND_BASE_URL"]]}},
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization", "X-Org-Id"],
        methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    )

    # ───────────────── Blueprints ─────────────────
    from app.blueprints.billing import bp as billing_bp
    from app.blueprints.enterprise import bp as enterprise_bp
    from app.auth import int_bp  # solo se registra en DEBUG

    app.register_blueprint(billing_bp, url_prefix="/api/billing")
    app.register_blueprint(enterprise_bp, url_prefix="/api/enterprise")
    if app.config["DEBUG"]:
        app.register_blueprint(int_bp, url_prefix="/api/_int")

    # ───────────────── Health & Errores ─────────────────
    @app.route("/healthz")
    def healthz():
        return {"ok": True, "status": "healthy"}

    @app.errorhandler(404)
    def _404(_):
        return jsonify({"ok": False, "error": "Not found"}), 404

    @app.errorhandler(400)
    def _400(_):
        return jsonify({"ok": False, "error": "Bad request"}), 400

    @app.errorhandler(500)
    def _500(e):
        app.logger.exception("Unhandled error", exc_info=e)
        return jsonify({"ok": False, "error": "Internal server error"}), 500

    # Log básico
    if not app.debug:
        logging.basicConfig(level=logging.INFO)

    return app
