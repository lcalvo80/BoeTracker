# app/routes/items.py
from flask import Blueprint, jsonify, request, current_app

from app.controllers.items_controller import (
    get_filtered_items,
    get_item_by_id,
    get_item_resumen,
    get_item_impacto,
    like_item,
    dislike_item,
    list_departamentos,
    list_secciones,
    list_epigrafes,
)

bp = Blueprint("items", __name__)

@bp.before_request
def handle_options():
    if request.method == "OPTIONS":
        return ("", 204)

# Listado
@bp.route("", methods=["GET"])
def api_items():
    try:
        data = get_filtered_items(request.args.to_dict())
        return jsonify(data), 200
    except Exception as e:
        current_app.logger.exception("items list failed")
        # Respuesta consistente para no romper la UI
        page  = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 12))
        return jsonify({
            "items": [],
            "page": page,
            "limit": limit,
            "total": 0,
            "pages": 0,
            "sort_by": request.args.get("sort_by", "created_at"),
            "sort_dir": request.args.get("sort_dir", "desc"),
        }), 200

# Detalle
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

# Reacciones
@bp.route("/<identificador>/like", methods=["POST"])
def api_like(identificador):
    return jsonify(like_item(identificador)), 200

@bp.route("/<identificador>/dislike", methods=["POST"])
def api_dislike(identificador):
    return jsonify(dislike_item(identificador)), 200

# Catálogos
@bp.route("/departamentos", methods=["GET"])
def api_departamentos():
    try:
        return jsonify(list_departamentos()), 200
    except Exception:
        current_app.logger.exception("departamentos failed")
        return jsonify([]), 200

@bp.route("/secciones", methods=["GET"])
def api_secciones():
    try:
        return jsonify(list_secciones()), 200
    except Exception:
        current_app.logger.exception("secciones failed")
        return jsonify([]), 200

@bp.route("/epigrafes", methods=["GET"])
def api_epigrafes():
    try:
        return jsonify(list_epigrafes()), 200
    except Exception:
        current_app.logger.exception("epigrafes failed")
        return jsonify([]), 200
