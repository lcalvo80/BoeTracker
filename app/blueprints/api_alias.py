# app/blueprints/api_alias.py
from __future__ import annotations
from flask import Blueprint

# Importa las vistas existentes (ya registradas sin prefijo)
# Ajusta estos imports a tus módulos reales:
from app.blueprints.compat import (
    filters_options as _filters_options,
    meta_filters as _meta_filters,
)
from app.blueprints.billing import (
    create_checkout as _create_checkout,
    create_portal as _create_portal,
    sync_after_success as _sync_after_success,
)
# Si tienes endpoints de items/comments sin prefijo:
from app.blueprints.comments import (
    list_item_comments as _list_item_comments,
    add_item_comment as _add_item_comment,
)

bp = Blueprint("api_alias", __name__, url_prefix="/api")

# GET /api/filters  -> /filters
bp.add_url_rule("/filters", view_func=_filters_options, methods=["GET"])

# GET /api/meta/filters -> /meta/filters
bp.add_url_rule("/meta/filters", view_func=_meta_filters, methods=["GET"])

# POST /api/checkout -> /checkout
bp.add_url_rule("/checkout", view_func=_create_checkout, methods=["POST"])

# POST /api/portal -> /portal
bp.add_url_rule("/portal", view_func=_create_portal, methods=["POST"])

# POST /api/sync -> /sync
bp.add_url_rule("/sync", view_func=_sync_after_success, methods=["POST"])

# Comments (ajusta el path param si en tu vista es distinto)
bp.add_url_rule("/items/<ident>/comments", view_func=_list_item_comments, methods=["GET"])
bp.add_url_rule("/items/<ident>/comments", view_func=_add_item_comment, methods=["POST"])
