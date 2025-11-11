from __future__ import annotations

import re
from typing import List, Dict, Optional, Tuple
from psycopg2 import sql
from app.services.postgres import get_db

# ───────────────── Helpers internos ─────────────────

def normalize_code(code: Optional[str]) -> str:
    """Quita ceros a la izquierda. Si queda vacío, devuelve '0'."""
    s = "" if code is None else str(code).strip()
    s = re.sub(r"^0+", "", s)
    return s or "0"

def _table_exists(conn, table: str) -> bool:
    if not table:
        return False
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema='public' AND table_name=%s
            LIMIT 1
            """,
            (table.split(".", 1)[-1],),
        )
        return cur.fetchone() is not None

def _ensure_lookup_table_cur(cur, table: str) -> None:
    """Crea la tabla lookup si no existe: (codigo TEXT PK, nombre TEXT)."""
    schema, tbl = ("public", table.split(".", 1)[1]) if "." in table else ("public", table)
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {schema}.{table} (
                codigo TEXT PRIMARY KEY,
                nombre TEXT
            )
            """
        ).format(schema=sql.Identifier(schema), table=sql.Identifier(tbl))
    )

def _upsert_lookup_cur(cur, table: str, codigo: str, nombre: str) -> str:
    """Inserta si no existe; actualiza nombre si cambia. Devuelve 'insert'|'update_name'|'noop'."""
    _ensure_lookup_table_cur(cur, table)
    code_norm = normalize_code(codigo)
    name_norm = (nombre or "").strip()
    schema, tbl = ("public", table.split(".", 1)[1]) if "." in table else ("public", table)

    # Intento de lectura
    cur.execute(
        sql.SQL("SELECT nombre FROM {schema}.{table} WHERE codigo = %s").format(
            schema=sql.Identifier(schema), table=sql.Identifier(tbl)
        ),
        (code_norm,),
    )
    row = cur.fetchone()

    if not row:
        cur.execute(
            sql.SQL(
                "INSERT INTO {schema}.{table} (codigo, nombre) VALUES (%s, %s) "
                "ON CONFLICT (codigo) DO NOTHING"
            ).format(schema=sql.Identifier(schema), table=sql.Identifier(tbl)),
            (code_norm, name_norm),
        )
        return "insert"

    current = (row[0] or "").strip()
    if name_norm and name_norm != current:
        cur.execute(
            sql.SQL("UPDATE {schema}.{table} SET nombre = %s WHERE codigo = %s").format(
                schema=sql.Identifier(schema), table=sql.Identifier(tbl)
            ),
            (name_norm, code_norm),
        )
        return "update_name"

    return "noop"

# ───────────────── ensure_* usados por parser.py ─────────────────

def ensure_seccion_cur(cur, codigo: str, nombre: str) -> str:
    """Asegura/actualiza la fila en public.secciones_lookup. Devuelve 'insert'|'update_name'|'noop'."""
    return _upsert_lookup_cur(cur, "public.secciones_lookup", codigo, nombre)

def ensure_departamento_cur(cur, codigo: str, nombre: str) -> str:
    """Asegura/actualiza la fila en public.departamentos_lookup. Devuelve 'insert'|'update_name'|'noop'."""
    return _upsert_lookup_cur(cur, "public.departamentos_lookup", codigo, nombre)

# ───────────────── list_* usados por items_svc.py ─────────────────

_DEPT_TABLE_CANDIDATES = (
    "departamentos",
    "lookup_departamentos",
    "cat_departamentos",
    "dim_departamentos",
    "departamentos_lookup",
)

_SEC_TABLE_CANDIDATES = (
    "secciones",
    "lookup_secciones",
    "cat_secciones",
    "dim_secciones",
    "secciones_lookup",
)

def _pick_table(conn, candidates: Tuple[str, ...]) -> Optional[str]:
    for t in candidates:
        if _table_exists(conn, t):
            return t
    return None

def list_departamentos_lookup() -> List[Dict[str, str]]:
    """Devuelve [{codigo, nombre}] normalizados. Si no hay tabla, []."""
    with get_db() as conn, conn.cursor() as cur:
        table = _pick_table(conn, _DEPT_TABLE_CANDIDATES)
        if not table:
            return []
        # Normalizamos en SELECT por si hay históricos con ceros a la izquierda
        cur.execute(
            sql.SQL(
                """
                SELECT REGEXP_REPLACE(TRIM(codigo), '^0+', '') AS codigo,
                       TRIM(COALESCE(nombre, ''))           AS nombre
                FROM {tbl}
                WHERE TRIM(COALESCE(codigo, '')) <> ''
                ORDER BY nombre, codigo
                """
            ).format(tbl=sql.Identifier(table))
        )
        rows = cur.fetchall()
        out: List[Dict[str, str]] = []
        seen = set()
        for code, name in rows:
            code = code or "0"
            if code in seen:
                continue
            seen.add(code)
            out.append({"codigo": code, "nombre": name})
        return out

def list_secciones_lookup() -> List[Dict[str, str]]:
    """Devuelve [{codigo, nombre}] normalizados. Si no hay tabla, []."""
    with get_db() as conn, conn.cursor() as cur:
        table = _pick_table(conn, _SEC_TABLE_CANDIDATES)
        if not table:
            return []
        cur.execute(
            sql.SQL(
                """
                SELECT REGEXP_REPLACE(TRIM(codigo), '^0+', '') AS codigo,
                       TRIM(COALESCE(nombre, ''))           AS nombre
                FROM {tbl}
                WHERE TRIM(COALESCE(codigo, '')) <> ''
                ORDER BY nombre, codigo
                """
            ).format(tbl=sql.Identifier(table))
        )
        rows = cur.fetchall()
        out: List[Dict[str, str]] = []
        seen = set()
        for code, name in rows:
            code = code or "0"
            if code in seen:
                continue
            seen.add(code)
            out.append({"codigo": code, "nombre": name})
        return out
