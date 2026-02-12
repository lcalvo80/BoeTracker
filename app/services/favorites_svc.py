from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Ajusta este import a tu helper real de Postgres.
# En BOE Tracker normalmente tienes algo tipo app/postgres.py o app/db/postgres.py
# Debe exponer un get_conn() que devuelve una conexión psycopg2 (o compatible).
try:
    from app.postgres import get_conn  # type: ignore
except Exception:  # pragma: no cover
    get_conn = None  # type: ignore


@dataclass(frozen=True)
class Page:
    items: List[Dict[str, Any]]
    page: int
    page_size: int
    total: int


def _require_db():
    if get_conn is None:
        raise RuntimeError(
            "DB helper no encontrado. Ajusta el import en favorites_svc.py "
            "para apuntar a tu get_conn() de Postgres."
        )


def list_favorite_ids(user_id: str) -> List[str]:
    _require_db()
    sql = """
        SELECT item_ident
        FROM favorites
        WHERE user_id = %s
        ORDER BY created_at DESC
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id,))
            rows = cur.fetchall()
    return [r[0] for r in rows]


def add_favorite(user_id: str, item_ident: str) -> None:
    _require_db()
    sql = """
        INSERT INTO favorites (user_id, item_ident)
        VALUES (%s, %s)
        ON CONFLICT (user_id, item_ident) DO NOTHING
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, item_ident))
        conn.commit()


def remove_favorite(user_id: str, item_ident: str) -> int:
    _require_db()
    sql = """
        DELETE FROM favorites
        WHERE user_id = %s AND item_ident = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, item_ident))
            deleted = cur.rowcount or 0
        conn.commit()
    return deleted


def list_favorite_items_page(
    user_id: str,
    page: int = 1,
    page_size: int = 20,
) -> Page:
    """
    Devuelve los ITEMS favoritos del usuario, paginados.

    Asume tabla `items` con columna `ident` (texto).
    Si tu tabla se llama distinto o ident no es `ident`, ajusta aquí.
    """
    _require_db()

    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 20), 1), 100)
    offset = (page - 1) * page_size

    # Total
    sql_total = """
        SELECT COUNT(*)
        FROM favorites f
        JOIN items i ON i.ident = f.item_ident
        WHERE f.user_id = %s
    """

    # Página: ordena por fecha_publicacion si existe, y si no, por created_at del favorito.
    # Si tu items tiene fecha_publicacion, esto va perfecto.
    # Si no, no rompe: COALESCE cae a f.created_at.
    sql_page = """
        SELECT
          i.*,
          f.created_at AS favorited_at
        FROM favorites f
        JOIN items i ON i.ident = f.item_ident
        WHERE f.user_id = %s
        ORDER BY
          COALESCE(i.fecha_publicacion::timestamptz, f.created_at) DESC,
          f.created_at DESC
        LIMIT %s OFFSET %s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_total, (user_id,))
            total = int(cur.fetchone()[0])

            cur.execute(sql_page, (user_id, page_size, offset))

            # cursor.description para mapear columnas a dict
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()

    items = [dict(zip(cols, row)) for row in rows]
    return Page(items=items, page=page, page_size=page_size, total=total)
