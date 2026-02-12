# app/blueprints/favorites.py
from __future__ import annotations

from flask import Blueprint, jsonify, request, g, current_app

from app.auth import require_auth

# Si tu proyecto usa un helper DB, cambia estos imports a los tuyos.
# Ejemplos típicos:
# from app.db import get_conn
# from app.services.postgres import get_conn
from app.postgres import get_conn  # <-- AJUSTA si tu helper se llama distinto


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
    n = max(min_v, min(max_v, n))
    return n


@bp.before_request
def _allow_options():
    # Para CORS preflight
    if request.method == "OPTIONS":
        return ("", 204)


@bp.get("/ids")
@require_auth
def favorites_ids():
    user_id = getattr(g, "user_id", None)
    if not user_id:
        return _json_err(401, "Unauthorized")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ident
                FROM favorites
                WHERE user_id = %s
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()

    idents = [r[0] for r in rows if r and r[0]]
    return _json_ok({"idents": idents})


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

    with get_conn() as conn:
        with conn.cursor() as cur:
            # existe?
            cur.execute(
                "SELECT 1 FROM favorites WHERE user_id = %s AND ident = %s LIMIT 1",
                (user_id, ident),
            )
            exists = cur.fetchone() is not None

            if active is True:
                if not exists:
                    cur.execute(
                        """
                        INSERT INTO favorites (user_id, ident)
                        VALUES (%s, %s)
                        ON CONFLICT (user_id, ident) DO NOTHING
                        """,
                        (user_id, ident),
                    )
                conn.commit()
                return _json_ok({"ident": ident, "active": True})

            if active is False:
                if exists:
                    cur.execute(
                        "DELETE FROM favorites WHERE user_id = %s AND ident = %s",
                        (user_id, ident),
                    )
                conn.commit()
                return _json_ok({"ident": ident, "active": False})

            # toggle
            if exists:
                cur.execute(
                    "DELETE FROM favorites WHERE user_id = %s AND ident = %s",
                    (user_id, ident),
                )
                conn.commit()
                return _json_ok({"ident": ident, "active": False})
            else:
                cur.execute(
                    """
                    INSERT INTO favorites (user_id, ident)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id, ident) DO NOTHING
                    """,
                    (user_id, ident),
                )
                conn.commit()
                return _json_ok({"ident": ident, "active": True})


@bp.get("/items")
@require_auth
def favorites_items():
    """
    Query:
      page, page_size, sort=published|saved
      from=YYYY-MM-DD, to=YYYY-MM-DD  (opcional)
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

    # Orden
    order_sql = "f.created_at DESC"
    if sort == "published":
        order_sql = "i.fecha_publicacion DESC NULLS LAST, f.created_at DESC"

    where = ["f.user_id = %s"]
    params = [user_id]

    # Filtros de fecha (sobre el item publicado)
    if date_from:
        where.append("i.fecha_publicacion >= %s")
        params.append(date_from)
    if date_to:
        where.append("i.fecha_publicacion <= %s")
        params.append(date_to)

    where_sql = " AND ".join(where)
    offset = (page - 1) * page_size

    with get_conn() as conn:
        with conn.cursor() as cur:
            # total
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM favorites f
                JOIN items i ON i.ident = f.ident
                WHERE {where_sql}
                """,
                tuple(params),
            )
            total = int(cur.fetchone()[0])

            # page
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
                JOIN items i ON i.ident = f.ident
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT %s OFFSET %s
                """,
                tuple(params + [page_size, offset]),
            )
            rows = cur.fetchall()

    items = []
    for r in rows:
        items.append(
            {
                "ident": r[0],
                "titulo": r[1],
                "title_short": r[2],
                "fecha_publicacion": (r[3].isoformat() if hasattr(r[3], "isoformat") else r[3]),
                "seccion_codigo": r[4],
                "departamento_codigo": r[5],
                "favorited_at": (r[6].isoformat() if hasattr(r[6], "isoformat") else r[6]),
            }
        )

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
