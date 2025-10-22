# app/services/items_svc.py
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple
import base64, gzip, json

from psycopg2 import sql
from app.services.postgres import get_db

# catálogos
from app.services.lookup import _table_exists as _lookup_table_exists
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

def _col_exists(conn, table: str, col: str) -> bool:
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

def _fts_available(conn) -> bool:
    return _col_exists(conn, "items", "fts")

def _ts_lang(conn) -> str:
    return "spanish"

# ---- Inflado gzip+base64 seguro -----------------------------------------

def _inflate_b64_gzip_maybe(val: Any) -> Any:
    """
    Si `val` es una cadena gzip+base64 la descomprime a texto.
    Si no lo es, la devuelve tal cual.
    """
    if not isinstance(val, str) or len(val) < 8:
        return val
    try:
        b = base64.b64decode(val, validate=True)
        try:
            out = gzip.decompress(b).decode("utf-8", errors="replace")
            return out
        except Exception:
            # No era gzip tras base64; devolvemos original
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

# ====================== Búsqueda / listado ======================

def search_items(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Igual que el antiguo get_filtered_items(params).
    """
    # paginación segura
    try:
        page = max(int(params.get("page", 1)), 1)
    except Exception:
        page = 1

    try:
        limit = int(params.get("limit", 12))
        if limit < 1:  limit = 1
        if limit > 100: limit = 100
    except Exception:
        limit = 12

    # ordenación segura
    sort_by = (params.get("sort_by", "created_at") or "").lower()
    sort_dir = (params.get("sort_dir", "desc") or "").lower()
    if sort_by not in {"created_at", "titulo", "id", "likes", "dislikes", "relevancia"}:
        sort_by = "created_at"
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "desc"

    # fechas
    fecha_eq = _to_date(_norm(params.get("fecha")))
    fecha_desde = _to_date(_norm(params.get("fecha_desde")))
    fecha_hasta = _to_date(_norm(params.get("fecha_hasta")))

    # listas
    dep_list = _list_param(params, "departamento_codigo", "departamento", "departamentos")
    sec_list = _list_param(params, "seccion_codigo", "seccion", "secciones")
    epi_list = _list_param(params, "epigrafe", "epigrafes")

    # texto
    q_adv = _norm(params.get("q")) or _norm(params.get("q_adv"))
    identificador = _norm(params.get("identificador"))
    control_val = _norm(params.get("control"))

    with get_db() as conn:
        has_titulo_resumen = _col_exists(conn, "items", "titulo_resumen")
        has_titulo_corto = _col_exists(conn, "items", "titulo_corto")
        has_titulo_completo = _col_exists(conn, "items", "titulo_completo")
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

        # WHERE
        where_sql_parts: List[sql.SQL] = []
        args: List[Any] = []

        date_expr = sql.SQL("DATE(i.created_at)") if has_created_at else sql.SQL("DATE(i.created_at_date)")
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

        # texto: FTS si hay columna, si no ILIKE
        _use_fts_rank = False
        if q_adv and len(q_adv) >= 2:
            if _fts_available(conn):
                where_sql_parts.append(sql.SQL("i.fts @@ plainto_tsquery(%s, %s)"))
                args.extend([_ts_lang(conn), q_adv])
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
                if _col_exists(conn, "items", "informe_impacto"):
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
            sql.SQL("i.titulo_resumen") if has_titulo_resumen else sql.SQL("NULL AS titulo_resumen"),
            sql.SQL("i.titulo_corto") if has_titulo_corto else sql.SQL("NULL AS titulo_corto"),
            sql.SQL("i.titulo_completo") if has_titulo_completo else sql.SQL("NULL AS titulo_completo"),
            sql.SQL("i.resumen") if has_resumen else sql.SQL("NULL AS resumen"),
            sql.SQL("i.informe_impacto AS impacto") if has_informe_imp
                else (sql.SQL("i.impacto") if has_impacto else sql.SQL("NULL AS impacto")),
            sql.SQL("i.departamento_codigo"),
            sql.SQL("i.seccion_codigo"),
            sql.SQL("i.epigrafe"),
            sql.SQL("i.created_at") if _col_exists(conn, "items", "created_at") else (
                sql.SQL("i.created_at_date AS created_at") if _col_exists(conn, "items", "created_at_date") else sql.SQL("NULL AS created_at")
            ),
            sql.SQL("i.likes") if has_likes else sql.SQL("NULL AS likes"),
            sql.SQL("i.dislikes") if has_dislikes else sql.SQL("NULL AS dislikes"),
            sql.SQL("i.control") if has_control else sql.SQL("NULL AS control"),
        ]
        joins: List[sql.SQL] = []

        dep_table = None
        for t in ("departamentos", "lookup_departamentos", "cat_departamentos", "dim_departamentos"):
            if _lookup_table_exists(conn, t):
                dep_table = t; break
        sec_table = None
        for t in ("secciones", "lookup_secciones", "cat_secciones", "dim_secciones"):
            if _lookup_table_exists(conn, t):
                sec_table = t; break

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

        # ORDER BY
        if sort_by == "relevancia" and _use_fts_rank:
            order_by_sql = sql.SQL("ts_rank(i.fts, plainto_tsquery(%s, %s)) ")
            order_params: List[Any] = [_ts_lang(conn), q_adv]
            sort_dir_sql = sql.SQL("ASC") if sort_dir == "asc" else sql.SQL("DESC")
            order_clause = order_by_sql + sort_dir_sql
        else:
            sort_by_sql = sql.SQL("i.created_at") if sort_by == "created_at" else sql.SQL("i." + sort_by)
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
            cur.execute(count_sql, args)
            total = cur.fetchone()[0]
            cur.execute(base_select_sql, args + order_params + [limit, offset])
            data = _rows(cur)

    # (listado no infla por rendimiento; los detalle/derivados sí)
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

def get_item_by_id(identificador: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn, conn.cursor() as cur:
        base_cols = [
            "id", "identificador",
            "titulo", "titulo_resumen", "titulo_corto", "titulo_completo",
            "contenido", "resumen",
            "departamento_codigo", "seccion_codigo", "epigrafe",
            "created_at", "likes", "dislikes", "control"
        ]
        def _col_exists_inner(c): return _col_exists(conn, "items", c)
        existing_cols = [c for c in base_cols if _col_exists_inner(c)]

        impacto_expr = None
        if _col_exists_inner("informe_impacto"):
            impacto_expr = "i.informe_impacto AS impacto"
        elif _col_exists_inner("impacto"):
            impacto_expr = "i.impacto AS impacto"

        url_pdf_expr = None
        for cand in ("url_pdf", "pdf_url", "pdf", "urlPdf"):
            if _col_exists_inner(cand):
                url_pdf_expr = f"i.{cand} AS url_pdf"; break

        source_url_expr = None
        for cand in ("sourceUrl", "url_boe"):
            if _col_exists_inner(cand):
                source_url_expr = f"i.{cand} AS sourceUrl"; break

        dep_table = None
        for t in ("departamentos", "lookup_departamentos", "cat_departamentos", "dim_departamentos"):
            if _lookup_table_exists(conn, t):
                dep_table = t; break
        sec_table = None
        for t in ("secciones", "lookup_secciones", "cat_secciones", "dim_secciones"):
            if _lookup_table_exists(conn, t):
                sec_table = t; break

        sel_parts: List[str] = [f"i.{c}" for c in existing_cols]
        if impacto_expr:    sel_parts.append(impacto_expr)
        if url_pdf_expr:    sel_parts.append(url_pdf_expr)
        if source_url_expr: sel_parts.append(source_url_expr)
        if dep_table:       sel_parts.append("d.nombre AS departamento_nombre")
        if sec_table:       sel_parts.append("s.nombre AS seccion_nombre")

        sel = ", ".join(sel_parts) or "i.identificador"

        join_sql: List[str] = []
        if dep_table:
            join_sql.append(f'LEFT JOIN {dep_table} d ON d.codigo = i.departamento_codigo')
        if sec_table:
            join_sql.append(f'LEFT JOIN {sec_table} s ON s.codigo = i.seccion_codigo')
        joins = " ".join(join_sql)

        cur.execute(f"SELECT {sel} FROM items i {joins} WHERE i.identificador = %s LIMIT 1", (identificador,))
        row = cur.fetchone()
        if not row:
            return None
        names = [desc.name for desc in cur.description]
        data = dict(zip(names, row))

    # Normalizaciones + inflado (detalle)
    for k in ("titulo", "titulo_resumen", "titulo_corto", "titulo_completo",
              "contenido", "resumen", "impacto", "likes", "dislikes", "control",
              "departamento_codigo", "seccion_codigo", "epigrafe", "created_at",
              "url_pdf", "sourceUrl", "departamento_nombre", "seccion_nombre"):
        data.setdefault(k, None)

    # Inflar gzip+base64 si procede
    if data.get("resumen") is not None:
        inflated = _inflate_b64_gzip_maybe(data["resumen"])
        data["resumen"] = (inflated or "").strip() or None

    if "impacto" in data and data["impacto"] is not None:
        imp_text = _inflate_b64_gzip_maybe(data["impacto"])
        parsed = _parse_json_maybe(imp_text)
        data["impacto"] = parsed

    return data

def get_item_resumen(identificador: str) -> Dict[str, Any]:
    with get_db() as conn, conn.cursor() as cur:
        if not _col_exists(conn, "items", "resumen"):
            return {"identificador": identificador, "resumen": None}
        cur.execute("SELECT resumen FROM items WHERE identificador = %s LIMIT 1", (identificador,))
        row = cur.fetchone()
    raw = row[0] if row else None
    inflated = _inflate_b64_gzip_maybe(raw)
    text = (inflated or "").strip()
    return {"identificador": identificador, "resumen": text if text != "" else None}

def get_item_impacto(identificador: str) -> Dict[str, Any]:
    with get_db() as conn, conn.cursor() as cur:
        col = None
        if _col_exists(conn, "items", "informe_impacto"):
            col = "informe_impacto"
        elif _col_exists(conn, "items", "impacto"):
            col = "impacto"
        else:
            return {"identificador": identificador, "impacto": None}

        cur.execute(f"SELECT {col} FROM items WHERE identificador = %s LIMIT 1", (identificador,))
        row = cur.fetchone()

    raw = row[0] if row else None
    inflated = _inflate_b64_gzip_maybe(raw)
    parsed = _parse_json_maybe(inflated)
    # Si quedó cadena vacía -> None ; si es {} se devolverá {}
    if isinstance(parsed, str) and parsed.strip() == "":
        parsed = None
    return {"identificador": identificador, "impacto": parsed}

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
