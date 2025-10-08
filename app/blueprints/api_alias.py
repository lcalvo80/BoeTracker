from __future__ import annotations
from flask import Blueprint, jsonify, request, current_app

bp = Blueprint("api_alias", __name__, url_prefix="/api")

def _call_endpoint(endpoint_name: str, *args, **kwargs):
    func = current_app.view_functions.get(endpoint_name)
    if not func:
        current_app.logger.error("api_alias: endpoint '%s' no encontrado", endpoint_name)
        return jsonify({"error": f"endpoint '{endpoint_name}' not available"}), 501
    return func(*args, **kwargs)

# ── compat filtros legacy ──
@bp.get("/filters")
def api_filters():
    return _call_endpoint("compat.filters_options")

@bp.get("/meta/filters")
def api_meta_filters():
    return _call_endpoint("compat.meta_filters")

# ── compat items/comments (si existen) ──
@bp.get("/items")
def api_items():
    for name in ("items.api_items", "items.list_items"):
        func = current_app.view_functions.get(name)
        if func:
            return func()
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 12))
    return jsonify({
        "items": [],
        "page": page,
        "limit": limit,
        "total": 0,
        "message": "Items list placeholder; implementa blueprint items.",
    }), 200

@bp.get("/items/<ident>/comments")
def api_list_item_comments(ident):
    return _call_endpoint("comments.list_item_comment", ident)  # si tu endpoint se llama distinto, ajusta aquí

@bp.post("/items/<ident>/comments")
def api_add_item_comment(ident):
    return _call_endpoint("comments.add_item_comment", ident)

# ⚠️ Nada de /checkout /portal /sync aquí. Billing ya los expone directamente.

# === FE compatibility aliases: enterprise & billing ===

# El front pide /api/enterprise/org/info → mapea a enterprise.org_info (tu ruta real es /api/enterprise/org)
@bp.get("/enterprise/org/info")
def api_enterprise_org_info_alias():
    return _call_endpoint("enterprise.org_info")

# El front pide /api/billing/open-customer-portal → mapea a billing.portal_post / billing.portal_get
@bp.post("/billing/open-customer-portal")
def api_billing_open_portal_post():
    return _call_endpoint("billing.portal_post")

@bp.get("/billing/open-customer-portal")
def api_billing_open_portal_get():
    return _call_endpoint("billing.portal_get")
