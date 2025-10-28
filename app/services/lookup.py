# app/services/lookup.py
from __future__ import annotations

import re
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────────
# Normalización y upsert de códigos de SECCIÓN/DEPARTAMENTO
# Esquema esperado en BD:
#   - Tabla secciones(codigo TEXT PRIMARY KEY, nombre TEXT)
#   - Tabla departamentos(codigo TEXT PRIMARY KEY, nombre TEXT)
# Notas:
#   * Los códigos se guardan como texto de 4 dígitos (ej. "0310", "1410").
#   * Si no llega nombre, se usa un placeholder ("Sección {code}" / "Dept. {code}").
#   * Si ya existe y posteriormente llega un nombre real distinto, se actualiza.
# ──────────────────────────────────────────────────────────────────────────────

_ONLY_DIGITS_RE = re.compile(r"\D+")
_WS_RE = re.compile(r"\s+")


def normalize_code(code_raw: Optional[str]) -> str:
    """
    Normaliza códigos de secciones/departamentos:
      - Mantiene solo dígitos.
      - Rellena a 4 dígitos con ceros a la izquierda (zfill(4)).
      - Si queda vacío → '0000'.
    """
    if code_raw is None:
        return "0000"
    s = str(code_raw).strip()
    s = _ONLY_DIGITS_RE.sub("", s)
    s = s.zfill(4)
    return s if s else "0000"


def _normalize_name(name_raw: Optional[str]) -> str:
    """
    Limpia el nombre: trim + colapsa espacios internos. Mantiene mayúsculas originales.
    """
    if not name_raw:
        return ""
    s = _WS_RE.sub(" ", str(name_raw).strip())
    return s


def _upsert_row_cur(cur, table: str, code: str, name: str, placeholder_prefix: str) -> str:
    """
    Upsert manual con retorno de acción:
      - 'insert'      → se ha insertado la fila (con nombre o con placeholder).
      - 'update_name' → se ha actualizado el nombre existente.
      - 'noop'        → ya existía y no se ha cambiado el nombre.

    No hace commit (lo gestiona el caller).
    """
    cur.execute(f"SELECT nombre FROM {table} WHERE codigo = %s", (code,))
    row = cur.fetchone()

    if row:
        current_name = _normalize_name(row[0] or "")
        new_name = _normalize_name(name)
        # Si llega un nombre no vacío y diferente, actualiza
        if new_name and new_name != current_name:
            cur.execute(f"UPDATE {table} SET nombre = %s WHERE codigo = %s", (new_name, code))
            return "update_name"
        return "noop"

    # Inserta aunque el nombre venga vacío → placeholder
    final_name = _normalize_name(name) or f"{placeholder_prefix} {code}"
    cur.execute(f"INSERT INTO {table} (codigo, nombre) VALUES (%s, %s)", (code, final_name))
    return "insert"


def ensure_seccion_cur(cur, codigo_raw: str, nombre_raw: str) -> str:
    """
    Garantiza que exista la fila en 'secciones' con el código normalizado.
    Inserta con placeholder si el nombre viene vacío.
    """
    code = normalize_code(codigo_raw)
    name = _normalize_name(nombre_raw)
    return _upsert_row_cur(cur, "secciones", code, name, "Sección")


def ensure_departamento_cur(cur, codigo_raw: str, nombre_raw: str) -> str:
    """
    Garantiza que exista la fila en 'departamentos' con el código normalizado.
    Inserta con placeholder si el nombre viene vacío.
    """
    code = normalize_code(codigo_raw)
    name = _normalize_name(nombre_raw)
    return _upsert_row_cur(cur, "departamentos", code, name, "Dept.")
