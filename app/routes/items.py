# items.py
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
    list_secciones
)

bp = Blueprint("items", __name__)

@bp.route("/", methods=["GET"])
def api_items():
    filters = request.args
    page = int(filters.get("page", 1))
    limit = int(filters.get("limit", 10))
    return jsonify(get_filtered_items(filters, page, limit))

@bp.route("/<identificador>", methods=["GET"])
def api_get_item(identificador):
    return jsonify(get_item_by_id(identificador))

@bp.route("/<identificador>/resumen", methods=["GET"])
def api_get_resumen(identificador):
    return jsonify(get_item_resumen(identificador))

@bp.route("/<identificador>/impacto", methods=["GET"])
def api_get_impacto(identificador):
    return jsonify(get_item_impacto(identificador))

@bp.route("/<identificador>/like", methods=["PUT"])
def api_like(identificador):
    return jsonify(like_item(identificador))

@bp.route("/<identificador>/dislike", methods=["PUT"])
def api_dislike(identificador):
    return jsonify(dislike_item(identificador))

@bp.route("/departamentos", methods=["GET"])
def api_departamentos():
    return jsonify(list_departamentos())

@bp.route("/secciones", methods=["GET"])
def api_secciones():
    return jsonify(list_secciones())

@bp.route("/epigrafes", methods=["GET"])
def api_epigrafes():
    return jsonify(list_epigrafes())
