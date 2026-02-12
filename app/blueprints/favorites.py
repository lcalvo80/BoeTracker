# app/blueprints/favorites.py
from __future__ import annotations

from flask import Blueprint, jsonify, request, g, current_app

from app.auth import require_auth
from app.services.postgres import get_db

bp = Blueprint("favorites", __name__, url_prefix="/api/favorites")


def _json_ok(data=None, **extra):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return jsonify(payload)


def _json_err(status: int, error: str, **extra):
    payload = {"ok": False, "error": error}
    payload.update(extra)
    return jsonify(payload), status


def _parse_int(v, default: int, min_v: int, max_v: int) -> int:
    try:
        n = int(v)
    except Exception:
        return default
    return max(min_v, min(max_v, n))


@bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)


@bp.get("/ids")
@require_auth
def favorites_ids():
    user_id = getattr(g, "user_id", None)
    if not user_id:
        return _json_err(401, "Unauthorized")

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT item_ident
                    FROM favorites
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    """,
                    (user_id,),
                )
                rows = cur.fetchall() or []

        idents = [r[0] for r in rows if r and r[0]]
        return _json_ok({"idents": idents})
    except Exception:
        current_app.logger.exception("[favorites] ids failed")
        return _json_err(500, "Internal server error")


@bp.post("")
@require_auth
def favorites_toggle():
    """
    Body:
      { "ident": "BOE-A-2026-xxxx", "active": true/false }   (active opcional)
    - Si active viene:
        - true => asegura favorito
        - false => elimina favorito
    - Si no viene => toggle
    """
    user_id = getattr(g, "user_id", None)
    if not user_id:
        return _json_err(401, "Unauthorized")

    payload = request.get_json(silent=True) or {}
    ident = str(payload.get("ident") or "").strip()
    if not ident:
        return _json_err(400, "Missing ident")

    active = payload.get("active", None)
    if active is not None and not isinstance(active, bool):
        return _json_err(400, "active must be boolean")

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                    FROM favorites
                    WHERE user_id = %s AND item_ident = %s
                    LIMIT 1
                    """,
                    (user_id, ident),
                )
                exists = cur.fetchone() is not None

                # ensure
                if active is True:
                    if not exists:
                        # Si NO tienes UNIQUE(user_id, item_ident), esto podría duplicar.
                        # Te dejo abajo el SQL recomendado para añadirlo.
                        cur.execute(
                            """
                            INSERT INTO favorites (user_id, item_ident)
                            VALUES (%s, %s)
                            """,
                            (user_id, ident),
                        )
                    return _json_ok({"ident": ident, "active": True})

                # remove
                if active is False:
                    if exists:
                        cur.execute(
                            """
                            DELETE FROM favorites
                            WHERE user_id = %s AND item_ident = %s
                            """,
                            (user_id, ident),
                        )
                    return _json_ok({"ident": ident, "active": False})

                # toggle
                if exists:
                    cur.execute(
                        """
                        DELETE FROM favorites
                        WHERE user_id = %s AND item_ident = %s
                        """,
                        (user_id, ident),
                    )
                    return _json_ok({"ident": ident, "active": False})
                else:
                    cur.execute(
                        """
                        INSERT INTO favorites (user_id, item_ident)
                        VALUES (%s, %s)
                        """,
                        (user_id, ident),
                    )
                    return _json_ok({"ident": ident, "active": True})

    except Exception:
        current_app.logger.exception("[favorites] toggle failed")
        return _json_err(500, "Internal server error")


@bp.get("/items")
@require_auth
def favorites_items():
    """
    Query:
      page, page_size, sort=saved|published
      from=YYYY-MM-DD, to=YYYY-MM-DD (opcional)
    """
    user_id = getattr(g, "user_id", None)
    if not user_id:
        return _json_err(401, "Unauthorized")

    page = _parse_int(request.args.get("page"), 1, 1, 10_000)
    page_size = _parse_int(request.args.get("page_size"), 12, 1, 100)

    sort = (request.args.get("sort") or "saved").strip().lower()
    if sort not in {"saved", "published"}:
        sort = "saved"

    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    order_sql = "f.created_at DESC"
    if sort == "published":
        order_sql = "i.fecha_publicacion DESC NULLS LAST, f.created_at DESC"

    where = ["f.user_id = %s"]
    params = [user_id]

    if date_from:
        where.append("i.fecha_publicacion >= %s")
        params.append(date_from)
    if date_to:
        where.append("i.fecha_publicacion <= %s")
        params.append(date_to)

    where_sql = " AND ".join(where)
    offset = (page - 1) * page_size

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM favorites f
                    JOIN items i ON i.ident = f.item_ident
                    WHERE {where_sql}
                    """,
                    tuple(params),
                )
                total = int(cur.fetchone()[0])

                cur.execute(
                    f"""
                    SELECT
                      i.ident,
                      i.titulo,
                      i.title_short,
                      i.fecha_publicacion,
                      i.seccion_codigo,
                      i.departamento_codigo,
                      f.created_at AS favorited_at
                    FROM favorites f
                    JOIN items i ON i.ident = f.item_ident
                    WHERE {where_sql}
                    ORDER BY {order_sql}
                    LIMIT %s OFFSET %s
                    """,
                    tuple(params + [page_size, offset]),
                )
                rows = cur.fetchall() or []

        def _iso(x):
            try:
                return x.isoformat() if x else None
            except Exception:
                return str(x) if x is not None else None

        items = [
            {
                "ident": r[0],
                "titulo": r[1],
                "title_short": r[2],
                "fecha_publicacion": _iso(r[3]),
                "seccion_codigo": r[4],
                "departamento_codigo": r[5],
                "favorited_at": _iso(r[6]),
            }
            for r in rows
        ]

        return _json_ok(
            {
                "items": items,
                "page": page,
                "page_size": page_size,
                "total": total,
                "sort": sort,
                "from": date_from or None,
                "to": date_to or None,
            }
        )

    except Exception:
        current_app.logger.exception("[favorites] items failed")
        return _json_err(500, "Internal server error")
