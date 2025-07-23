from flask import Blueprint, jsonify, request
from app.controllers.items_controller import (
    get_item_by_id, get_item_resumen, get_item_impacto,
    like_item, dislike_item, get_filtered_items,
    list_departamentos, list_epigrafes, list_secciones
)

bp = Blueprint("items", __name__)

@bp.route("/items", methods=["GET"])
def list_items():
    filters = {
        "identificador": request.args.get("identificador"),
        "control": request.args.get("control"),
        "departamento_nombre": request.args.get("departamento_nombre"),
        "epigrafe": request.args.get("epigrafe"),
        "seccion_nombre": request.args.get("seccion_nombre"),
        "fecha": request.args.get("fecha"),
    }
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 12))
    return jsonify(get_filtered_items(filters, page, limit))

@bp.route("/items/<string:item_id>", methods=["GET"])
def get_item(item_id):
    return jsonify(get_item_by_id(item_id))

@bp.route("/items/<string:item_id>/resumen", methods=["GET"])
def item_resumen(item_id):
    return jsonify(get_item_resumen(item_id))

@bp.route("/items/<string:item_id>/impacto", methods=["GET"])
def item_impacto(item_id):
    return jsonify(get_item_impacto(item_id))

@bp.route("/items/<string:item_id>/like", methods=["PUT"])
def like(item_id):
    return jsonify(like_item(item_id))

@bp.route("/items/<string:item_id>/dislike", methods=["PUT"])
def dislike(item_id):
    return jsonify(dislike_item(item_id))

@bp.route("/departamentos", methods=["GET"])
def get_departamentos():
    return jsonify(list_departamentos())

@bp.route("/epigrafes", methods=["GET"])
def get_epigrafes():
    return jsonify(list_epigrafes())

@bp.route("/secciones", methods=["GET"])
def get_secciones():
    return jsonify(list_secciones())
