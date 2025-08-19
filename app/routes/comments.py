# app/routes/comments.py
from flask import Blueprint, jsonify, request
from app.controllers.comments_controller import (
    get_comments_by_item as ctrl_get_comments_by_item,
    add_comment as ctrl_add_comment,
)

bp = Blueprint("comments", __name__)  # sin url_prefix aquí

# GET /api/comments/<item_id>
@bp.route("/<int:item_id>", methods=["GET"])
def api_get_comments(item_id: int):
    try:
        comments = ctrl_get_comments_by_item(item_id)
        if comments is None:
            comments = []
        return jsonify(comments), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": "Error al obtener comentarios", "detail": str(e)}), 500

# POST /api/comments/
@bp.route("/", methods=["POST"])
def api_add_comment():
    data = request.get_json(silent=True) or {}
    item_id = data.get("item_id")
    text = data.get("text")

    if item_id is None or text is None or not str(text).strip():
        return jsonify({"error": "Faltan campos requeridos: item_id (int) y text (str)"}), 400

    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return jsonify({"error": "item_id debe ser numérico"}), 400

    try:
        created = ctrl_add_comment({"item_id": item_id, "text": text})
        return jsonify(created), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "Error al crear el comentario", "detail": str(e)}), 500
