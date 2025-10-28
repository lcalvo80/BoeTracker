# app/services/lookup.py
from __future__ import annotations

from typing import List, Dict, Tuple, Optional, Iterable
import logging
from psycopg2 import sql
from app.services.postgres import get_db

# ───────────────────────── Helpers internos ─────────────────────────

def _split_schema_and_table(name: str) -> Tuple[str, str]:
    """Devuelve (schema, table) con schema='public' por defecto."""
    if "." in name:
        schema, table = name.split(".", 1)
        return schema, table
    return "public", name

def _table_exists(table_name: str) -> bool:
    """Comprueba existencia de tabla (admite schema.table)."""
    schema, table = _split_schema_and_table(table_name)
    with get_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
            LIMIT 1
            """,
            (schema, table),
        )
        return cur.fetchone() is not None

def _list_columns(table_name: str) -> List[str]:
    """Lista columnas de una tabla (admite schema.table)."""
    schema, table = _split_schema_and_table(table_name)
    with get_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, table),
        )
        return [r[0] for r in cur.fetchall()]

def _first_present(candidates: Iterable[str], haystack: Iterable[str]) -> Optional[str]:
    s = {c.lower() for c in haystack}
    for c in candidates:
        if c.lower() in s:
            return c
    return None

def _norm_code_expr(identifier: sql.Identifier) -> sql.SQL:
    """
    Expr SQL que normaliza códigos:
      - castea a texto
      - quita ceros a la izquierda
      - si queda vacío → '0'
    """
    return sql.SQL(
        "COALESCE(NULLIF(REGEXP_REPLACE({col}::text, '^0+', ''), ''), '0')"
    ).format(col=identifier)

def _select_lookup_generic(
    table_name: str,
    code_candidates: List[str],
    name_candidates: List[str],
) -> List[Dict[str, str]]:
    """
    Selecciona pares {value, label} desde una tabla de catálogo flexible.
    """
    schema, table = _split_schema_and_table(table_name)
    cols = _list_columns(table_name)
    code_col = _first_present(code_candidates, cols)
    name_col = _first_present(name_candidates, cols)

    if not code_col:
        raise RuntimeError(f"No se encontró columna de código en {table_name}. Cols: {cols}")
    if not name_col:
        # A falta de nombre, usamos el propio código como label
        name_col = code_col

    with get_db() as conn, conn.cursor() as cur:
        val_expr = _norm_code_expr(sql.Identifier(code_col))
        q = sql.SQL(
            """
            SELECT {val_expr} AS value, {label}::text AS label
            FROM {schema}.{table}
            WHERE {code} IS NOT NULL
            GROUP BY value, label
            ORDER BY label ASC
            """
        ).format(
            val_expr=val_expr,
            label=sql.Identifier(name_col),
            schema=sql.Identifier(schema),
            table=sql.Identifier(table),
            code=sql.Identifier(code_col),
        )
        cur.execute(q)
        rows = cur.fetchall()
        return [{"value": r[0], "label": r[1] or r[0]} for r in rows]

def _fallback_from_items(
    # Fallback para cuando no hay tablas de catálogo:
    # intenta deducir {value, label} desde la tabla items.
    code_candidates: List[str],
    name_candidates: List[str],
) -> List[Dict[str, str]]:
    if not _table_exists("items"):
        logging.warning("Fallback desde items imposible: no existe tabla 'items'")
        return []

    cols = _list_columns("items")
    code_col = _first_present(code_candidates, cols)
    name_col = _first_present(name_candidates, cols)

    if not code_col and not name_col:
        logging.warning("No hay columnas compatibles en items para fallback")
        return []

    schema, table = "public", "items"
    with get_db() as conn, conn.cursor() as cur:
        if code_col and name_col:
            val_expr = _norm_code_expr(sql.Identifier(code_col))
            q = sql.SQL(
                """
                SELECT {val_expr} AS value, {label}::text AS label
                FROM {schema}.{table}
                WHERE {code} IS NOT NULL OR {label} IS NOT NULL
                GROUP BY value, label
                ORDER BY label ASC
                """
            ).format(
                val_expr=val_expr,
                label=sql.Identifier(name_col),
                schema=sql.Identifier(schema),
                table=sql.Identifier(table),
                code=sql.Identifier(code_col),
            )
        elif code_col:
            val_expr = _norm_code_expr(sql.Identifier(code_col))
            q = sql.SQL(
                """
                SELECT {val_expr} AS value, {val_expr} AS label
                FROM {schema}.{table}
                WHERE {code} IS NOT NULL
                GROUP BY value
                ORDER BY value ASC
                """
            ).format(
                val_expr=val_expr,
                schema=sql.Identifier(schema),
                table=sql.Identifier(table),
                code=sql.Identifier(code_col),
            )
        else:  # solo nombre
            q = sql.SQL(
                """
                SELECT DISTINCT {label}::text AS value, {label}::text AS label
                FROM {schema}.{table}
                WHERE {label} IS NOT NULL AND {label}::text <> ''
                ORDER BY label ASC
                """
            ).format(
                label=sql.Identifier(name_col),
                schema=sql.Identifier(schema),
                table=sql.Identifier(table),
            )

        cur.execute(q)
        rows = cur.fetchall()
        return [{"value": r[0], "label": r[1]} for r in rows]

# ─────────────────────── API pública usada por items_svc ───────────────────────

def list_departamentos_lookup() -> List[Dict[str, str]]:
    """
    Devuelve [{ value, label }] para departamentos.
    Intenta primero tablas de catálogo y luego hace fallback desde items.
    Normaliza códigos (sin ceros a la izquierda).
    """
    # Tablas candidatas (prioridad)
    catalog_candidates = ["public.departamentos_lookup", "public.departamentos"]

    # Candidatos de columnas
    code_candidates = ["codigo", "code", "cod", "id", "value", "departamento_codigo", "departamento_cod", "departamento_code"]
    name_candidates = ["nombre", "name", "label", "descripcion", "departamento_nombre", "departamento_name", "departamento"]

    for tbl in catalog_candidates:
        if _table_exists(tbl):
            try:
                return _select_lookup_generic(tbl, code_candidates, name_candidates)
            except Exception as e:
                logging.warning(f"No se pudo leer {tbl}: {e}")

    # Fallback desde items
    return _fallback_from_items(
        code_candidates=["departamento_codigo", "departamento_cod", "departamento_code", "departamento", "depto", "dep", "departamento_id", "dep_id", "codigo_departamento"],
        name_candidates=["departamento_nombre", "departamento_name", "departamento", "dep_nombre", "dep_name"],
    )

def list_secciones_lookup() -> List[Dict[str, str]]:
    """
    Devuelve [{ value, label }] para secciones.
    """
    catalog_candidates = ["public.secciones_lookup", "public.secciones"]
    code_candidates = ["codigo", "code", "cod", "id", "value", "seccion_codigo", "seccion_cod", "seccion_code", "seccion"]
    name_candidates = ["nombre", "name", "label", "descripcion", "seccion_nombre", "seccion_name", "seccion"]

    for tbl in catalog_candidates:
        if _table_exists(tbl):
            try:
                return _select_lookup_generic(tbl, code_candidates, name_candidates)
            except Exception as e:
                logging.warning(f"No se pudo leer {tbl}: {e}")

    # Fallback desde items
    return _fallback_from_items(
        code_candidates=["seccion_codigo", "seccion_cod", "seccion_code", "seccion"],
        name_candidates=["seccion_nombre", "seccion_name", "seccion"],
    )
