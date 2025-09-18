# app/__init__.py
import os
import sys
import json
import importlib
from pathlib import Path
from urllib.parse import parse_qs

from flask import Flask, jsonify, make_response, request
from flask_cors import CORS
from flask_sock import Sock
from dotenv import load_dotenv


def _parse_origins():
    raw = os.getenv("ALLOWED_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    single = os.getenv("FRONTEND_ORIGIN", "").strip()
    if single:
        return [single]
    return ["http://localhost:3000", "http://localhost:5173"]


def create_app(config: dict | None = None):
    load_dotenv()
    app = Flask(__name__)
    app.url_map.strict_slashes = False

    # ── Logs de diagnóstico para paths y archivos ──
    app.logger.info(f"[init] cwd={os.getcwd()} root_path={app.root_path} sys.path[0]={sys.path[0]}")
    for rel in [
        "app/__init__.py",
        "app/routes/__init__.py",
        "app/routes/billing.py",
        "app/routes/debug.py",
        "app/routes/webhooks.py",
    ]:
        p = Path(app.root_path).parent / rel  # app.root_path suele ser /app/app
        app.logger.info(f"[init] exists {rel}? {'YES' if p.exists() else 'NO'} -> {p}")

    @app.before_request
    def _short_circuit_preflight():
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            return make_response(("", 204))

    app.config.update(
        FRONTEND_URL=os.getenv("FRONTEND_URL", "http://localhost:5173"),
        CLERK_PUBLISHABLE_KEY=os.getenv("CLERK_PUBLISHABLE_KEY", ""),
        CLERK_SECRET_KEY=os.getenv("CLERK_SECRET_KEY", ""),
        CLERK_JWKS_URL=os.getenv("CLERK_JWKS_URL", ""),
        STRIPE_SECRET_KEY=os.getenv("STRIPE_SECRET_KEY", ""),
        STRIPE_WEBHOOK_SECRET=os.getenv("STRIPE_WEBHOOK_SECRET", ""),
        PRICE_PRO_MONTHLY_ID=os.getenv("PRICE_PRO_MONTHLY_ID", ""),
        PRICE_ENTERPRISE_SEAT_ID=os.getenv("PRICE_ENTERPRISE_SEAT_ID", ""),
        JSON_SORT_KEYS=False,
    )
    if config:
        app.config.update(config)

    origins = _parse_origins()
    CORS(
        app,
        resources={r"/api/.*": {"origins": origins}},
        supports_credentials=True,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Requested-With", "X-Debug-Filters"],
        expose_headers=["X-Total-Count", "Content-Range"],
        max_age=86400,
    )

    def _try_register(module_path: str, attr: str, url_prefix: str) -> bool:
        try:
            mod = importlib.import_module(module_path)
            bp = getattr(mod, attr)
            app.register_blueprint(bp, url_prefix=url_prefix)
            app.logger.info(f"[init] registrado {module_path}.{attr} en {url_prefix}")
            return True
        except Exception as e:
            app.logger.error(f"[init] NO se pudo registrar {module_path}.{attr}: {e}")
            return False

    # Debug (si falla, monta fallback)
    if not _try_register("app.routes.debug", "bp", "/api/_debug"):
        from flask import Blueprint, current_app
        debug_bp = Blueprint("debug_fallback", __name__)

        @debug_bp.get("/routes")
        def _routes_list():
            rules = []
            for r in current_app.url_map.iter_rules():
                rules.append({
                    "endpoint": r.endpoint,
                    "methods": sorted(m for m in r.methods if m in {"GET","POST","PUT","PATCH","DELETE","OPTIONS"}),
                    "rule": str(r.rule),
                })
            return jsonify(sorted(rules, key=lambda x: x["rule"]))

        @debug_bp.route("/echo", methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"])
        def _echo():
            return jsonify({
                "method": request.method,
                "path": request.path,
                "headers": {k: v for k, v in request.headers.items()},
                "json": request.get_json(silent=True),
            })

        app.register_blueprint(debug_bp, url_prefix="/api/_debug")
        app.logger.warning("[init] debug fallback montado en /api/_debug")

    # Core (si existen)
    _try_register("app.routes.items", "bp", "/api/items")
    _try_register("app.routes.comments", "bp", "/api")
    _try_register("app.routes.compat", "bp", "/api")

    # Billing (si falla, monta fallback temporal para evitar 404)
    if not _try_register("app.routes.billing", "bp", "/api/billing"):
        from flask import Blueprint, abort
        billing_bp = Blueprint("billing_fallback", __name__)

        @billing_bp.post("/checkout")
        def _checkout_fallback():
            body = request.get_json(silent=True) or {}
            if not body.get("price_id"):
                abort(400, "price_id required (fallback)")
            return jsonify({"checkout_url": "https://example/checkout/fallback"})

        @billing_bp.post("/portal")
        def _portal_fallback():
            return jsonify({"portal_url": "https://example/portal/fallback"})

        app.register_blueprint(billing_bp, url_prefix="/api/billing")
        app.logger.warning("[init] billing fallback montado en /api/billing")

    # Webhooks (si existe)
    _try_register("app.routes.webhooks", "bp", "/api/webhooks")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"}), 200

    sock = Sock(app)

    @sock.route("/ws")
    def ws_handler(ws):
        try:
            qs = ws.environ.get("QUERY_STRING", "")
            _ = qs
        except Exception:
            pass

        ws.send(json.dumps({"type": "hello", "msg": "ws up"}))
        while True:
            data = ws.receive()
            if data is None:
                break
            try:
                payload = json.loads(data)
            except Exception:
                payload = data
            if isinstance(payload, dict) and payload.get("type") == "ping":
                ws.send(json.dumps({"type": "pong"}))
            elif payload == "ping":
                ws.send("pong")
            else:
                ws.send(json.dumps({"type": "echo", "data": payload}))

    return app
