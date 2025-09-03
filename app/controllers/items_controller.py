# app/controllers/items_controller.py
from __future__ import annotations

from datetime import datetime
from typing import Dict, Any, List, Optional, Sequence, Tuple

from psycopg2 import sql
from app.services.postgres import get_db

# catálogos
from app.services.lookup import _table_exists as _lookup_table_exists
from app.services.lookup import (
    list_departamentos_lookup,
    list_secciones_lookup,
)

# ======================== helpers generales ========================

def _col_exists(conn, table: str, col: str) -> bool:
    """Comprueba si existe la columna `col` en `table` (esquema public)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name=%s
              AND column_name=%s
            LIMIT 1
            """,
            (table, col),
        )
        return cur.fetchone() is not None

def _rows(cur) -> List[Dict[str, Any]]:
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

def _as_list(v) -> List[str]:
    """Normaliza una entrada a lista de strings (acepta list o str coma-separado)."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip() != ""]
    s = str(v).strip()
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip() != ""]

def _to_date(s: Optional[str]):
    """Parsea fecha en formatos comunes → date() o None."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None

def _pick_first_existing_col(conn, table: str, candidates: Sequence[str]) -> Optional[str]:
    """Devuelve la primera columna existente de `candidates` en `table`, o None."""
    for c in candidates:
        if _col_exists(conn, table, c):
            return c
    return None

# ======================== listado con filtros ========================

