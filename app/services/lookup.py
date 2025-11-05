# app/services/lookup.py
from __future__ import annotations

from typing import List, Dict, Tuple, Optional, Iterable
import logging
import re
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

# ─────────────────────── Normalización y upserts de lookups ───────────────────────

def normalize_code(code: str) -> str:
    """
    Normaliza códigos tipo '0310' -> '310'.
    Si queda vacío o None -> '0'.
    """
    s = "" if code is None else str(code).strip()
    s = re.sub(r"^0+", "", s)  # quitar ceros a la izquierda
    return s or "0"

def _ensure_lookup_table_cur(cur, table: str, code_col: str = "codigo", name_col: str = "nombre") -> None:
    """
    Garantiza que exista la tabla de lookups con PK en 'codigo'.
    Usa el cursor proporcionado (misma transacción que el caller).
    """
    schema, tbl = _split_schema_and_table(table)
    q = sql.SQL(
        """
        CREATE TABLE IF NOT EXISTS {schema}.{table} (
            {code_col} TEXT PRIMARY KEY,
            {name_col} TEXT
        );
        """
    ).format(
        schema=sql.Identifier(schema),
        table=sql.Identifier(tbl),
        code_col=sql.Identifier(code_col),
        name_col=sql.Identifier(name_col),
    )
    cur.execute(q)

def _ensure_lookup_cur(
    cur,
    table: str,
    code: str,
    name: str,
    code_col: str = "codigo",
    name_col: str = "nombre",
) -> str:
    """
    Upsert de un par (codigo, nombre) en la tabla de catálogo indicada.
    Devuelve:
      - "insert" si ha insertado
      - "update_name" si ha actualizado el nombre
      - "noop" si no ha hecho cambios
    """
    code_norm = normalize_code(code)
    name_norm = (name or "").strip()

    _ensure_lookup_table_cur(cur, table, code_col=code_col, name_col=name_col)

    # SELECT nombre actual
    schema, tbl = _split_schema_and_table(table)
    q_sel = sql.SQL("SELECT {name_col} FROM {schema}.{table} WHERE {code_col} = %s").format(
        name_col=sql.Identifier(name_col),
        schema=sql.Identifier(schema),
        table=sql.Identifier(tbl),
        code_col=sql.Identifier(code_col),
    )
    cur.execute(q_sel, (code_norm,))
    row = cur.fetchone()
    if not row:
        # INSERT ON CONFLICT DO NOTHING (si ya existiera por carrera)
        q_ins = sql.SQL(
            "INSERT INTO {schema}.{table} ({code_col}, {name_col}) VALUES (%s, %s) "
            "ON CONFLICT ({code_col}) DO NOTHING"
        ).format(
            schema=sql.Identifier(schema),
            table=sql.Identifier(tbl),
            code_col=sql.Identifier(code_col),
            name_col=sql.Identifier(name_col),
        )
        cur.execute(q_ins, (code_norm, name_norm))
        return "insert"

    current_name = (row[0] or "").strip()
    if name_norm and name_norm != current_name:
        q_upd = sql.SQL(
            "UPDATE {schema}.{table} SET {name_col} = %s WHERE {code_col} = %s"
        ).format(
            schema=sql.Identifier(schema),
            table=sql.Identifier(tbl),
            name_col=sql.Identifier(name_col),
            code_col=sql.Identifier(code_col),
        )
        cur.execute(q_upd, (name_norm, code_norm))
        return "update_name"

    return "noop"

def ensure_seccion_cur(cur, codigo: str, nombre: str) -> str:
    """
    Asegura la presencia de la sección en 'public.secciones_lookup'.
    Retorna: 'insert' | 'update_name' | 'noop'
    """
    return _ensure_lookup_cur(
        cur,
        table="public.secciones_lookup",
        code=codigo,
        name=nombre,
        code_col="codigo",
        name_col="nombre",
    )

def ensure_departamento_cur(cur, codigo: str, nombre: str) -> str:
    """
    Asegura la presencia del departamento en 'public.departamentos_lookup'.
    Retorna: 'insert' | 'update_name' | 'noop'
    """
    return _ensure_lookup_cur(
        cur,
        table="public.departamentos_lookup",
        code=codigo,
        name=nombre,
        code_col="codigo",
        name_col="nombre",
    )

# ─────────────────────── Selectores genéricos para filtros ───────────────────────

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
