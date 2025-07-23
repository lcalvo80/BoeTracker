from flask import Blueprint, request, jsonify
from app.controllers.comments_controller import get_comments_by_item, add_comment

bp_comments = Blueprint("comments", __name__)

@bp_comments.route("/comments/<string:item_identificador>", methods=["GET"])
def list_comments(item_identificador):
    return jsonify(get_comments_by_item(item_identificador))

@bp_comments.route("/comments", methods=["POST"])
def create_comment():
    data = request.get_json()
    if not data or not all(k in data for k in ("item_identificador", "user_name", "comment")):
        return jsonify({"error": "Campos incompletos"}), 400
    return jsonify(add_comment(data["item_identificador"], data["user_name"], data["comment"])), 201
