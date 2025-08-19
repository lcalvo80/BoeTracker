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

@bp.route("", methods=["GET"])
def api_items():
    data = get_filtered_items(request.args.to_dict())
    return jsonify(data), 200

@bp.route("/<identificador>", methods=["GET"])
def api_item_by_id(identificador):
    data = get_item_by_id(identificador)
    if not data:
        return jsonify({"detail": "Not found"}), 404
    return jsonify(data), 200

@bp.route("/<identificador>/resumen", methods=["GET"])
def api_resumen(identificador):
    return jsonify(get_item_resumen(identificador)), 200

@bp.route("/<identificador>/impacto", methods=["GET"])
def api_impacto(identificador):
    return jsonify(get_item_impacto(identificador)), 200

@bp.route("/<identificador>/like", methods=["POST"])
def api_like(identificador):
    return jsonify(like_item(identificador)), 200

@bp.route("/<identificador>/dislike", methods=["POST"])
def api_dislike(identificador):
    return jsonify(dislike_item(identificador)), 200

@bp.route("/departamentos", methods=["GET"])
def api_departamentos():
    return jsonify(list_departamentos()), 200

@bp.route("/secciones", methods=["GET"])
def api_secciones():
    return jsonify(list_secciones()), 200

@bp.route("/epigrafes", methods=["GET"])
def api_epigrafes():
    return jsonify(list_epigrafes()), 200
