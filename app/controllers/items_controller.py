# app/controllers/items_controller.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from psycopg2 import sql
from app.services.postgres import get_db

# catálogos
from app.services.lookup import _table_exists as _lookup_table_exists
from app.services.lookup import (
    list_departamentos_lookup,
    list_secciones_lookup,
)

# ============================ Helpers ============================

def _norm(s: Optional[str]) -> Optional[str]:
    """Normaliza strings de entrada, convirtiendo vacíos/placeholder a None."""
    if s is None:
        return None
    s = s.strip()
    if not s or s.lower() in {"todos", "all", "null", "none"}:
        return None
    return s

def _to_date(s: Optional[str]):
    """Convierte a date si cuadra con formatos soportados."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None

def _rows(cur) -> List[Dict[str, Any]]:
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

def _split_csv(s: Optional[str]) -> List[str]:
    """Divide una CSV simple en lista depurada."""
    if not s:
        return []
    parts = [p.strip() for p in s.split(",")]
    return [p for p in parts if p]

def _as_list(val: Any) -> List[str]:
    """Normaliza a lista de strings depurados (acepta CSV, lista o string)."""
    if val is None:
        return []
    if isinstance(val, str):
        return _split_csv(_norm(val))
    if isinstance(val, Sequence) and not isinstance(val, (bytes, bytearray)):
        out: List[str] = []
        seen = set()
        for v in val:
            vv = _norm(str(v)) if v is not None else None
            if vv and vv not in seen:
                seen.add(vv)
                out.append(vv)
        return out
    return []

def _list_param(params: Dict[str, Any], *names: str) -> List[str]:
    """
    Obtiene un parámetro que puede venir como CSV o lista, probando varios alias.
    Devuelve lista deduplicada preservando el orden de llegada.
    """
    for n in names:
        if n in params and params[n] is not None:
            return _as_list(params[n])
    return []

def _col_exists(conn, table: str, col: str) -> bool:
    """Comprueba si existe una columna en una tabla del esquema public."""
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

# ====================== Listado con filtros ======================

def get_filtered_items(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Filtros soportados (con alias):
      - page, limit
      - sort_by in {created_at, titulo, id, likes, dislikes}, sort_dir in {asc, desc}
      - fecha, fecha_desde, fecha_hasta (aplican sobre created_at o created_at_date si existe)
      - departamento | departamentos | departamento_codigo (CSV o array)
      - seccion      | secciones     | seccion_codigo      (CSV o array)
      - epigrafe     | epigrafes                            (CSV o array)
      - q | q_adv (ILIKE sobre titulo/resumen/contenido/CAST(informe_impacto AS TEXT))
      - identificador (ILIKE)
      - control (ILIKE si existe columna)
    """

    # paginación segura
    try:
        page = max(int(params.get("page", 1)), 1)
    except Exception:
        page = 1

    try:
        limit = int(params.get("limit", 12))
        if limit < 1:
            limit = 1
        if limit > 100:
            limit = 100
    except Exception:
        limit = 12

    # ordenación segura
    sort_by = (params.get("sort_by", "created_at") or "").lower()
    sort_dir = (params.get("sort_dir", "desc") or "").lower()
    if sort_by not in {"created_at", "titulo", "id", "likes", "dislikes"}:
        sort_by = "created_at"
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "desc"

    # fechas
    fecha_eq = _to_date(_norm(params.get("fecha")))
    fecha_desde = _to_date(_norm(params.get("fecha_desde")))
    fecha_hasta = _to_date(_norm(params.get("fecha_hasta")))

    # listas (acepta CSV o array y varios nombres)
    dep_list = _list_param(params, "departamento_codigo", "departamento", "departamentos")
    sec_list = _list_param(params, "seccion_codigo", "seccion", "secciones")
    epi_list = _list_param(params, "epigrafe", "epigrafes")

    # texto
    q_adv = _norm(params.get("q")) or _norm(params.get("q_adv"))
    identificador = _norm(params.get("identificador"))
    control_val = _norm(params.get("control"))

    with get_db() as conn:
        # columnas disponibles
        has_created_at_date = _col_exists(conn, "items", "created_at_date")
        has_created_at = _col_exists(conn, "items", "created_at")
        has_control = _col_exists(conn, "items", "control")
        has_contenido = _col_exists(conn, "items", "contenido")
        has_resumen = _col_exists(conn, "items", "resumen")
        has_titulo = _col_exists(conn, "items", "titulo")
        has_likes = _col_exists(conn, "items", "likes")
        has_dislikes = _col_exists(conn, "items", "dislikes")
        has_informe_imp = _col_exists(conn, "items", "informe_impacto")
        has_impacto = _col_exists(conn, "items", "impacto")

        # catálogos disponibles
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

        # WHERE dinámico
        where_sql_parts: List[sql.SQL] = []
        args: List[Any] = []

        # fechas (usa created_at si existe; si no, created_at_date)
        date_expr = sql.SQL("DATE(i.created_at)") if has_created_at else sql.SQL("DATE(i.created_at_date)")

        if fecha_eq:
            where_sql_parts.append(sql.SQL("{} = %s").format(date_expr))
            args.append(fecha_eq)
        else:
            if fecha_desde:
                where_sql_parts.append(sql.SQL("{} >= %s").format(date_expr))
                args.append(fecha_desde)
            if fecha_hasta:
                where_sql_parts.append(sql.SQL("{} <= %s").format(date_expr))
                args.append(fecha_hasta)

        # IN filters
        def _in_clause(col_name: str, values: Sequence[str]) -> Optional[Tuple[sql.SQL, List[str]]]:
            if not values:
                return None
            placeholders = sql.SQL(", ").join(sql.Placeholder() * len(values))
            return sql.SQL("{} IN ({})").format(sql.SQL(col_name), placeholders), list(values)

        in_dep = _in_clause("i.departamento_codigo", dep_list)
        if in_dep:
            where_sql_parts.append(in_dep[0]); args.extend(in_dep[1])

        in_sec = _in_clause("i.seccion_codigo", sec_list)
        if in_sec:
            where_sql_parts.append(in_sec[0]); args.extend(in_sec[1])

        in_epi = _in_clause("i.epigrafe", epi_list)
        if in_epi:
            where_sql_parts.append(in_epi[0]); args.extend(in_epi[1])

        # texto: q_adv → ILIKE en columnas disponibles
        if q_adv:
            like_val = f"%{q_adv}%"
            text_clauses = []
            if has_titulo:
                text_clauses.append(sql.SQL("i.titulo ILIKE %s")); args.append(like_val)
            if has_resumen:
                text_clauses.append(sql.SQL("i.resumen ILIKE %s")); args.append(like_val)
            if has_contenido:
                text_clauses.append(sql.SQL("i.contenido ILIKE %s")); args.append(like_val)
            # incluir informe_impacto::text sólo si existe y la query tiene al menos 3 chars
            if has_informe_imp and len(q_adv) >= 3:
                text_clauses.append(sql.SQL("CAST(i.informe_impacto AS TEXT) ILIKE %s")); args.append(like_val)
            if text_clauses:
                where_sql_parts.append(sql.SQL("(") + sql.SQL(" OR ").join(text_clauses) + sql.SQL(")"))

        if identificador:
            where_sql_parts.append(sql.SQL("i.identificador ILIKE %s"))
            args.append(f"%{identificador}%")

        if control_val and has_control:
            where_sql_parts.append(sql.SQL("i.control ILIKE %s"))
            args.append(f"%{control_val}%")

        where_sql = sql.SQL("WHERE ") + sql.SQL(" AND ").join(where_sql_parts) if where_sql_parts else sql.SQL("")

        # SELECT + JOINs
        select_cols = [
            sql.SQL("i.id"),
            sql.SQL("i.identificador"),
            sql.SQL("i.titulo") if has_titulo else sql.SQL("NULL AS titulo"),
            sql.SQL("i.resumen") if has_resumen else sql.SQL("NULL AS resumen"),
            # impacto normalizado: prioriza informe_impacto
            sql.SQL("i.informe_impacto AS impacto") if has_informe_imp
                else (sql.SQL("i.impacto") if has_impacto else sql.SQL("NULL AS impacto")),
            sql.SQL("i.departamento_codigo"),
            sql.SQL("i.seccion_codigo"),
            sql.SQL("i.epigrafe"),
            sql.SQL("i.created_at") if has_created_at else (
                sql.SQL("i.created_at_date AS created_at") if has_created_at_date else sql.SQL("NULL AS created_at")
            ),
            sql.SQL("i.likes") if has_likes else sql.SQL("NULL AS likes"),
            sql.SQL("i.dislikes") if has_dislikes else sql.SQL("NULL AS dislikes"),
            sql.SQL("i.control") if has_control else sql.SQL("NULL AS control"),
        ]
        joins: List[sql.SQL] = []

        if dep_table:
            select_cols.append(sql.SQL("d.nombre AS departamento_nombre"))
            joins.append(sql.SQL('LEFT JOIN {} d ON d.codigo = i.departamento_codigo').format(sql.Identifier(dep_table)))
        else:
            select_cols.append(sql.SQL("NULL AS departamento_nombre"))

        if sec_table:
            select_cols.append(sql.SQL("s.nombre AS seccion_nombre"))
            joins.append(sql.SQL('LEFT JOIN {} s ON s.codigo = i.seccion_codigo').format(sql.Identifier(sec_table)))
        else:
            select_cols.append(sql.SQL("NULL AS seccion_nombre"))

        # ORDER BY whitelisted
        sort_by_sql = sql.SQL("i.created_at") if sort_by == "created_at" else sql.SQL("i." + sort_by)
        sort_dir_sql = sql.SQL("ASC") if sort_dir == "asc" else sql.SQL("DESC")

        base_select_sql = sql.SQL("""
            SELECT {cols}
            FROM items i
            {joins}
            {where}
            ORDER BY {sort_by} {sort_dir}
            LIMIT %s OFFSET %s
        """).format(
            cols=sql.SQL(", ").join(select_cols),
            joins=sql.SQL(" ").join(joins),
            where=where_sql,
            sort_by=sort_by_sql,
            sort_dir=sort_dir_sql
        )

        count_sql = sql.SQL("SELECT COUNT(*) FROM items i ") + where_sql

        offset = (page - 1) * limit

        with conn.cursor() as cur:
            cur.execute(count_sql, args)
            total = cur.fetchone()[0]
            cur.execute(base_select_sql, args + [limit, offset])
            data = _rows(cur)

    return {
        "items": data,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
    }

