# app/routes/items.py
from flask import Blueprint, jsonify, request
from app.controllers.items_controller import (
    get_filtered_items,
    get_item_by_id,
    get_item_resumen,
    get_item_impacto,
    like_item,
    dislike_item,
    list_departamentos,
    list_epigrafes,
    list_secciones,
)

bp = Blueprint("items", __name__)

# -----------------------------
# LISTADO /api/items  (GET)
# -----------------------------
@bp.route("/", methods=["GET"])
def api_items():
    filters = request.args
    page = int(filters.get("page", 1))
    limit = int(filters.get("limit", 10))
    data = get_filtered_items(filters, page, limit)
    return jsonify(data)

# Alias sin barra final para evitar 404 si el proxy la quita
@bp.route("", methods=["GET"])
def api_items_no_slash():
    return api_items()

# ---------------------------------------
# DETALLE /api/items/<identificador> (GET)
# ---------------------------------------
@bp.route("/<identificador>", methods=["GET"])
def api_get_item(identificador):
    return jsonify(get_item_by_id(identificador))

# --------------------------------------------
# RESUMEN /api/items/<identificador>/resumen (GET)
# --------------------------------------------
@bp.route("/<identificador>/resumen", methods=["GET"])
def api_get_resumen(identificador):
    return jsonify(get_item_resumen(identificador))

# ------------------------------------------------
# IMPACTO /api/items/<identificador>/impacto (GET)
# ------------------------------------------------
@bp.route("/<identificador>/impacto", methods=["GET"])
def api_get_impacto(identificador):
    return jsonify(get_item_impacto(identificador))

# --------------------------------------------
# LIKE /api/items/<identificador>/like (PUT)
# DISLIKE /api/items/<identificador>/dislike (PUT)
# --------------------------------------------
@bp.route("/<identificador>/like", methods=["PUT"])
def api_like(identificador):
    return jsonify(like_item(identificador))

@bp.route("/<identificador>/dislike", methods=["PUT"])
def api_dislike(identificador):
    return jsonify(dislike_item(identificador))

# --------------------------------------------
# LOOKUPS
# /api/items/departamentos (GET)
# /api/items/secciones     (GET)
# /api/items/epigrafes     (GET)
# --------------------------------------------
@bp.route("/departamentos", methods=["GET"])
def api_departamentos():
    return jsonify(list_departamentos())

@bp.route("/secciones", methods=["GET"])
def api_secciones():
    return jsonify(list_secciones())

@bp.route("/epigrafes", methods=["GET"])
def api_epigrafes():
    return jsonify(list_epigrafes())
