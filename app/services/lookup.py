# app/services/lookup.py
import os
from typing import Optional, List, Dict, Literal
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

# ────────────────────────── NUEVO: upsert idempotente ──────────────────────────

def _norm_code_py(code: Optional[str]) -> str:
    s = (code or "").strip()
    if s == "":
        return ""
    n = s.lstrip("0")
    return n if n != "" else "0"

def _ensure_lookup_cur(
    cur,
    table_candidates: List[str],
    codigo: Optional[str],
    nombre: Optional[str],
    code_col: str = ENV_CODE_COL,
    name_col: str = ENV_NAME_COL,
) -> Literal["insert", "update_name", "noop", "skip_no_table", "skip_need_code_or_name"]:
    """
    Garantiza que (codigo,nombre) exista en la tabla de lookup. Comparación por código normalizado (sin ceros a la izq).
    - Si existen ambas columnas (codigo, nombre):
        * si existe por código (normalizado) y nombre vacío -> update nombre
        * si no existe -> insert (codigo, nombre)
    - Si solo hay 'codigo' -> inserta código si no existe
    - Si solo hay 'nombre' -> inserta nombre si no existe
    """
    conn = cur.connection
    table = _first_existing_table(conn, table_candidates)
    if not table:
        return "skip_no_table"

    has_code = _column_exists(conn, table, code_col)
    has_name = _column_exists(conn, table, name_col)

    codigo_raw = (codigo or "").strip()
    nombre_raw = (nombre or "").strip()
    codigo_norm = _norm_code_py(codigo_raw)

    if not has_code and not has_name:
        return "skip_no_table"

    if has_code and has_name:
        if not codigo_norm:
            if not nombre_raw:
                return "skip_need_code_or_name"
            # Si no hay código y la tabla lo requiere, evitamos insertar fila incompleta.
            q_sel_by_name = sql.SQL("""
                SELECT 1 FROM {tbl}
                WHERE TRIM(COALESCE({name}::text,'')) = %s
                LIMIT 1
            """).format(tbl=sql.Identifier(table), name=sql.Identifier(name_col))
            cur.execute(q_sel_by_name, (nombre_raw,))
            return "noop" if cur.fetchone() else "skip_need_code_or_name"

        q_sel = sql.SQL("""
            SELECT {name}
            FROM {tbl}
            WHERE REGEXP_REPLACE(TRIM({code}::text), '^0+', '') = %s
            LIMIT 1
        """).format(tbl=sql.Identifier(table),
                    name=sql.Identifier(name_col),
                    code=sql.Identifier(code_col))
        cur.execute(q_sel, (codigo_norm,))
        row = cur.fetchone()
        if row:
            current_name = (row[0] or "").strip()
            if not current_name and nombre_raw:
                q_upd = sql.SQL("""
                    UPDATE {tbl}
                    SET {name} = %s
                    WHERE REGEXP_REPLACE(TRIM({code}::text), '^0+', '') = %s
                """).format(tbl=sql.Identifier(table),
                            name=sql.Identifier(name_col),
                            code=sql.Identifier(code_col))
                cur.execute(q_upd, (nombre_raw, codigo_norm))
                return "update_name"
            return "noop"

        q_ins = sql.SQL("""
            INSERT INTO {tbl} ({code}, {name})
            VALUES (%s, %s)
        """).format(tbl=sql.Identifier(table),
                    code=sql.Identifier(code_col),
                    name=sql.Identifier(name_col))
        cur.execute(q_ins, (codigo_raw, (nombre_raw or None)))
        return "insert"

    if has_code and not has_name:
        if not codigo_norm:
            return "skip_need_code_or_name"
        q_sel = sql.SQL("""
            SELECT 1 FROM {tbl}
            WHERE REGEXP_REPLACE(TRIM({code}::text), '^0+', '') = %s
            LIMIT 1
        """).format(tbl=sql.Identifier(table), code=sql.Identifier(code_col))
        cur.execute(q_sel, (codigo_norm,))
        if cur.fetchone():
            return "noop"
        q_ins = sql.SQL("""
            INSERT INTO {tbl} ({code}) VALUES (%s)
        """).format(tbl=sql.Identifier(table), code=sql.Identifier(code_col))
        cur.execute(q_ins, (codigo_raw,))
        return "insert"

    if not has_code and has_name:
        if not nombre_raw:
            return "skip_need_code_or_name"
        q_sel = sql.SQL("""
            SELECT 1 FROM {tbl}
            WHERE TRIM(COALESCE({name}::text,'')) = %s
            LIMIT 1
        """).format(tbl=sql.Identifier(table), name=sql.Identifier(name_col))
        cur.execute(q_sel, (nombre_raw,))
        if cur.fetchone():
            return "noop"
        q_ins = sql.SQL("""
            INSERT INTO {tbl} ({name}) VALUES (%s)
        """).format(tbl=sql.Identifier(table), name=sql.Identifier(name_col))
        cur.execute(q_ins, (nombre_raw,))
        return "insert"

    return "noop"

def ensure_departamento_cur(cur, codigo: Optional[str], nombre: Optional[str]) -> str:
    return _ensure_lookup_cur(cur, DEP_TABLE_CANDIDATES, codigo, nombre)

def ensure_seccion_cur(cur, codigo: Optional[str], nombre: Optional[str]) -> str:
    return _ensure_lookup_cur(cur, SEC_TABLE_CANDIDATES, codigo, nombre)

# Wrappers por si se quieren usar fuera de una transacción existente
def ensure_departamento(codigo: Optional[str], nombre: Optional[str]) -> str:
    with get_db() as conn:
        with conn.cursor() as cur:
            action = ensure_departamento_cur(cur, codigo, nombre)
        conn.commit()
        return action

def ensure_seccion(codigo: Optional[str], nombre: Optional[str]) -> str:
    with get_db() as conn:
        with conn.cursor() as cur:
            action = ensure_seccion_cur(cur, codigo, nombre)
        conn.commit()
        return action
    