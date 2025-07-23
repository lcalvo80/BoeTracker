from flask import Blueprint, request, jsonify
from app.controllers.comments_controller import fetch_comments, create_comment

bp_comments = Blueprint("comments", __name__)

@bp_comments.route("/comments/<string:item_id>", methods=["GET"])
def get_comments(item_id):
    return jsonify(fetch_comments(item_id))

@bp_comments.route("/comments", methods=["POST"])
def post_comment():
    data = request.get_json()
    return jsonify(create_comment(data))
