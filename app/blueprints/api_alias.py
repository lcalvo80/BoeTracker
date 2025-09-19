# app/blueprints/api_alias.py
from __future__ import annotations
from flask import Blueprint, jsonify, request, current_app

bp = Blueprint("api_alias", __name__, url_prefix="/api")

def _call_endpoint(endpoint_name: str, *args, **kwargs):
    """
    Busca y ejecuta el handler ya registrado en Flask por nombre de endpoint.
    Evita imports y problemas de orden/circularidad.
    """
    func = current_app.view_functions.get(endpoint_name)
    if not func:
        current_app.logger.error("api_alias: endpoint '%s' no encontrado", endpoint_name)
        return jsonify({"error": f"endpoint '{endpoint_name}' not available"}), 501
    return func(*args, **kwargs)

# ─────────── GET /api/filters -> compat.filters_options ───────────
@bp.get("/filters")
def api_filters():
    return _call_endpoint("compat.filters_options")

# ─────────── GET /api/meta/filters -> compat.meta_filters ───────────
@bp.get("/meta/filters")
def api_meta_filters():
    return _call_endpoint("compat.meta_filters")

# ─────────── POST /api/checkout -> billing.create_checkout ───────────
@bp.post("/checkout")
def api_checkout():
    return _call_endpoint("billing.create_checkout")

# ─────────── POST /api/portal -> billing.create_portal ───────────
@bp.post("/portal")
def api_portal():
    return _call_endpoint("billing.create_portal")

# ─────────── POST /api/sync -> billing.sync_after_success ───────────
@bp.post("/sync")
def api_sync():
    return _call_endpoint("billing.sync_after_success")

# ─────────── GET /api/items -> alias al listado si existe ───────────
@bp.get("/items")
def api_items():
    # Si has registrado un blueprint 'items' con endpoint 'items.api_items', úsalo:
    # (ajusta el nombre si tu función se llama distinto en el blueprint real)
    # Fallback: placeholder para no romper el FE mientras implementas
    func_name_candidates = [
        "items.api_items",     # si tu función se llama api_items dentro del BP 'items'
        "items.list_items",    # otro nombre típico
    ]
    for name in func_name_candidates:
        func = current_app.view_functions.get(name)
        if func:
            return func()
    # Placeholder
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 12))
    return jsonify({
        "items": [],
        "page": page,
        "limit": limit,
        "total": 0,
        "message": "Items list not implemented in backend; using /api alias placeholder."
    }), 200

# ─────────── Comments (si las vistas existen) ───────────
@bp.get("/items/<ident>/comments")
def api_list_item_comments(ident):
    return _call_endpoint("comments.list_item_comments", ident)

@bp.post("/items/<ident>/comments")
def api_add_item_comment(ident):
    return _call_endpoint("comments.add_item_comment", ident)
