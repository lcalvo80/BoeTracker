from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    # Ajusta a tu helper real si difiere
    from app.postgres import get_conn  # type: ignore
except Exception:  # pragma: no cover
    get_conn = None  # type: ignore


@dataclass(frozen=True)
class Page:
    items: List[Dict[str, Any]]
    page: int
    page_size: int
    total: int


# Cache simple para no consultar information_schema en cada request
_COL_CACHE: dict[tuple[str, str], bool] = {}


def _require_db():
    if get_conn is None:
        raise RuntimeError(
            "DB helper no encontrado. Ajusta el import en favorites_svc.py "
            "para apuntar a tu get_conn() de Postgres."
        )


def _col_exists(table: str, column: str) -> bool:
    """
    Comprueba si existe una columna en una tabla dentro del schema 'public'.
    Cacheado en memoria del proceso.
    """
    _require_db()
    key = (table, column)
    if key in _COL_CACHE:
        return _COL_CACHE[key]

    sql = """
      SELECT 1
      FROM information_schema.columns
      WHERE table_schema='public' AND table_name=%s AND column_name=%s
      LIMIT 1
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (table, column))
            ok = cur.fetchone() is not None

    _COL_CACHE[key] = ok
    return ok


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


def bulk_remove_favorites(user_id: str, item_idents: Sequence[str]) -> int:
    _require_db()
    if not item_idents:
        return 0

    sql = """
        DELETE FROM favorites
        WHERE user_id = %s
          AND item_ident = ANY(%s)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, list(item_idents)))
            deleted = cur.rowcount or 0
        conn.commit()
    return deleted


def favorites_count(user_id: str) -> int:
    _require_db()
    sql = "SELECT COUNT(*) FROM favorites WHERE user_id=%s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id,))
            return int(cur.fetchone()[0])


def _pick_first_existing(table: str, candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if _col_exists(table, c):
            return c
    return None


def list_favorite_items_page(
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    q: Optional[str] = None,
    sort: str = "published",  # "published" | "favorited"
    from_date: Optional[str] = None,  # "YYYY-MM-DD"
    to_date: Optional[str] = None,    # "YYYY-MM-DD"
    seccion: Optional[str] = None,
    departamento: Optional[str] = None,
) -> Page:
    """
    Devuelve items favoritos paginados.

    - sort=published: ordena por fecha_publicacion si existe, si no por favorited_at
    - sort=favorited: ordena por favorited_at desc
    - q: búsqueda simple por título (usa la primera columna existente entre title_short/titulo/title)
    - from_date/to_date: filtra por fecha_publicacion si existe (si no, se ignora)
    - seccion/departamento: aplica si existen columnas equivalentes en items
    """
    _require_db()

    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 20), 1), 100)
    offset = (page - 1) * page_size

    # Detecta columnas útiles (no rompe si tu esquema difiere)
    items_table = "items"

    col_ident = _pick_first_existing(items_table, ["ident", "item_ident", "boe_ident"]) or "ident"
    col_fecha_pub = _pick_first_existing(items_table, ["fecha_publicacion", "published_at", "fecha"])
    col_title = _pick_first_existing(items_table, ["title_short", "titulo", "title"])
    # posibles columnas de códigos:
    col_seccion = _pick_first_existing(items_table, ["seccion_codigo", "seccion", "cod_seccion", "section_code"])
    col_depart = _pick_first_existing(items_table, ["departamento_codigo", "departamento", "cod_departamento", "department_code"])

    where: List[str] = ["f.user_id = %s"]
    params: List[Any] = [user_id]

    # q
    if q and col_title:
        where.append(f"COALESCE(i.{col_title}::text, '') ILIKE %s")
        params.append(f"%{q.strip()}%")

    # date range (solo si existe fecha_publicacion)
    if col_fecha_pub:
        if from_date:
            where.append(f"i.{col_fecha_pub}::date >= %s::date")
            params.append(from_date)
        if to_date:
            where.append(f"i.{col_fecha_pub}::date <= %s::date")
            params.append(to_date)

    # seccion/departamento (solo si existen columnas)
    if seccion and col_seccion:
        where.append(f"i.{col_seccion}::text = %s")
        params.append(str(seccion).strip())

    if departamento and col_depart:
        where.append(f"i.{col_depart}::text = %s")
        params.append(str(departamento).strip())

    where_sql = " AND ".join(where)

    # ORDER BY
    sort = (sort or "published").strip().lower()
    if sort not in ("published", "favorited"):
        sort = "published"

    if sort == "favorited":
        order_by = "f.created_at DESC"
    else:
        if col_fecha_pub:
            order_by = f"i.{col_fecha_pub} DESC NULLS LAST, f.created_at DESC"
        else:
            order_by = "f.created_at DESC"

    # Total
    sql_total = f"""
        SELECT COUNT(*)
        FROM favorites f
        JOIN items i ON i.{col_ident} = f.item_ident
        WHERE {where_sql}
    """

    # Page query
    sql_page = f"""
        SELECT i.*, f.created_at AS favorited_at
        FROM favorites f
        JOIN items i ON i.{col_ident} = f.item_ident
        WHERE {where_sql}
        ORDER BY {order_by}
        LIMIT %s OFFSET %s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_total, tuple(params))
            total = int(cur.fetchone()[0])

            cur.execute(sql_page, tuple(params + [page_size, offset]))
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()

    items = [dict(zip(cols, row)) for row in rows]
    return Page(items=items, page=page, page_size=page_size, total=total)
