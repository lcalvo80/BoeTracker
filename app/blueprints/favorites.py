from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.favorites_svc import (
    add_favorite,
    bulk_remove_favorites,
    favorites_count,
    list_favorite_ids,
    list_favorite_items_page,
    remove_favorite,
)

bp = Blueprint("favorites", __name__, url_prefix="/api/favorites")


# ========= AUTH =========
def get_current_user_id() -> str:
    """
    Usa tu helper real de Clerk. En tu proyecto suele existir require_auth().
    Debe devolver el Clerk user_id.
    """
    try:
        from app.auth import require_auth  # type: ignore
        user = require_auth()
        uid = user.get("user_id") or user.get("sub")
        if not uid:
            raise RuntimeError("Auth ok pero sin user_id/sub")
        return str(uid)
    except Exception:
        from flask import abort
        abort(401, description="Unauthorized: integra get_current_user_id() con Clerk JWT")


# ========= ENDPOINTS =========

@bp.get("/ids")
def favorite_ids():
    user_id = get_current_user_id()
    ids = list_favorite_ids(user_id)
    return jsonify({"ids": ids})


@bp.get("/stats")
def favorite_stats():
    user_id = get_current_user_id()
    return jsonify({"count": favorites_count(user_id)})


@bp.post("")
def favorite_add():
    user_id = get_current_user_id()
    data = request.get_json(silent=True) or {}
    item_ident = (data.get("item_ident") or "").strip()

    if not item_ident:
        return jsonify({"ok": False, "error": "item_ident is required"}), 400

    add_favorite(user_id, item_ident)
    return jsonify({"ok": True, "item_ident": item_ident, "favorited": True})


@bp.delete("/<path:item_ident>")
def favorite_remove(item_ident: str):
    user_id = get_current_user_id()
    item_ident = (item_ident or "").strip()

    if not item_ident:
        return jsonify({"ok": False, "error": "item_ident is required"}), 400

    remove_favorite(user_id, item_ident)
    return jsonify({"ok": True, "item_ident": item_ident, "favorited": False})


@bp.delete("")
def favorites_bulk_remove():
    """
    Bulk remove:
      DELETE /api/favorites
      body: { "item_idents": ["BOE-A-...", ...] }
    """
    user_id = get_current_user_id()
    data = request.get_json(silent=True) or {}
    item_idents = data.get("item_idents") or data.get("ids") or []

    if not isinstance(item_idents, list):
        return jsonify({"ok": False, "error": "item_idents must be an array"}), 400

    # Sanitiza
    cleaned = []
    for x in item_idents:
        if x is None:
            continue
        s = str(x).strip()
        if s:
            cleaned.append(s)

    if not cleaned:
        return jsonify({"ok": True, "deleted": 0})

    # límites defensivos
    if len(cleaned) > 200:
        return jsonify({"ok": False, "error": "too many ids (max 200)"}), 400

    deleted = bulk_remove_favorites(user_id, cleaned)
    return jsonify({"ok": True, "deleted": deleted})


@bp.get("/items")
def favorite_items():
    """
    Listado "Mi BOE" paginado y filtrable.

    Query:
      - page (default 1)
      - page_size (default 20, max 100)
      - q (search)
      - sort=published|favorited
      - from=YYYY-MM-DD
      - to=YYYY-MM-DD
      - seccion=<code> (opcional, si existe en DB)
      - departamento=<code> (opcional, si existe en DB)

    Response:
      { "items": [...], "page": 1, "page_size": 20, "total": 123 }
    """
    user_id = get_current_user_id()

    page = int(request.args.get("page", "1") or 1)
    page_size = int(request.args.get("page_size", "20") or 20)
    q = request.args.get("q", None)
    sort = request.args.get("sort", "published") or "published"
    from_date = request.args.get("from", None)
    to_date = request.args.get("to", None)
    seccion = request.args.get("seccion", None)
    departamento = request.args.get("departamento", None)

    p = list_favorite_items_page(
        user_id=user_id,
        page=page,
        page_size=page_size,
        q=q,
        sort=sort,
        from_date=from_date,
        to_date=to_date,
        seccion=seccion,
        departamento=departamento,
    )
    return jsonify({
        "items": p.items,
        "page": p.page,
        "page_size": p.page_size,
        "total": p.total,
    })
