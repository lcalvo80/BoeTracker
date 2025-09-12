from flask import Blueprint, jsonify, current_app
from app.controllers.items_controller import (
    get_item_by_id, list_departamentos, list_secciones, list_epigrafes
)

bp = Blueprint("compat", __name__)

@bp.route("/filters", methods=["GET"])
@bp.route("/filtros", methods=["GET"])
def filters_options():
    try:
        return jsonify({
            "departamentos": list_departamentos() or [],
            "secciones":     list_secciones()     or [],
            "epigrafes":     list_epigrafes()     or [],
        }), 200
    except Exception:
        current_app.logger.exception("filters options failed")
        return jsonify({"departamentos": [], "secciones": [], "epigrafes": []}), 200

@bp.route("/meta/filters", methods=["GET"])
def meta_filters():
    return jsonify({
        "version": "1.0.0",
        "last_updated": "2025-09-12"
    }), 200

@bp.route("/boe/<identificador>", methods=["GET"])
def boe_by_id(identificador):
    data = get_item_by_id(identificador)
    if not data:
        return jsonify({"detail":"Not found"}), 404
    return jsonify(data), 200
