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

        # Frontend
        FRONTEND_ORIGIN=os.getenv("FRONTEND_ORIGIN", "http://localhost:3000"),
        FRONTEND_BASE_URL=os.getenv("FRONTEND_BASE_URL", os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")),

        # Feature flags (dev)
        DEBUG_FILTERS_ENABLED=os.getenv("DEBUG_FILTERS_ENABLED", "0") == "1",

        # Stripe
        STRIPE_SECRET_KEY=os.getenv("STRIPE_SECRET_KEY", ""),
        STRIPE_PRICE_PRO=os.getenv("STRIPE_PRICE_PRO", ""),
        STRIPE_PRICE_ENTERPRISE=os.getenv("STRIPE_PRICE_ENTERPRISE", ""),
        STRIPE_WEBHOOK_SECRET=os.getenv("STRIPE_WEBHOOK_SECRET", ""),

        # Clerk
        CLERK_SECRET_KEY=os.getenv("CLERK_SECRET_KEY", ""),
        CLERK_JWKS_URL=os.getenv("CLERK_JWKS_URL", ""),   # p.ej., https://<sub>.clerk.accounts.dev/.well-known/jwks.json
        CLERK_ISSUER=os.getenv("CLERK_ISSUER", ""),       # p.ej., https://<sub>.clerk.accounts.dev
        CLERK_API_BASE=os.getenv("CLERK_API_BASE", "https://api.clerk.com/v1"),
        CLERK_AUDIENCE=os.getenv("CLERK_AUDIENCE", ""),   # opcional; si se define, verificamos 'aud'
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
    # Canónicos:
    # - /api/stripe  (webhook Stripe)
    # - /api/clerk   (webhook Clerk)
    from app.blueprints.webhooks import bp as webhooks_bp
    from app.blueprints.billing import bp as billing_bp
    from app.blueprints.enterprise import bp as enterprise_bp

    # Nuevos: items / meta / comments
    from app.blueprints.items import bp as items_bp
    from app.blueprints.meta import bp as meta_bp
    from app.blueprints.comments import bp as comments_bp

    # DEV only (_int/claims)
    from app.auth import int_bp  # solo en DEBUG

    # Registro (IMPORTANTE: webhooks bajo /api para exponer /api/stripe y /api/clerk)
    app.register_blueprint(webhooks_bp, url_prefix="/api")
    app.register_blueprint(billing_bp, url_prefix="/api/billing")
    app.register_blueprint(enterprise_bp, url_prefix="/api/enterprise")

    # Items & Comments: mismos prefijos que espera el FE
    app.register_blueprint(items_bp, url_prefix="/api/items")
    app.register_blueprint(comments_bp, url_prefix="/api/items")  # expone /api/items/<ident>/comments
    app.register_blueprint(meta_bp, url_prefix="/api/meta")       # /api/meta/filters

    if app.config["DEBUG"]:
        app.register_blueprint(int_bp, url_prefix="/api/_int")

    # ───────────────── Salud y errores ─────────────────
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

    if not app.debug:
        logging.basicConfig(level=logging.INFO)

    return app
