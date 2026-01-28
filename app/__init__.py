# app/__init__.py
from __future__ import annotations

import os
import logging
from flask import Flask, jsonify
from flask_cors import CORS


def _build_cors_origins(app: Flask):
    cand = [
        app.config.get("FRONTEND_ORIGIN"),
        app.config.get("FRONTEND_BASE_URL"),
        os.getenv("ADDITIONAL_FRONTEND_ORIGIN", ""),
    ]
    # filtra vacíos y duplicados
    seen, out = set(), []
    for o in cand:
        if not o:
            continue
        v = str(o).strip()
        # Normaliza para evitar mismatches por slash final (muy común en envs)
        if v != "*":
            v = v.rstrip("/")
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    # ⚠️ Ojo: con supports_credentials=True, "*" no funciona en navegadores.
    # Solo lo usamos en DEV para pruebas sin credenciales.
    if app.config.get("DEBUG") and not out:
        out = ["*"]
    return out


def create_app() -> Flask:
    app = Flask(__name__)

    # ───────────────── Config ─────────────────
    app.config.update(
        DEBUG=os.getenv("FLASK_DEBUG", "0") == "1",
        JSON_SORT_KEYS=False,

        # Frontend
        FRONTEND_ORIGIN=os.getenv("FRONTEND_ORIGIN", "http://localhost:3000"),
        FRONTEND_BASE_URL=os.getenv(
            "FRONTEND_BASE_URL",
            os.getenv("FRONTEND_ORIGIN", "http://localhost:3000"),
        ),

        # Feature flags (dev)
        DEBUG_FILTERS_ENABLED=os.getenv("DEBUG_FILTERS_ENABLED", "0") == "1",

        # ✅ Subscriptions cache (Stripe live)
        # Recomendación: DEV 10–30, PROD 60–120
        SUB_CACHE_TTL_S=int(os.getenv("SUB_CACHE_TTL_S", "60")),

        # ✅ Feature flag: enforcement de suscripción
        # MVP (sin paywall): 0 (default)
        # Para reactivar el gating por suscripción en el futuro: 1
        REQUIRE_ACTIVE_SUBSCRIPTION=os.getenv("REQUIRE_ACTIVE_SUBSCRIPTION", "0") == "1",

        # (Opcional) exponer motivos de denegación en 403 cuando DEBUG=1
        DEBUG_SUBSCRIPTION=os.getenv("DEBUG_SUBSCRIPTION", "0") == "1",

        # Stripe
        STRIPE_SECRET_KEY=os.getenv("STRIPE_SECRET_KEY", ""),
        STRIPE_PRICE_PRO=os.getenv("STRIPE_PRICE_PRO", ""),
        STRIPE_PRICE_ENTERPRISE=os.getenv("STRIPE_PRICE_ENTERPRISE", ""),
        STRIPE_WEBHOOK_SECRET=os.getenv("STRIPE_WEBHOOK_SECRET", ""),

        # Clerk
        CLERK_SECRET_KEY=os.getenv("CLERK_SECRET_KEY", ""),
        CLERK_JWKS_URL=os.getenv("CLERK_JWKS_URL", ""),  # https://<sub>.clerk.../jwks.json
        CLERK_ISSUER=os.getenv("CLERK_ISSUER", ""),      # https://<sub>.clerk...
        CLERK_API_BASE=os.getenv("CLERK_API_BASE", "https://api.clerk.com/v1"),
        CLERK_AUDIENCE=os.getenv("CLERK_AUDIENCE", ""),
    )

    # ───────────────── CORS ─────────────────
    cors_origins = _build_cors_origins(app)
    CORS(
        app,
        resources={
            r"/api/*": {
                "origins": cors_origins,
                "allow_headers": ["Content-Type", "Authorization", "X-Org-Id"],
                "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            }
        },
        supports_credentials=True,
    )

    # ───────────────── Blueprints ─────────────────
    # Canónicos:
    from app.blueprints.webhooks import bp as webhooks_bp   # /api/stripe y /api/clerk
    from app.blueprints.billing import bp as billing_bp
    from app.blueprints.enterprise import bp as enterprise_bp

    # Nuevos: items / meta / comments
    from app.blueprints.items import bp as items_bp
    from app.blueprints.meta import bp as meta_bp
    from app.blueprints.comments import bp as comments_bp

    # IA BOE: título + resumen + impacto (SIEMPRE PDF)
    from app.blueprints.ai_boe import bp as ai_boe_bp

    # ✅ Resumen diario público (sin login)
    from app.blueprints.resumen import bp as resumen_bp

    # DEV only (_int: endpoints de prueba OpenAI y utilidades)
    from app.auth import int_bp  # expone /api/_int/* SOLO en DEBUG

    # Registro
    app.register_blueprint(webhooks_bp, url_prefix="/api")
    app.register_blueprint(billing_bp, url_prefix="/api/billing")
    app.register_blueprint(enterprise_bp, url_prefix="/api/enterprise")
    app.register_blueprint(items_bp, url_prefix="/api/items")
    app.register_blueprint(comments_bp, url_prefix="/api/items")  # /api/items/<ident>/comments
    app.register_blueprint(meta_bp, url_prefix="/api/meta")       # /api/meta/filters

    # ✅ Nuevas APIs IA BOE
    app.register_blueprint(ai_boe_bp, url_prefix="/api/ai")

    # ✅ Resumen diario (público): /api/resumen/*
    app.register_blueprint(resumen_bp, url_prefix="/api/resumen")

    if app.config["DEBUG"]:
        app.register_blueprint(int_bp, url_prefix="/api/_int")

        # Pequeña introspección de rutas: útil tras deploy en dev
        @app.get("/api/_int/routes")
        def _routes():
            routes = []
            for r in app.url_map.iter_rules():
                if str(r.rule).startswith("/api/"):
                    routes.append({
                        "rule": str(r.rule),
                        "methods": sorted(m for m in r.methods if m not in {"HEAD", "OPTIONS"}),
                        "endpoint": r.endpoint,
                    })
            routes.sort(key=lambda x: x["rule"])
            return {"ok": True, "routes": routes}

    # ───────────────── Salud y errores ─────────────────
    @app.route("/health")
    def health():
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
