from flask import abort
from app.models.comments import get_comments_by_item, add_comment

def fetch_comments(item_id):
    return get_comments_by_item(item_id)

def create_comment(data):
    item_id = data.get("item_identificador")
    user_name = data.get("user_name")
    comment_text = data.get("comment")

    if not item_id or not user_name or not comment_text:
        abort(400, "Todos los campos son obligatorios")

    add_comment(item_id, user_name, comment_text)
    return {"message": "Comentario añadido correctamente"}
