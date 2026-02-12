from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.favorites_svc import (
    add_favorite,
    list_favorite_ids,
    list_favorite_items_page,
    remove_favorite,
)

bp = Blueprint("favorites", __name__, url_prefix="/api/favorites")


# ========= AUTH =========
# Ajusta este import a TU helper real de auth.
# Debe devolver user_id (Clerk user id) o lanzar/abortar 401.
def get_current_user_id() -> str:
    """
    Implementación "puente" para integrarte rápido.
    ✅ Reemplaza este cuerpo por tu helper real (Clerk JWT template backend).

    Requisitos:
    - Leer Authorization: Bearer <jwt>
    - Verificar token
    - Extraer user_id
    """
    # Si ya tienes algo tipo: from app.auth import require_auth; user = require_auth()
    # entonces aquí lo llamas y devuelves user["user_id"].

    # --- Default: intenta un import estándar del proyecto (ajústalo si aplica) ---
    try:
        from app.auth import require_auth  # type: ignore
        user = require_auth()
        uid = user.get("user_id") or user.get("sub")
        if not uid:
            raise RuntimeError("Auth ok pero sin user_id/sub")
        return str(uid)
    except Exception:
        pass

    # --- Fallback: NO aceptamos user_id por header ni query (seguridad) ---
    # Para evitar 500, devolvemos 401 si no está integrado.
    from flask import abort
    abort(401, description="Unauthorized: integra get_current_user_id() con Clerk JWT")


# ========= ENDPOINTS =========

@bp.get("/ids")
def favorite_ids():
    user_id = get_current_user_id()
    ids = list_favorite_ids(user_id)
    return jsonify({"ids": ids})


@bp.post("")
def favorite_add():
    user_id = get_current_user_id()
    data = request.get_json(silent=True) or {}
    item_ident = (data.get("item_ident") or "").strip()

    if not item_ident:
        return jsonify({"error": "item_ident is required"}), 400

    add_favorite(user_id, item_ident)
    return jsonify({"ok": True, "item_ident": item_ident, "favorited": True})


@bp.delete("/<path:item_ident>")
def favorite_remove(item_ident: str):
    user_id = get_current_user_id()
    item_ident = (item_ident or "").strip()

    if not item_ident:
        return jsonify({"error": "item_ident is required"}), 400

    remove_favorite(user_id, item_ident)
    return jsonify({"ok": True, "item_ident": item_ident, "favorited": False})


@bp.get("/items")
def favorite_items():
    """
    Listado "Mi BOE" paginado.

    Query:
      - page (default 1)
      - page_size (default 20, max 100)

    Response:
      {
        "items": [...],
        "page": 1,
        "page_size": 20,
        "total": 123
      }
    """
    user_id = get_current_user_id()

    page = request.args.get("page", "1")
    page_size = request.args.get("page_size", "20")

    p = list_favorite_items_page(user_id=user_id, page=int(page), page_size=int(page_size))
    return jsonify({
        "items": p.items,
        "page": p.page,
        "page_size": p.page_size,
        "total": p.total,
    })
