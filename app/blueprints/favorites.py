# app/blueprints/favorites.py
from __future__ import annotations

from flask import Blueprint, jsonify, request, g, current_app
from psycopg2 import sql

from app.auth import require_auth
from app.services.postgres import get_db

bp = Blueprint("favorites", __name__, url_prefix="/api/favorites")


# -------------------------
# JSON helpers
# -------------------------
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


def _coalesce_ident(payload: dict) -> str:
    """
    Acepta varias keys para MVP:
      - ident (canónica)
      - item_ident (DB favorites)
      - identificador / boe_id / id (fallbacks típicos de FE)
    """
    cand = (
        payload.get("ident")
        or payload.get("item_ident")
        or payload.get("identificador")
        or payload.get("boe_id")
        or payload.get("id")
    )
    return (str(cand).strip() if cand is not None else "")


@bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)


# -------------------------
# Items ident column detection (cache por proceso)
# -------------------------
_ITEMS_IDENT_COL_CACHE: str | None = None


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema='public'
          AND table_name=%s
          AND column_name=%s
        LIMIT 1
        """,
        (table, column),
    )
    return cur.fetchone() is not None


def _get_items_ident_col(conn) -> str:
    """
    Detecta columna ident en items.
    Preferencia:
      1) identificador  (en tu proyecto es lo habitual)
      2) ident
      3) boe_id
      4) id
    """
    global _ITEMS_IDENT_COL_CACHE
    if _ITEMS_IDENT_COL_CACHE:
        return _ITEMS_IDENT_COL_CACHE

    candidates = ["identificador", "ident", "boe_id", "id"]
    with conn.cursor() as cur:
        for c in candidates:
            if _column_exists(cur, "items", c):
                _ITEMS_IDENT_COL_CACHE = c
                current_app.logger.info("[favorites] items ident column resolved: %s", c)
                return c

    _ITEMS_IDENT_COL_CACHE = "identificador"
    current_app.logger.warning("[favorites] items ident column not found; defaulting to 'identificador'")
    return _ITEMS_IDENT_COL_CACHE


# -------------------------
# GET /api/favorites/ids
# -------------------------
@bp.get("/ids")
@require_auth
def favorites_ids():
    """
    Este endpoint NO debe tumbar el listado.
    - Si hay excepción: devolvemos ok:true con ids:[] (best-effort)
    """
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

        ids = [r[0] for r in rows if r and r[0]]
        return _json_ok({"ids": ids})

    except Exception:
        # Best-effort para NO romper UX
        current_app.logger.exception("[favorites] ids failed (best-effort returning empty)")
        return _json_ok({"ids": []})


# -------------------------
# POST /api/favorites
# -------------------------
@bp.post("")
@require_auth
def favorites_toggle():
    """
    Body:
      { "ident": "...", "active": true/false }  (active opcional)
    Acepta también item_ident/id/identificador por MVP.
    """
    user_id = getattr(g, "user_id", None)
    if not user_id:
        return _json_err(401, "Unauthorized")

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = {}

    ident = _coalesce_ident(payload)
    if not ident:
        # Log útil para depurar payloads raros
        try:
            raw = request.get_data(cache=False, as_text=True) or ""
            raw_snip = raw[:400]
        except Exception:
            raw_snip = ""
        current_app.logger.warning("[favorites] Missing ident. keys=%s raw_snip=%s", sorted(payload.keys()), raw_snip)
        return _json_err(400, "Missing ident")

    active = payload.get("active", None)
    if active is not None and not isinstance(active, bool):
        return _json_err(400, "active must be boolean")

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # ¿existe?
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

                def _insert():
                    cur.execute(
                        """
                        INSERT INTO favorites (user_id, item_ident)
                        VALUES (%s, %s)
                        ON CONFLICT (user_id, item_ident) DO NOTHING
                        """,
                        (user_id, ident),
                    )

                def _delete():
                    cur.execute(
                        """
                        DELETE FROM favorites
                        WHERE user_id = %s AND item_ident = %s
                        """,
                        (user_id, ident),
                    )

                # modo explícito
                if active is True:
                    if not exists:
                        _insert()
                    return _json_ok({"ident": ident, "active": True})

                if active is False:
                    if exists:
                        _delete()
                    return _json_ok({"ident": ident, "active": False})

                # toggle
                if exists:
                    _delete()
                    return _json_ok({"ident": ident, "active": False})
                else:
                    _insert()
                    return _json_ok({"ident": ident, "active": True})

    except Exception:
        current_app.logger.exception("[favorites] toggle failed")
        return _json_err(500, "Internal server error")


# -------------------------
# GET /api/favorites/items
# -------------------------
@bp.get("/items")
@require_auth
def favorites_items():
    """
    Items favoritos paginados.
    - sort=saved|published
    - from/to (YYYY-MM-DD) sobre fecha_publicacion
    - q: titulo ILIKE
    - seccion/departamento: códigos
    """
    user_id = getattr(g, "user_id", None)
    if not user_id:
        return _json_err(401, "Unauthorized")

    page = _parse_int(request.args.get("page"), 1, 1, 10_000)
    page_size = _parse_int(request.args.get("page_size"), 12, 1, 100)
    offset = (page - 1) * page_size

    sort = (request.args.get("sort") or "saved").strip().lower()
    if sort not in {"saved", "published"}:
        sort = "saved"

    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()
    q = (request.args.get("q") or "").strip()
    seccion = (request.args.get("seccion") or "").strip()
    departamento = (request.args.get("departamento") or "").strip()

    where = ["f.user_id = %s"]
    params: list[object] = [user_id]

    if date_from:
        where.append("i.fecha_publicacion >= %s")
        params.append(date_from)
    if date_to:
        where.append("i.fecha_publicacion <= %s")
        params.append(date_to)

    if seccion:
        where.append("i.seccion_codigo = %s")
        params.append(seccion)
    if departamento:
        where.append("i.departamento_codigo = %s")
        params.append(departamento)

    if q:
        where.append("COALESCE(i.titulo,'') ILIKE %s")
        params.append(f"%{q}%")

    where_sql = " AND ".join(where)

    try:
        with get_db() as conn:
            items_ident_col = _get_items_ident_col(conn)
            items_ident_ident = sql.Identifier(items_ident_col)

            # Orden: saved usa created_at de favorites; published usa fecha_publicacion y luego created_at
            if sort == "published":
                order_sql = sql.SQL("i.fecha_publicacion DESC NULLS LAST, f.created_at DESC")
            else:
                order_sql = sql.SQL("f.created_at DESC")

            with conn.cursor() as cur:
                # COUNT
                count_q = sql.SQL(
                    """
                    SELECT COUNT(*)
                    FROM favorites f
                    JOIN items i ON i.{items_ident} = f.item_ident
                    WHERE {where_sql}
                    """
                ).format(
                    items_ident=items_ident_ident,
                    where_sql=sql.SQL(where_sql),
                )
                cur.execute(count_q, tuple(params))
                total = int(cur.fetchone()[0])

                # SELECT (campos mínimos)
                select_q = sql.SQL(
                    """
                    SELECT
                      i.{items_ident}        AS ident_value,
                      i.titulo              AS titulo,
                      i.resumen             AS resumen,
                      i.fecha_publicacion   AS fecha_publicacion,
                      i.seccion_codigo      AS seccion_codigo,
                      i.departamento_codigo AS departamento_codigo,
                      f.created_at          AS favorited_at
                    FROM favorites f
                    JOIN items i ON i.{items_ident} = f.item_ident
                    WHERE {where_sql}
                    ORDER BY {order_sql}
                    LIMIT %s OFFSET %s
                    """
                ).format(
                    items_ident=items_ident_ident,
                    where_sql=sql.SQL(where_sql),
                    order_sql=order_sql,
                )

                cur.execute(select_q, tuple(params + [page_size, offset]))
                rows = cur.fetchall() or []

        def _iso(x):
            try:
                return x.isoformat() if x else None
            except Exception:
                return str(x) if x is not None else None

        items = [
            {
                "ident": (str(r[0]).strip() if r[0] is not None else None),
                "titulo": r[1],
                "resumen": r[2],
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
