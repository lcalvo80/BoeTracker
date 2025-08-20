# app/services/lookup.py
import os
from typing import Optional, List, Dict
from psycopg2 import sql
from app.services.postgres import get_db

# ENV opcionales
ENV_DEP_TABLE = os.getenv("LOOKUP_DEPARTAMENTOS_TABLE")  # p.ej., "departamentos"
ENV_SEC_TABLE = os.getenv("LOOKUP_SECCIONES_TABLE")      # p.ej., "secciones"
ENV_CODE_COL  = os.getenv("LOOKUP_CODE_COLUMN", "codigo")
ENV_NAME_COL  = os.getenv("LOOKUP_NAME_COLUMN", "nombre")

DEP_TABLE_CANDIDATES = [t for t in [ENV_DEP_TABLE, "departamentos", "lookup_departamentos", "cat_departamentos", "dim_departamentos"] if t]
SEC_TABLE_CANDIDATES = [t for t in [ENV_SEC_TABLE, "secciones", "lookup_secciones", "cat_secciones", "dim_secciones"] if t]

def _table_exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
            LIMIT 1
        """, (table,))
        return cur.fetchone() is not None

def _column_exists(conn, table: str, column: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
            LIMIT 1
        """, (table, column))
        return cur.fetchone() is not None

def _first_existing_table(conn, candidates: List[str]) -> Optional[str]:
    for t in candidates:
        if _table_exists(conn, t):
            return t
    return None

def list_lookup(table_candidates: List[str], code_col: str = ENV_CODE_COL, name_col: str = ENV_NAME_COL) -> List[Dict]:
    with get_db() as conn:
        table = _first_existing_table(conn, table_candidates)
        if not table:
            return []
        has_code = _column_exists(conn, table, code_col)
        has_name = _column_exists(conn, table, name_col)
        if not has_code and not has_name:
            return []

        if has_code and has_name:
            q = sql.SQL("""
                SELECT DISTINCT {code} AS codigo, {name} AS nombre
                FROM {tbl}
                WHERE TRIM(COALESCE({code}::text,'')) <> ''
                ORDER BY nombre, codigo
            """).format(code=sql.Identifier(code_col),
                        name=sql.Identifier(name_col),
                        tbl=sql.Identifier(table))
            with conn.cursor() as cur:
                cur.execute(q)
                return [{"codigo": r[0], "nombre": r[1]} for r in cur.fetchall()]
        elif has_code:
            q = sql.SQL("""
                SELECT DISTINCT {code} AS codigo
                FROM {tbl}
                WHERE TRIM(COALESCE({code}::text,'')) <> ''
                ORDER BY codigo
            """).format(code=sql.Identifier(code_col),
                        tbl=sql.Identifier(table))
            with conn.cursor() as cur:
                cur.execute(q)
                return [{"codigo": r[0], "nombre": None} for r in cur.fetchall()]
        else:
            q = sql.SQL("""
                SELECT DISTINCT {name} AS nombre
                FROM {tbl}
                WHERE TRIM(COALESCE({name}::text,'')) <> ''
                ORDER BY nombre
            """).format(name=sql.Identifier(name_col),
                        tbl=sql.Identifier(table))
            with conn.cursor() as cur:
                cur.execute(q)
                return [{"codigo": r[0], "nombre": r[0]} for r in cur.fetchall()]

def list_departamentos_lookup() -> List[Dict]:
    return list_lookup(DEP_TABLE_CANDIDATES)

def list_secciones_lookup() -> List[Dict]:
    return list_lookup(SEC_TABLE_CANDIDATES)
