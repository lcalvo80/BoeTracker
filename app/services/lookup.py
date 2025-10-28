# app/services/lookup.py
from __future__ import annotations

import re
from typing import Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────────
# Normalización y upsert de SECCIONES/DEPARTAMENTOS + utilidades de introspección
# Esquema esperado:
#   secciones(codigo TEXT PRIMARY KEY, nombre TEXT)
#   departamentos(codigo TEXT PRIMARY KEY, nombre TEXT)
# Códigos guardados como texto de 4 dígitos: "0310", "1410", ...
# ──────────────────────────────────────────────────────────────────────────────

_ONLY_DIGITS_RE = re.compile(r"\D+")
_WS_RE = re.compile(r"\s+")
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z0-9_\.]+$")  # p.ej. public.departamentos


def normalize_code(code_raw: Optional[str]) -> str:
    """
    Mantiene solo dígitos y rellena a 4 con ceros (zfill(4)).
    Si queda vacío → '0000'.
    """
    if code_raw is None:
        return "0000"
    s = str(code_raw).strip()
    s = _ONLY_DIGITS_RE.sub("", s)
    s = s.zfill(4)
    return s if s else "0000"


def _normalize_name(name_raw: Optional[str]) -> str:
    """
    Limpia el nombre: trim + colapsa espacios internos.
    """
    if not name_raw:
        return ""
    return _WS_RE.sub(" ", str(name_raw).strip())


def _split_schema_table(ident: str) -> Tuple[str, str]:
    """
    'public.departamentos' -> ('public', 'departamentos')
    'items'                -> ('public', 'items')  (por defecto)
    """
    if "." in ident:
        schema, table = ident.split(".", 1)
        schema = schema.strip() or "public"
        table = table.strip()
    else:
        schema, table = "public", ident.strip()
    return schema, table


def _table_exists(cur, table_ident: str) -> bool:
    """
    Comprueba la existencia de una tabla usando information_schema.
    Se exporta porque items_svc lo importa explícitamente.

    Uso:
        with get_db() as conn:
            with conn.cursor() as cur:
                if _table_exists(cur, "public.departamentos"): ...
    """
    if not table_ident or not _SAFE_IDENT_RE.match(table_ident):
        return False

    schema, table = _split_schema_table(table_ident)
    cur.execute(
        """
        SELECT EXISTS (
          SELECT 1
          FROM information_schema.tables
          WHERE table_schema = %s AND table_name = %s
        )
        """,
        (schema, table),
    )
    row = cur.fetchone()
    return bool(row and row[0])


def _upsert_row_cur(cur, table: str, code: str, name: str, placeholder_prefix: str) -> str:
    """
    Upsert manual con retorno de acción:
      - 'insert'      → se insertó la fila (con nombre o placeholder).
      - 'update_name' → se actualizó el nombre existente.
      - 'noop'        → ya existía y no cambió el nombre.
    NO hace commit (lo gestiona el caller).
    """
    cur.execute(f"SELECT nombre FROM {table} WHERE codigo = %s", (code,))
    row = cur.fetchone()

    if row:
        current_name = _normalize_name(row[0] or "")
        new_name = _normalize_name(name)
        if new_name and new_name != current_name:
            cur.execute(f"UPDATE {table} SET nombre = %s WHERE codigo = %s", (new_name, code))
            return "update_name"
        return "noop"

    final_name = _normalize_name(name) or f"{placeholder_prefix} {code}"
    cur.execute(f"INSERT INTO {table} (codigo, nombre) VALUES (%s, %s)", (code, final_name))
    return "insert"


def ensure_seccion_cur(cur, codigo_raw: str, nombre_raw: str) -> str:
    """
    Garantiza fila en 'secciones' con código normalizado (4 dígitos).
    Inserta con placeholder si el nombre viene vacío.
    """
    code = normalize_code(codigo_raw)
    name = _normalize_name(nombre_raw)
    return _upsert_row_cur(cur, "secciones", code, name, "Sección")


def ensure_departamento_cur(cur, codigo_raw: str, nombre_raw: str) -> str:
    """
    Garantiza fila en 'departamentos' con código normalizado (4 dígitos).
    Inserta con placeholder si el nombre viene vacío.
    """
    code = normalize_code(codigo_raw)
    name = _normalize_name(nombre_raw)
    return _upsert_row_cur(cur, "departamentos", code, name, "Dept.")
