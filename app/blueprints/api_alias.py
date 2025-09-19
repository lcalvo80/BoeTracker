# app/blueprints/api_alias.py
from __future__ import annotations
from flask import Blueprint, jsonify, request, current_app

bp = Blueprint("api_alias", __name__, url_prefix="/api")

# ─────────── Imports de vistas existentes (sin prefijo) ───────────
# Ajustados a lo que ya vimos en /api/_int/routes
try:
    from app.blueprints.compat import filters_options as _filters_options, meta_filters as _meta_filters
except Exception as e:
    _filters_options = None  # type: ignore
    _meta_filters = None     # type: ignore
    import_error_compat = e
else:
    import_error_compat = None

try:
    from app.blueprints.billing import (
        create_checkout as _create_checkout,
        create_portal as _create_portal,
        sync_after_success as _sync_after_success,
    )
except Exception as e:
    _create_checkout = _create_portal = _sync_after_success = None  # type: ignore
    import_error_billing = e
else:
    import_error_billing = None

# Si en el futuro tienes un listado de items, impórtalo aquí:
try:
    # EJEMPLO: from app.blueprints.items import list_items as _list_items
    _list_items = None  # type: ignore
except Exception:
    _list_items = None  # type: ignore


# ─────────── Handlers/alias ───────────

# GET /api/filters  -> compat.filters_options
@bp.get("/filters")
def api_filters():
    if not _filters_options:
        current_app.logger.error("api_alias: compat.filters_options no disponible: %s", import_error_compat)
        return jsonify({"error": "filters endpoint not available"}), 501
    return _filters_options()  # llama a tu handler real

# GET /api/meta/filters -> compat.meta_filters
@bp.get("/meta/filters")
def api_meta_filters():
    if not _meta_filters:
        current_app.logger.error("api_alias: compat.meta_filters no disponible: %s", import_error_compat)
        return jsonify({"error": "meta filters endpoint not available"}), 501
    return _meta_filters()

# POST /api/checkout -> billing.create_checkout
@bp.post("/checkout")
def api_checkout():
    if not _create_checkout:
        current_app.logger.error("api_alias: billing.create_checkout no disponible: %s", import_error_billing)
        return jsonify({"error": "checkout endpoint not available"}), 501
    return _create_checkout()

# POST /api/portal -> billing.create_portal
@bp.post("/portal")
def api_portal():
    if not _create_portal:
        current_app.logger.error("api_alias: billing.create_portal no disponible: %s", import_error_billing)
        return jsonify({"error": "portal endpoint not available"}), 501
    return _create_portal()

# POST /api/sync -> billing.sync_after_success
@bp.post("/sync")
def api_sync():
    if not _sync_after_success:
        current_app.logger.error("api_alias: billing.sync_after_success no disponible: %s", import_error_billing)
        return jsonify({"error": "sync endpoint not available"}), 501
    return _sync_after_success()

# GET /api/items -> listado (placeholder si aún no existe)
@bp.get("/items")
def api_items():
    if _list_items:
        # Passthrough si tienes un handler real
        return _list_items()
    # Placeholder para que el frontend no reciba 404 y puedas ver el wiring
    # Lee algunos params habituales del frontend:
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 12))
    return jsonify({
        "items": [],
        "page": page,
        "limit": limit,
        "total": 0,
        "message": "Items list not implemented in backend; using /api alias placeholder."
    }), 200
