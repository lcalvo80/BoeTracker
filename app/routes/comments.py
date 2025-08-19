# app/routes/comments.py
from flask import Blueprint, jsonify, request
from app.controllers.comments_controller import (
    get_comments_by_item,
    add_comment,
)

bp = Blueprint("comments", __name__)

# GET /api/comments/<item_id>
@bp.route("/<item_id>", methods=["GET"])
def api_get_comments(item_id):
    return jsonify(get_comments_by_item(item_id))

# POST /api/comments
@bp.route("/", methods=["POST"])
def api_add_comment():
    data = request.json
    return jsonify(add_comment(data)), 201