def get_filtered_items(p: Dict[str, Any]) -> Dict[str, Any]:
    """
    Filtros esperados (tras saneo en la ruta):
      - p["departamentos"] : list[str]
      - p["secciones"]     : list[str]
      - p["epigrafes"]     : list[str]
      - p["tags"]          : list[str]   (opcional, si existe columna/relación)
      - p["ids"]           : list[str]
      - p["q"]             : str|None
      - p["fecha_desde"], p["fecha_hasta"] : 'YYYY-MM-DD'|None
      - p["page"], p["limit"] : int
      - p["sort_by"] in {'created_at','fecha','updated_at','relevancia','titulo'}
      - p["sort_dir"] in {'asc','desc'}
    """
    page: int = int(p.get("page", 1))
    limit: int = int(p.get("limit", 12))
    sort_by: str = str(p.get("sort_by", "created_at"))
    sort_dir: str = "ASC" if str(p.get("sort_dir", "desc")).lower() == "asc" else "DESC"

    q: Optional[str] = p.get("q") or None
    fecha_desde = _to_date(p.get("fecha_desde"))
    fecha_hasta = _to_date(p.get("fecha_hasta"))

    departamentos = _as_list(p.get("departamentos"))
    secciones     = _as_list(p.get("secciones"))
    epigrafes     = _as_list(p.get("epigrafes"))
    tags          = _as_list(p.get("tags"))
    ids           = _as_list(p.get("ids"))

    with get_db() as conn:
        # -------- columnas disponibles / mapping flexible --------
        # fecha de negocio: si existe 'fecha' úsala; si no cae a DATE(created_at / created_at_date)
        has_fecha = _col_exists(conn, "items", "fecha")
        has_created_at      = _col_exists(conn, "items", "created_at")
        has_created_at_date = _col_exists(conn, "items", "created_at_date")

        date_expr = (
            sql.SQL("i.fecha")
            if has_fecha
            else (sql.SQL("DATE(i.created_at)") if has_created_at else sql.SQL("DATE(i.created_at_date)"))
        )

        # nombres de columnas de catálogo (acepta *_codigo o sin sufijo)
        dep_col = _pick_first_existing_col(conn, "items", ("departamento_codigo", "departamento"))
        sec_col = _pick_first_existing_col(conn, "items", ("seccion_codigo", "seccion"))
        epi_col = _pick_first_existing_col(conn, "items", ("epigrafe",))

        # otras columnas opcionales
        has_titulo    = _col_exists(conn, "items", "titulo")
        has_resumen   = _col_exists(conn, "items", "resumen")
        has_contenido = _col_exists(conn, "items", "contenido")
        has_informe   = _col_exists(conn, "items", "informe_impacto")
        has_impacto   = _col_exists(conn, "items", "impacto")
        has_relev     = _col_exists(conn, "items", "relevancia")
        has_upd       = _col_exists(conn, "items", "updated_at")
        has_likes     = _col_exists(conn, "items", "likes")
        has_dislikes  = _col_exists(conn, "items", "dislikes")
        has_ident     = _col_exists(conn, "items", "identificador")
        has_tags      = _col_exists(conn, "items", "tags")  # por si es text[] o jsonb

        # tablas de lookup (nombres flexibles)
        dep_table = None
        for t in ("departamentos", "lookup_departamentos", "cat_departamentos", "dim_departamentos"):
            if _lookup_table_exists(conn, t):
                dep_table = t
                break

        sec_table = None
        for t in ("secciones", "lookup_secciones", "cat_secciones", "dim_secciones"):
            if _lookup_table_exists(conn, t):
                sec_table = t
                break

        # -------- WHERE dinámico --------
        where_sql_parts: List[sql.SQL] = []
        args: List[Any] = []

        # fecha_desde / fecha_hasta
        if fecha_desde:
            where_sql_parts.append(sql.SQL("{} >= %s").format(date_expr))
            args.append(fecha_desde)
        if fecha_hasta:
            where_sql_parts.append(sql.SQL("{} <= %s").format(date_expr))
            args.append(fecha_hasta)

        # ids
        if ids and has_ident:
            where_sql_parts.append(sql.SQL("i.identificador = ANY(%s)"))
            args.append(ids)

        # departamentos
        if departamentos and dep_col:
            where_sql_parts.append(sql.SQL(f"i.{dep_col} = ANY(%s)"))
            args.append(departamentos)

        # secciones
        if secciones and sec_col:
            where_sql_parts.append(sql.SQL(f"i.{sec_col} = ANY(%s)"))
            args.append(secciones)

        # epigrafes
        if epigrafes and epi_col:
            where_sql_parts.append(sql.SQL(f"i.{epi_col} = ANY(%s)"))
            args.append(epigrafes)

        # tags (si existe columna 'tags'). Soporta text[] o jsonb/array de texto
        if tags and has_tags:
            # Caso 1: text[] → tags && ARRAY[...]
            # Caso 2: jsonb → tags ?| ARRAY[...]
            # Para simplicidad: usar ANY por cada tag con OR (más compatible).
            tag_clauses = [sql.SQL("EXISTS (SELECT 1 WHERE %s = ANY(i.tags))")]  # text[]
            args.extend(tags)
            # Si fuese jsonb y quieres compatibilidad adicional, podrías OR con: (i.tags ? %s)
            where_sql_parts.append(
                sql.SQL("(") +
                sql.SQL(" OR ").join(tag_clauses * len(tags)) +
                sql.SQL(")")
            )

        # q: ILIKE sobre columnas disponibles
        if q:
            like_val = f"%{q}%"
            text_clauses = []
            if has_titulo:
                text_clauses.append(sql.SQL("i.titulo ILIKE %s")); args.append(like_val)
            if has_resumen:
                text_clauses.append(sql.SQL("i.resumen ILIKE %s")); args.append(like_val)
            if has_contenido:
                text_clauses.append(sql.SQL("i.contenido ILIKE %s")); args.append(like_val)
            # informe_impacto como texto si cadena es ≥3 chars (para no matar índices en consultas muy cortas)
            if has_informe and len(q) >= 3:
                text_clauses.append(sql.SQL("CAST(i.informe_impacto AS TEXT) ILIKE %s")); args.append(like_val)

            if text_clauses:
                where_sql_parts.append(
                    sql.SQL("(") + sql.SQL(" OR ").join(text_clauses) + sql.SQL(")")
                )

        where_sql = sql.SQL("WHERE ") + sql.SQL(" AND ").join(where_sql_parts) if where_sql_parts else sql.SQL("")

        # -------- SELECT + LEFT JOINs a lookups --------
        select_cols = [
            sql.SQL("i.id") if _col_exists(conn, "items", "id") else sql.SQL("NULL AS id"),
            sql.SQL("i.identificador") if has_ident else sql.SQL("NULL AS identificador"),
            sql.SQL("i.titulo") if has_titulo else sql.SQL("NULL AS titulo"),
            sql.SQL("i.resumen") if has_resumen else sql.SQL("NULL AS resumen"),
            # impacto normalizado
            sql.SQL("i.informe_impacto AS impacto") if has_informe
                else (sql.SQL("i.impacto") if has_impacto else sql.SQL("NULL AS impacto")),
            # catálogos base
            sql.SQL(f"i.{dep_col}") if dep_col else sql.SQL("NULL AS departamento_codigo"),
            sql.SQL(f"i.{sec_col}") if sec_col else sql.SQL("NULL AS seccion_codigo"),
            sql.SQL(f"i.{epi_col}") if epi_col else sql.SQL("NULL AS epigrafe"),
            # fechas
            sql.SQL("i.fecha") if has_fecha else (
                sql.SQL("i.created_at") if has_created_at else (
                    sql.SQL("i.created_at_date AS created_at") if has_created_at_date else sql.SQL("NULL AS created_at")
                )
            ),
            sql.SQL("i.likes") if has_likes else sql.SQL("NULL AS likes"),
            sql.SQL("i.dislikes") if has_dislikes else sql.SQL("NULL AS dislikes"),
        ]

        joins: List[sql.SQL] = []
        if dep_table and dep_col:
            select_cols.append(sql.SQL("d.nombre AS departamento_nombre"))
            joins.append(
                sql.SQL("LEFT JOIN {} d ON d.codigo = i.{}").format(
                    sql.Identifier(dep_table), sql.Identifier(dep_col)
                )
            )
        else:
            select_cols.append(sql.SQL("NULL AS departamento_nombre"))

        if sec_table and sec_col:
            select_cols.append(sql.SQL("s.nombre AS seccion_nombre"))
            joins.append(
                sql.SQL("LEFT JOIN {} s ON s.codigo = i.{}").format(
                    sql.Identifier(sec_table), sql.Identifier(sec_col)
                )
            )
        else:
            select_cols.append(sql.SQL("NULL AS seccion_nombre"))

        # -------- ORDER BY (whitelist + fallback por columnas disponibles) --------
        # mapping de sort_by a expresión SQL
        if sort_by == "fecha":
            sort_expr = sql.SQL("i.fecha") if has_fecha else date_expr
        elif sort_by == "updated_at" and has_upd:
            sort_expr = sql.SQL("i.updated_at")
        elif sort_by in ("relevancia", "relevance") and has_relev:
            sort_expr = sql.SQL("i.relevancia")
        elif sort_by == "titulo" and has_titulo:
            sort_expr = sql.SQL("i.titulo")
        else:
            # created_at o fallback a created_at_date/fecha
            sort_expr = sql.SQL("i.created_at") if has_created_at else (
                sql.SQL("i.created_at_date") if has_created_at_date else date_expr
            )

        order_sql = sql.SQL("ORDER BY {} {}").format(sort_expr, sql.SQL(sort_dir))

        # -------- consulta principal y conteo --------
        offset = (page - 1) * limit
        base_select_sql = sql.SQL("""
            SELECT {cols}
            FROM items i
            {joins}
            {where}
            {order}
            LIMIT %s OFFSET %s
        """).format(
            cols=sql.SQL(", ").join(select_cols),
            joins=sql.SQL(" ").join(joins),
            where=where_sql,
            order=order_sql,
        )

        count_sql = sql.SQL("SELECT COUNT(*) FROM items i ") + where_sql

        with conn.cursor() as cur:
            cur.execute(count_sql, args)
            total = cur.fetchone()[0] or 0

            cur.execute(base_select_sql, args + [limit, offset])
            data = _rows(cur)

    pages = (total + limit - 1) // limit if limit else 0
    return {
        "items": data,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": pages,
        "sort_by": sort_by,
        "sort_dir": "asc" if sort_dir == "ASC" else "desc",
    }

# ======================== detalle & derivados ========================

def get_item_by_id(identificador: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn
