# app/services/items_svc.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple
import base64, gzip, json, time

from psycopg2 import sql
from app.services.postgres import get_db

from app.services.lookup import (
    list_departamentos_lookup,
    list_secciones_lookup,
)

# ============================ Helpers internos ============================

def _norm(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = s.strip()
    if not s or s.lower() in {"todos", "all", "null", "none"}:
        return None
    return s

def _to_date(s: Optional[str]):
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
    if not s:
        return []
    parts = [p.strip() for p in s.split(",")]
    return [p for p in parts if p]

def _as_list(val: Any) -> List[str]:
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
    for n in names:
        if n in params and params[n] is not None:
            return _as_list(params[n])
    return []

def _inflate_b64_gzip_maybe(val: Any) -> Any:
    if not isinstance(val, str) or len(val) < 8:
        return val
    try:
        b = base64.b64decode(val, validate=True)
        try:
            out = gzip.decompress(b).decode("utf-8", errors="replace")
            return out
        except Exception:
            return val
    except Exception:
        return val

def _parse_json_maybe(text: Optional[str]) -> Any:
    if not isinstance(text, str):
        return text
    t = text.strip()
    if not t:
        return ""
    if t.startswith("{") or t.startswith("["):
        try:
            return json.loads(t)
        except Exception:
            return text
    return text

def _ts_lang() -> str:
    return "spanish"

# ============================ Schema cache (proceso) ============================

_SCHEMA_CACHE: Optional[Dict[str, Any]] = None
_SCHEMA_CACHE_TS: float = 0.0
_SCHEMA_CACHE_TTL_S: int = 300  # 5 min (ajustable)

def _load_schema_cache(conn) -> Dict[str, Any]:
    """
    Cache de tablas/columnas en memoria (por proceso) con TTL.
    Evita decenas de consultas a information_schema por request.
    """
    global _SCHEMA_CACHE, _SCHEMA_CACHE_TS

    now = time.time()
    if _SCHEMA_CACHE and (now - _SCHEMA_CACHE_TS) < _SCHEMA_CACHE_TTL_S:
        return _SCHEMA_CACHE

    tables: set[str] = set()
    columns_by_table: Dict[str, set[str]] = {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema='public'
            """
        )
        for (t,) in cur.fetchall():
            tables.add(t)

        cur.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema='public'
            """
        )
        for t, c in cur.fetchall():
            columns_by_table.setdefault(t, set()).add(c)

    _SCHEMA_CACHE = {"tables": tables, "columns_by_table": columns_by_table}
    _SCHEMA_CACHE_TS = now
    return _SCHEMA_CACHE

def _table_exists_cached(schema: Dict[str, Any], table: str) -> bool:
    return table in schema["tables"]

def _col_exists_cached(schema: Dict[str, Any], table: str, col: str) -> bool:
    return col in schema["columns_by_table"].get(table, set())

def _fts_available(schema: Dict[str, Any]) -> bool:
    return _col_exists_cached(schema, "items", "fts")

# ====================== Reactions helpers ======================

def _reactions_table_exists(schema: Dict[str, Any]) -> bool:
    return _table_exists_cached(schema, "item_reactions")

def _reactions_agg_join_sql() -> sql.SQL:
    return sql.SQL(
        """
        LEFT JOIN (
          SELECT
            item_id,
            COALESCE(SUM(CASE WHEN reaction = 1 THEN 1 ELSE 0 END), 0)::int  AS likes_calc,
            COALESCE(SUM(CASE WHEN reaction = -1 THEN 1 ELSE 0 END), 0)::int AS dislikes_calc
          FROM item_reactions
          GROUP BY item_id
        ) r ON r.item_id = i.identificador
        """
    )

# ====================== Categorías (Fase 4) ======================

def get_category_filters() -> Dict[str, Any]:
    """
    Devuelve filtros de categorías de forma defensiva.
    Keys nuevas:
      - categories_l1: distinct category_l1
      - categories_l2: distinct unnest(category_l2)
      - categories_l2_by_l1: { l1: [l2,...] }

    Incluye compat ES opcional:
      - categorias_n1, categorias_n2, categorias_n2_por_n1
    """
    with get_db() as conn:
        schema = _load_schema_cache(conn)

        has_l1 = _col_exists_cached(schema, "items", "category_l1")
        has_l2 = _col_exists_cached(schema, "items", "category_l2")

        categories_l1: List[str] = []
        categories_l2: List[str] = []
        categories_l2_by_l1: Dict[str, List[str]] = {}

        with conn.cursor() as cur:
            if has_l1:
                cur.execute(
                    """
                    SELECT DISTINCT category_l1
                    FROM items
                    WHERE category_l1 IS NOT NULL AND btrim(category_l1) <> ''
                    ORDER BY 1
                    """
                )
                categories_l1 = [r[0] for r in cur.fetchall()]

            if has_l2:
                cur.execute(
                    """
                    SELECT DISTINCT x AS category_l2
                    FROM items
                    CROSS JOIN LATERAL unnest(items.category_l2) AS x
                    WHERE items.category_l2 IS NOT NULL
                      AND x IS NOT NULL AND btrim(x) <> ''
                    ORDER BY 1
                    """
                )
                categories_l2 = [r[0] for r in cur.fetchall()]

                if has_l1:
                    cur.execute(
                        """
                        SELECT
                            items.category_l1,
                            array_agg(DISTINCT x ORDER BY x) AS l2s
                        FROM items
                        CROSS JOIN LATERAL unnest(items.category_l2) AS x
                        WHERE items.category_l1 IS NOT NULL AND btrim(items.category_l1) <> ''
                          AND items.category_l2 IS NOT NULL
                          AND x IS NOT NULL AND btrim(x) <> ''
                        GROUP BY items.category_l1
                        ORDER BY items.category_l1
                        """
                    )
                    for l1, l2s in cur.fetchall():
                        categories_l2_by_l1[str(l1)] = list(l2s or [])

    return {
        "categories_l1": categories_l1,
        "categories_l2": categories_l2,
        "categories_l2_by_l1": categories_l2_by_l1,
        # compat ES
        "categorias_n1": categories_l1,
        "categorias_n2": categories_l2,
        "categorias_n2_por_n1": categories_l2_by_l1,
    }

# ====================== Búsqueda / listado ======================

def search_items(params: Dict[str, Any], *, user_id: Optional[str] = None) -> Dict[str, Any]:
    t0 = time.time()

    # paginación
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

    # ordenación
    sort_by = (params.get("sort_by", "created_at") or "").lower()
    sort_dir = (params.get("sort_dir", "desc") or "").lower()
    if sort_by not in {"created_at", "titulo", "id", "likes", "dislikes", "relevancia"}:
        sort_by = "created_at"
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "desc"

    # fechas / filtros
    fecha_eq = _to_date(_norm(params.get("fecha")))
    fecha_desde = _to_date(_norm(params.get("fecha_desde")))
    fecha_hasta = _to_date(_norm(params.get("fecha_hasta")))

    dep_list = _list_param(params, "departamento_codigo", "departamento", "departamentos")
    sec_list = _list_param(params, "seccion_codigo", "seccion", "secciones")
    epi_list = _list_param(params, "epigrafe", "epigrafes")

    # Fase 4: categorías (multi)
    cat_l1_list = _list_param(params, "category_l1", "categories_l1", "categoria_n1", "categorias_n1")
    cat_l2_list = _list_param(params, "category_l2", "categories_l2", "categoria_n2", "categorias_n2")

    q_adv = _norm(params.get("q")) or _norm(params.get("q_adv"))
    identificador = _norm(params.get("identificador"))
    control_val = _norm(params.get("control"))

    with get_db() as conn:
        schema = _load_schema_cache(conn)

        has_titulo_resumen   = _col_exists_cached(schema, "items", "titulo_resumen")
        has_titulo_corto     = _col_exists_cached(schema, "items", "titulo_corto")
        has_titulo_completo  = _col_exists_cached(schema, "items", "titulo_completo")
        has_created_at       = _col_exists_cached(schema, "items", "created_at")
        has_created_at_date  = _col_exists_cached(schema, "items", "created_at_date")
        has_fecha_public     = _col_exists_cached(schema, "items", "fecha_publicacion")
        has_control          = _col_exists_cached(schema, "items", "control")
        has_contenido        = _col_exists_cached(schema, "items", "contenido")
        has_resumen          = _col_exists_cached(schema, "items", "resumen")
        has_titulo           = _col_exists_cached(schema, "items", "titulo")
        has_likes_legacy     = _col_exists_cached(schema, "items", "likes")
        has_dislikes_legacy  = _col_exists_cached(schema, "items", "dislikes")
        has_informe_imp      = _col_exists_cached(schema, "items", "informe_impacto")
        has_impacto          = _col_exists_cached(schema, "items", "impacto")
        has_id               = _col_exists_cached(schema, "items", "id")

        # Fase 4: columnas categorías
        has_category_l1      = _col_exists_cached(schema, "items", "category_l1")
        has_category_l2      = _col_exists_cached(schema, "items", "category_l2")

        use_reactions = _reactions_table_exists(schema)

        # WHERE
        where_sql_parts: List[sql.SQL] = []
        args: List[Any] = []

        # fecha coherente
        if has_created_at:
            date_expr = sql.SQL("DATE(i.created_at)")
            created_expr = sql.SQL("i.created_at AS created_at")
            created_order_col = sql.SQL("i.created_at")
        elif has_created_at_date:
            date_expr = sql.SQL("DATE(i.created_at_date)")
            created_expr = sql.SQL("i.created_at_date AS created_at")
            created_order_col = sql.SQL("i.created_at_date")
        elif has_fecha_public:
            date_expr = sql.SQL("DATE(i.fecha_publicacion)")
            created_expr = sql.SQL("i.fecha_publicacion AS created_at")
            created_order_col = sql.SQL("i.fecha_publicacion")
        else:
            date_expr = None
            created_expr = sql.SQL("NULL AS created_at")
            created_order_col = sql.SQL("i.id") if has_id else sql.SQL("i.identificador")

        if date_expr is not None:
            if fecha_eq:
                where_sql_parts.append(sql.SQL("{} = %s").format(date_expr)); args.append(fecha_eq)
            else:
                if fecha_desde:
                    where_sql_parts.append(sql.SQL("{} >= %s").format(date_expr)); args.append(fecha_desde)
                if fecha_hasta:
                    where_sql_parts.append(sql.SQL("{} <= %s").format(date_expr)); args.append(fecha_hasta)

        def _in_clause(col_name: str, values: Sequence[str]) -> Optional[Tuple[sql.SQL, List[str]]]:
            if not values:
                return None
            placeholders = sql.SQL(", ").join([sql.Placeholder()] * len(values))
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

        # Fase 4: filtros por categorías (defensivo)
        if cat_l1_list:
            if has_category_l1:
                where_sql_parts.append(sql.SQL("i.category_l1 = ANY(%s::text[])"))
                args.append(cat_l1_list)
            else:
                # el cliente pidió filtro pero la columna no existe -> 0 resultados
                where_sql_parts.append(sql.SQL("1=0"))

        if cat_l2_list:
            if has_category_l2:
                where_sql_parts.append(sql.SQL("i.category_l2 && %s::text[]"))
                args.append(cat_l2_list)
            else:
                where_sql_parts.append(sql.SQL("1=0"))

        # texto
        _use_fts_rank = False
        if q_adv and len(q_adv) >= 2:
            if _fts_available(schema):
                where_sql_parts.append(sql.SQL("i.fts @@ plainto_tsquery(%s, %s)"))
                args.extend([_ts_lang(), q_adv])
                _use_fts_rank = True
            else:
                like_val = f"%{q_adv}%"
                text_clauses = []
                if has_titulo:
                    text_clauses.append(sql.SQL("i.titulo ILIKE %s")); args.append(like_val)
                if has_titulo_resumen:
                    text_clauses.append(sql.SQL("i.titulo_resumen ILIKE %s")); args.append(like_val)
                if has_titulo_corto:
                    text_clauses.append(sql.SQL("i.titulo_corto ILIKE %s")); args.append(like_val)
                if has_titulo_completo:
                    text_clauses.append(sql.SQL("i.titulo_completo ILIKE %s")); args.append(like_val)
                if has_resumen:
                    text_clauses.append(sql.SQL("i.resumen ILIKE %s")); args.append(like_val)
                if has_contenido:
                    text_clauses.append(sql.SQL("i.contenido ILIKE %s")); args.append(like_val)
                if has_informe_imp or has_impacto:
                    text_clauses.append(
                        sql.SQL("CAST(i.informe_impacto AS TEXT) ILIKE %s") if has_informe_imp
                        else sql.SQL("CAST(i.impacto AS TEXT) ILIKE %s")
                    )
                    args.append(like_val)
                if text_clauses:
                    where_sql_parts.append(sql.SQL("(") + sql.SQL(" OR ").join(text_clauses) + sql.SQL(")"))

        if identificador:
            where_sql_parts.append(sql.SQL("i.identificador ILIKE %s"))
            args.append(f"%{identificador}%")

        if control_val and has_control:
            where_sql_parts.append(sql.SQL("i.control ILIKE %s"))
            args.append(f"%{control_val}%")

        where_sql = sql.SQL("WHERE ") + sql.SQL(" AND ").join(where_sql_parts) if where_sql_parts else sql.SQL("")

        # SELECT
        select_cols = [
            sql.SQL("i.identificador"),
            sql.SQL("i.titulo") if has_titulo else sql.SQL("NULL AS titulo"),
            sql.SQL("i.titulo_resumen") if has_titulo_resumen else sql.SQL("NULL AS titulo_resumen"),
            sql.SQL("i.titulo_corto") if has_titulo_corto else sql.SQL("NULL AS titulo_corto"),
            sql.SQL("i.titulo_completo") if has_titulo_completo else sql.SQL("NULL AS titulo_completo"),
            sql.SQL("i.resumen") if has_resumen else sql.SQL("NULL AS resumen"),
            (sql.SQL("i.informe_impacto AS impacto") if has_informe_imp else (sql.SQL("i.impacto AS impacto") if has_impacto else sql.SQL("NULL AS impacto"))),
            sql.SQL("i.departamento_codigo"),
            sql.SQL("i.seccion_codigo"),
            sql.SQL("i.epigrafe"),
            (sql.SQL("i.fecha_publicacion") if has_fecha_public else sql.SQL("NULL AS fecha_publicacion")),
            created_expr,
            sql.SQL("i.control") if has_control else sql.SQL("NULL AS control"),

            # Fase 4: devolver categorías (defensivo)
            (sql.SQL("i.category_l1") if has_category_l1 else sql.SQL("NULL AS category_l1")),
            (sql.SQL("COALESCE(i.category_l2, ARRAY[]::text[]) AS category_l2") if has_category_l2 else sql.SQL("ARRAY[]::text[] AS category_l2")),
        ]

        joins: List[sql.SQL] = []

        # Lookups nombres
        dep_table = None
        for t in ("departamentos", "lookup_departamentos", "cat_departamentos", "dim_departamentos", "departamentos_lookup"):
            if _table_exists_cached(schema, t):
                dep_table = t
                break
        sec_table = None
        for t in ("secciones", "lookup_secciones", "cat_secciones", "dim_secciones", "secciones_lookup"):
            if _table_exists_cached(schema, t):
                sec_table = t
                break

        if dep_table:
            select_cols.append(sql.SQL("d.nombre AS departamento_nombre"))
            joins.append(sql.SQL("LEFT JOIN {} d ON d.codigo = i.departamento_codigo").format(sql.Identifier(dep_table)))
        else:
            select_cols.append(sql.SQL("NULL AS departamento_nombre"))

        if sec_table:
            select_cols.append(sql.SQL("s.nombre AS seccion_nombre"))
            joins.append(sql.SQL("LEFT JOIN {} s ON s.codigo = i.seccion_codigo").format(sql.Identifier(sec_table)))
        else:
            select_cols.append(sql.SQL("NULL AS seccion_nombre"))

        if use_reactions:
            joins.append(_reactions_agg_join_sql())
            select_cols.append(sql.SQL("COALESCE(r.likes_calc, 0) AS likes"))
            select_cols.append(sql.SQL("COALESCE(r.dislikes_calc, 0) AS dislikes"))
        else:
            select_cols.append(sql.SQL("COALESCE(i.likes, 0) AS likes") if has_likes_legacy else sql.SQL("0 AS likes"))
            select_cols.append(sql.SQL("COALESCE(i.dislikes, 0) AS dislikes") if has_dislikes_legacy else sql.SQL("0 AS dislikes"))

        # ORDER BY
        order_params: List[Any] = []
        if sort_by == "relevancia" and _use_fts_rank:
            order_by_sql = sql.SQL("ts_rank(i.fts, plainto_tsquery(%s, %s)) ")
            order_params = [_ts_lang(), q_adv]
            sort_dir_sql = sql.SQL("ASC") if sort_dir == "asc" else sql.SQL("DESC")
            order_clause = order_by_sql + sort_dir_sql
        else:
            if sort_by == "created_at":
                sort_by_sql = created_order_col
            elif sort_by == "likes":
                sort_by_sql = sql.SQL("likes")
            elif sort_by == "dislikes":
                sort_by_sql = sql.SQL("dislikes")
            else:
                cand = sql.SQL(f"i.{sort_by}")
                if (sort_by == "id" and not has_id) or (sort_by == "titulo" and not has_titulo):
                    cand = created_order_col
                sort_by_sql = cand

            sort_dir_sql = sql.SQL("ASC") if sort_dir == "asc" else sql.SQL("DESC")
            order_clause = sort_by_sql + sql.SQL(" ") + sort_dir_sql
            order_params = []

        base_select_sql = sql.SQL("""
            SELECT {cols}
            FROM items i
            {joins}
            {where}
            ORDER BY {order_clause}
            LIMIT %s OFFSET %s
        """).format(
            cols=sql.SQL(", ").join(select_cols),
            joins=sql.SQL(" ").join(joins),
            where=where_sql,
            order_clause=order_clause,
        )

        count_sql = sql.SQL("SELECT COUNT(*) FROM items i ") + where_sql
        offset = (page - 1) * limit

        with conn.cursor() as cur:
            t_count = time.time()
            cur.execute(count_sql, args)
            total = cur.fetchone()[0]
            ms_count = int((time.time() - t_count) * 1000)

            t_sel = time.time()
            cur.execute(base_select_sql, args + order_params + [limit, offset])
            data = _rows(cur)
            ms_sel = int((time.time() - t_sel) * 1000)

    ms_total = int((time.time() - t0) * 1000)
    print(f"[items_svc.search_items] ms_total={ms_total} ms_count={ms_count} ms_select={ms_sel} total={total} page={page} limit={limit}")

    return {
        "items": data,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
    }

def get_filtered_items(params: Dict[str, Any]) -> Dict[str, Any]:
    return search_items(params)

# ====================== Detalle & derivados ======================

def get_item_by_id(identificador: str, *, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    identificador = (identificador or "").strip()
    if not identificador:
        return None

    with get_db() as conn:
        schema = _load_schema_cache(conn)

        def _col(c: str) -> bool:
            return _col_exists_cached(schema, "items", c)

        with conn.cursor() as cur:
            base_cols = [
                "identificador",
                "titulo", "titulo_resumen", "titulo_corto", "titulo_completo",
                "contenido", "resumen",
                "departamento_codigo", "seccion_codigo", "epigrafe",
                # Fase 4:
                "category_l1", "category_l2",
            ]
            existing_cols = [c for c in base_cols if _col(c)]

            impacto_expr = None
            if _col("informe_impacto"):
                impacto_expr = "i.informe_impacto AS impacto"
            elif _col("impacto"):
                impacto_expr = "i.impacto AS impacto"

            url_pdf_expr = None
            for cand in ("url_pdf", "pdf_url", "pdf", "urlPdf"):
                if _col(cand):
                    url_pdf_expr = f"i.{cand} AS url_pdf"
                    break

            source_url_expr = None
            for cand in ("sourceUrl", "url_boe"):
                if _col(cand):
                    source_url_expr = f"i.{cand} AS sourceUrl"
                    break

            fecha_pub_expr = "i.fecha_publicacion AS fecha_publicacion" if _col("fecha_publicacion") else "NULL AS fecha_publicacion"

            use_reactions = _reactions_table_exists(schema)
            has_likes_legacy = _col("likes")
            has_dislikes_legacy = _col("dislikes")

            sel_parts: List[str] = [f"i.{c}" for c in existing_cols]
            sel_parts.append(fecha_pub_expr)
            if impacto_expr:    sel_parts.append(impacto_expr)
            if url_pdf_expr:    sel_parts.append(url_pdf_expr)
            if source_url_expr: sel_parts.append(source_url_expr)

            dep_table = None
            for t in ("departamentos", "lookup_departamentos", "cat_departamentos", "dim_departamentos", "departamentos_lookup"):
                if _table_exists_cached(schema, t):
                    dep_table = t
                    break
            sec_table = None
            for t in ("secciones", "lookup_secciones", "cat_secciones", "dim_secciones", "secciones_lookup"):
                if _table_exists_cached(schema, t):
                    sec_table = t
                    break

            if dep_table:
                sel_parts.append("d.nombre AS departamento_nombre")
            else:
                sel_parts.append("NULL AS departamento_nombre")

            if sec_table:
                sel_parts.append("s.nombre AS seccion_nombre")
            else:
                sel_parts.append("NULL AS seccion_nombre")

            params2: List[Any] = [identificador]
            joins_sql: List[str] = []

            if dep_table:
                joins_sql.append(f"LEFT JOIN {dep_table} d ON d.codigo = i.departamento_codigo")
            if sec_table:
                joins_sql.append(f"LEFT JOIN {sec_table} s ON s.codigo = i.seccion_codigo")

            if use_reactions:
                joins_sql.append(
                    """
                    LEFT JOIN (
                      SELECT
                        item_id,
                        COALESCE(SUM(CASE WHEN reaction = 1 THEN 1 ELSE 0 END), 0)::int  AS likes_calc,
                        COALESCE(SUM(CASE WHEN reaction = -1 THEN 1 ELSE 0 END), 0)::int AS dislikes_calc
                      FROM item_reactions
                      GROUP BY item_id
                    ) r ON r.item_id = i.identificador
                    """
                )
                sel_parts.append("COALESCE(r.likes_calc, 0) AS likes")
                sel_parts.append("COALESCE(r.dislikes_calc, 0) AS dislikes")
                if user_id:
                    sel_parts.append(
                        "(SELECT COALESCE(MAX(reaction),0)::int FROM item_reactions WHERE item_id=i.identificador AND user_id=%s) AS my_reaction"
                    )
                    params2.append(user_id)
                else:
                    sel_parts.append("0 AS my_reaction")
            else:
                sel_parts.append("COALESCE(i.likes,0) AS likes" if has_likes_legacy else "0 AS likes")
                sel_parts.append("COALESCE(i.dislikes,0) AS dislikes" if has_dislikes_legacy else "0 AS dislikes")
                sel_parts.append("0 AS my_reaction")

            sel = ", ".join(sel_parts) or "i.identificador"
            joins = " ".join(joins_sql)

            cur.execute(
                f"SELECT {sel} FROM items i {joins} WHERE i.identificador = %s LIMIT 1",
                tuple(params2),
            )
            row = cur.fetchone()
            if not row:
                return None

            names = [desc.name for desc in cur.description]
            data = dict(zip(names, row))

    for k in (
        "titulo", "titulo_resumen", "titulo_corto", "titulo_completo",
        "contenido", "resumen", "impacto",
        "likes", "dislikes", "my_reaction",
        "control", "departamento_codigo", "seccion_codigo", "epigrafe",
        "fecha_publicacion", "url_pdf", "sourceUrl", "departamento_nombre", "seccion_nombre",
        # Fase 4:
        "category_l1", "category_l2",
    ):
        data.setdefault(k, None)

    # Normaliza category_l2 a lista
    if data.get("category_l2") is None:
        data["category_l2"] = []

    if data.get("resumen") is not None:
        inflated = _inflate_b64_gzip_maybe(data["resumen"])
        data["resumen"] = (inflated or "").strip() or None

    if "impacto" in data and data["impacto"] is not None:
        imp_text = _inflate_b64_gzip_maybe(data["impacto"])
        data["impacto"] = _parse_json_maybe(imp_text)

    try:
        data["my_reaction"] = int(data.get("my_reaction") or 0)
    except Exception:
        data["my_reaction"] = 0

    return data

def get_item_resumen(identificador: str) -> Dict[str, Any]:
    with get_db() as conn:
        schema = _load_schema_cache(conn)
        if not _col_exists_cached(schema, "items", "resumen"):
            return {"identificador": identificador, "resumen": None}
        with conn.cursor() as cur:
            cur.execute("SELECT resumen FROM items WHERE identificador = %s LIMIT 1", (identificador,))
            row = cur.fetchone()
    raw = row[0] if row else None
    inflated = _inflate_b64_gzip_maybe(raw)
    text = (inflated or "").strip()
    return {"identificador": identificador, "resumen": text if text != "" else None}

def get_item_impacto(identificador: str) -> Dict[str, Any]:
    with get_db() as conn:
        schema = _load_schema_cache(conn)
        col = None
        if _col_exists_cached(schema, "items", "informe_impacto"):
            col = "informe_impacto"
        elif _col_exists_cached(schema, "items", "impacto"):
            col = "impacto"
        else:
            return {"identificador": identificador, "impacto": None}

        with conn.cursor() as cur:
            cur.execute(f"SELECT {col} FROM items WHERE identificador = %s LIMIT 1", (identificador,))
            row = cur.fetchone()

    raw = row[0] if row else None
    inflated = _inflate_b64_gzip_maybe(raw)
    parsed = _parse_json_maybe(inflated)
    if isinstance(parsed, str) and parsed.strip() == "":
        parsed = None
    return {"identificador": identificador, "impacto": parsed}

def like_item(identificador: str) -> Dict[str, Any]:
    return {"identificador": identificador, "detail": "Use reactions_svc via blueprint", "ok": False}

def dislike_item(identificador: str) -> Dict[str, Any]:
    return {"identificador": identificador, "detail": "Use reactions_svc via blueprint", "ok": False}

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
    with get_db() as conn:
        schema = _load_schema_cache(conn)
        if not _col_exists_cached(schema, "items", "epigrafe"):
            return []
        with conn.cursor() as cur:
            cur.execute(sql_text)
            return [r[0] for r in cur.fetchall()]