# ====================== Detalle & derivados ======================

def get_item_by_id(identificador: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn, conn.cursor() as cur:
        base_cols = [
            "id", "identificador", "titulo", "contenido", "resumen",
            "departamento_codigo", "seccion_codigo", "epigrafe",
            "created_at", "likes", "dislikes", "control"
        ]
        existing_cols = [c for c in base_cols if _col_exists(conn, "items", c)]

        sel_parts: List[str] = [f"i.{c}" for c in existing_cols]
        # impacto normalizado
        if _col_exists(conn, "items", "informe_impacto"):
            sel_parts.append("i.informe_impacto AS impacto")
        elif _col_exists(conn, "items", "impacto"):
            sel_parts.append("i.impacto")

        sel = ", ".join(sel_parts) or "i.identificador"
        cur.execute(f"SELECT {sel} FROM items i WHERE identificador = %s LIMIT 1", (identificador,))
        row = cur.fetchone()
        if not row:
            return None
        names = [desc.name for desc in cur.description]
        data = dict(zip(names, row))

    if "impacto" not in data and "informe_impacto" in data:
        data["impacto"] = data.get("informe_impacto")

    for k in ("titulo", "resumen", "impacto", "likes", "dislikes", "control",
              "departamento_codigo", "seccion_codigo", "epigrafe", "created_at"):
        data.setdefault(k, None)

    return data

def get_item_resumen(identificador: str) -> Dict[str, Any]:
    with get_db() as conn, conn.cursor() as cur:
        if not _col_exists(conn, "items", "resumen"):
            return {"identificador": identificador, "resumen": None}
        cur.execute("SELECT resumen FROM items WHERE identificador = %s LIMIT 1", (identificador,))
        row = cur.fetchone()
    return {"identificador": identificador, "resumen": row[0] if row else None}

def get_item_impacto(identificador: str) -> Dict[str, Any]:
    with get_db() as conn, conn.cursor() as cur:
        if not _col_exists(conn, "items", "informe_impacto"):
            if _col_exists(conn, "items", "impacto"):
                cur.execute("SELECT impacto FROM items WHERE identificador = %s LIMIT 1", (identificador,))
                row = cur.fetchone()
                return {"identificador": identificador, "impacto": row[0] if row else None}
            return {"identificador": identificador, "impacto": None}
        cur.execute("SELECT informe_impacto FROM items WHERE identificador = %s LIMIT 1", (identificador,))
        row = cur.fetchone()
    return {"identificador": identificador, "impacto": row[0] if row else None}

def like_item(identificador: str) -> Dict[str, Any]:
    with get_db() as conn, conn.cursor() as cur:
        if not _col_exists(conn, "items", "likes"):
            return {"identificador": identificador, "likes": None}
        cur.execute(
            "UPDATE items SET likes = COALESCE(likes,0) + 1 WHERE identificador = %s RETURNING likes",
            (identificador,))
        row = cur.fetchone()
        conn.commit()
    return {"identificador": identificador, "likes": row[0] if row else 0}

def dislike_item(identificador: str) -> Dict[str, Any]:
    with get_db() as conn, conn.cursor() as cur:
        if not _col_exists(conn, "items", "dislikes"):
            return {"identificador": identificador, "dislikes": None}
        cur.execute(
            "UPDATE items SET dislikes = COALESCE(dislikes,0) + 1 WHERE identificador = %s RETURNING dislikes",
            (identificador,))
        row = cur.fetchone()
        conn.commit()
    return {"identificador": identificador, "dislikes": row[0] if row else 0}

# ====================== Catálogos ======================

def list_departamentos() -> List[Dict[str, Any]]:
    return list_departamentos_lookup()

def list_secciones() -> List[Dict[str, Any]]:
    return list_secciones_lookup()

def list_epigrafes() -> List[str]:
    sql_text = """
        SELECT DISTINCT TRIM(epigrafe) AS epigrafe
        FROM items
        WHERE TRIM(COALESCE(epigrafe,'')) <> ''
        ORDER BY epigrafe
    """
    with get_db() as conn, conn.cursor() as cur:
        if not _col_exists(conn, "items", "epigrafe"):
            return []
        cur.execute(sql_text)
        return [r[0] for r in cur.fetchall()]
