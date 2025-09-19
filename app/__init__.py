# app/__init__.py
import os
import sys
import json
import importlib
from pathlib import Path

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
    # fallback dev
    return ["http://localhost:3000", "http://localhost:5173"]


def create_app(config: dict | None = None):
    load_dotenv()
    app = Flask(__name__)
    app.url_map.strict_slashes = False

    # ── Logs de diagnóstico para paths y archivos ──
    app.logger.info(f"[init] cwd={os.getcwd()} root_path={app.root_path} sys.path[0]={sys.path[0]}")
    for rel in [
        "app/__init__.py",
        # ubicaciones preferidas (blueprints)
        "app/blueprints/debug.py",
        "app/blueprints/billing.py",
        "app/blueprints/webhooks.py",
        "app/blueprints/items.py",
        "app/blueprints/comments.py",
        "app/blueprints/compat.py",
        # rutas legacy (si existen)
        "app/routes/debug.py",
        "app/routes/billing.py",
        "app/routes/webhooks.py",
        "app/routes/items.py",
        "app/routes/comments.py",
        "app/routes/compat.py",
    ]:
        p = Path(app.root_path).parent / rel
        app.logger.info(f"[init] exists {rel}? {'YES' if p.exists() else 'NO'} -> {p}")

    # ── Preflight universal /api/* ──
    @app.before_request
    def _short_circuit_preflight():
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            return make_response(("", 204))

    # ── Config ──
    app.config.update(
        FRONTEND_URL=os.getenv("FRONTEND_URL", "http://localhost:5173"),
        # Clerk
        CLERK_PUBLISHABLE_KEY=os.getenv("CLERK_PUBLISHABLE_KEY", ""),
        CLERK_SECRET_KEY=os.getenv("CLERK_SECRET_KEY", ""),
        CLERK_JWKS_URL=os.getenv("CLERK_JWKS_URL", ""),
        CLERK_WEBHOOK_SECRET=os.getenv("CLERK_WEBHOOK_SECRET", ""),
        # Stripe
        STRIPE_SECRET_KEY=os.getenv("STRIPE_SECRET_KEY", ""),
        STRIPE_WEBHOOK_SECRET=os.getenv("STRIPE_WEBHOOK_SECRET", ""),
        PRICE_PRO_MONTHLY_ID=os.getenv("PRICE_PRO_MONTHLY_ID", ""),
        PRICE_ENTERPRISE_SEAT_ID=os.getenv("PRICE_ENTERPRISE_SEAT_ID", ""),
        # Flags
        DISABLE_AUTH=os.getenv("DISABLE_AUTH", "0"),
        # Flask sane defaults
        JSON_SORT_KEYS=False,
    )
    if config:
        app.config.update(config)

    # ── CORS (solo /api/*) ──
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

    # ── Helper: registra un BP si existe (blueprints primero; luego legacy routes) ──
    MODULE_ROOTS = ["app.blueprints", "app.routes"]

    def register_bp(module_name: str, attr: str, url_prefix: str) -> bool:
        """
        Busca module_name en app.blueprints y app.routes; si lo encuentra,
        registra su atributo 'attr' (normalmente 'bp') con url_prefix dado.
        """
        errors = []
        for root in MODULE_ROOTS:
            module_path = f"{root}.{module_name}"
            try:
                mod = importlib.import_module(module_path)
            except ModuleNotFoundError:
                continue
            except Exception as e:
                errors.append((module_path, f"import_error: {e}"))
                continue

            try:
                bp = getattr(mod, attr)
            except Exception as e:
                errors.append((module_path, f"missing_attr_{attr}: {e}"))
                continue

            try:
                # Si el BP ya trae url_prefix en su Blueprint, Flask ignora este parámetro;
                # lo dejamos por compat, pero debug/billing ya definen su propio url_prefix interno.
                app.register_blueprint(bp, url_prefix=None)
                app.logger.info(f"[init] Registrado BP '{bp.name}' de {module_path}")
                return True
            except Exception as e:
                errors.append((module_path, f"register_error: {e}"))

        if errors:
            for where, msg in errors:
                app.logger.error(f"[init] Fallo registrando {where}.{attr}: {msg}")
        else:
            app.logger.info(f"[init] No se encontró módulo '{module_name}' en {MODULE_ROOTS}")
        return False

    # ── Blueprints ──
    # debug.py define url_prefix="/api/debug"
    register_bp("debug", "bp", "/api/debug")

    # billing.py define BP con endpoints de billing; lo registramos tal cual.
    # Si el propio BP trae url_prefix, lo respeta; si no, quedará bajo raíz.
    register_bp("billing", "bp", "/api/billing")
    register_bp("webhooks", "bp", "/api/webhooks")

    # Otros (si existen en tu proyecto)
    register_bp("items", "bp", "/api/items")
    register_bp("comments", "bp", "/api")
    register_bp("compat", "bp", "/api")

    # ── Health ──
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"}), 200

    # ── WebSocket (opcional) ──
    sock = Sock(app)

    @sock.route("/ws")
    def ws_handler(ws):
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
