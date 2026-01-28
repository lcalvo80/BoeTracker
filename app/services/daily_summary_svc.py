# app/services/daily_summary_svc.py
from __future__ import annotations

"""Persistencia y lectura del Resumen Diario por secciones.

Tabla: daily_section_summaries

Mejoras:
- list_available_days() para servir /api/resumen/index con metadata útil (total entradas, updated_at).
- Inserción JSONB más limpia (psycopg2.extras.Json si está disponible).
"""

import json
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from app.services.postgres import get_db

try:
    from psycopg2.extras import Json as PgJson  # type: ignore
except Exception:  # pragma: no cover
    PgJson = None  # type: ignore


TABLE = "public.daily_section_summaries"


def _ensure_table_cur(cur) -> None:
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id BIGSERIAL PRIMARY KEY,
            fecha_publicacion DATE NOT NULL,
            seccion_codigo TEXT NOT NULL,
            seccion_nombre TEXT NOT NULL,
            total_entradas INTEGER NOT NULL DEFAULT 0,
            resumen_texto TEXT NOT NULL,
            resumen_json JSONB,
            ai_model TEXT,
            ai_prompt_version SMALLINT NOT NULL DEFAULT 1,
            source_dept_counts JSONB,
            source_sample_items JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (fecha_publicacion, seccion_codigo)
        );
        """
    )
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_daily_section_summaries_fecha ON {TABLE} (fecha_publicacion DESC);"
    )
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_daily_section_summaries_codigo ON {TABLE} (seccion_codigo);"
    )


def _pg_json(val: Any):
    if val is None:
        return None
    if PgJson is None:
        # Fallback: string JSON (funciona, pero luego puede volver como str)
        return json.dumps(val, ensure_ascii=False)
    return PgJson(val, dumps=lambda x: json.dumps(x, ensure_ascii=False))


def ensure_table() -> None:
    with get_db() as conn, conn.cursor() as cur:
        _ensure_table_cur(cur)


def get_latest_date() -> Optional[date]:
    with get_db() as conn, conn.cursor() as cur:
        _ensure_table_cur(cur)
        cur.execute(f"SELECT MAX(fecha_publicacion) FROM {TABLE}")
        row = cur.fetchone()
        return row[0] if row and row[0] else None


def list_available_dates(*, limit: int = 30, offset: int = 0) -> List[str]:
    limit = max(1, min(int(limit), 365))
    offset = max(0, int(offset))
    with get_db() as conn, conn.cursor() as cur:
        _ensure_table_cur(cur)
        cur.execute(
            f"""
            SELECT DISTINCT fecha_publicacion
            FROM {TABLE}
            ORDER BY fecha_publicacion DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        return [r[0].isoformat() for r in cur.fetchall() if r and r[0]]


def list_available_days(*, limit: int = 30, offset: int = 0) -> List[Dict[str, Any]]:
    """Devuelve metadata por día (para /api/resumen/index)."""
    limit = max(1, min(int(limit), 365))
    offset = max(0, int(offset))

    with get_db() as conn, conn.cursor() as cur:
        _ensure_table_cur(cur)
        cur.execute(
            f"""
            SELECT
              fecha_publicacion,
              COALESCE(SUM(total_entradas), 0) AS total_entradas,
              MAX(updated_at) AS updated_at
            FROM {TABLE}
            GROUP BY fecha_publicacion
            ORDER BY fecha_publicacion DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        rows = cur.fetchall() or []

    out: List[Dict[str, Any]] = []
    for fp, total, updated_at in rows:
        iso = fp.isoformat()
        out.append(
            {
                "fecha_publicacion": iso,
                "yyyymmdd": iso.replace("-", ""),
                "total_entradas": int(total or 0),
                "updated_at": updated_at.isoformat() if updated_at else None,
                "title": f"Resumen BOE — {iso}",
                "meta_description": "Lo más relevante del BOE del día, resumido por secciones para empresa y compliance.",
            }
        )
    return out


def get_daily_summary(*, fecha_publicacion: date) -> Dict[str, Any]:
    with get_db() as conn, conn.cursor() as cur:
        _ensure_table_cur(cur)
        cur.execute(
            f"""
            SELECT
              fecha_publicacion,
              seccion_codigo,
              seccion_nombre,
              total_entradas,
              resumen_texto,
              resumen_json,
              updated_at
            FROM {TABLE}
            WHERE fecha_publicacion = %s
            ORDER BY seccion_codigo
            """,
            (fecha_publicacion,),
        )
        rows = cur.fetchall() or []

    sections: List[Dict[str, Any]] = []
    for r in rows:
        fp, code, name, total, resumen_txt, resumen_js, updated_at = r
        js = resumen_js
        if isinstance(js, str) and js.strip().startswith("{"):
            try:
                js = json.loads(js)
            except Exception:
                js = None

        sections.append(
            {
                "codigo": code,
                "nombre": name,
                "total_entradas": int(total or 0),
                "resumen": (resumen_txt or "").strip(),
                "resumen_json": js,
                "updated_at": updated_at.isoformat() if updated_at else None,
            }
        )

    return {
        "fecha_publicacion": fecha_publicacion.isoformat(),
        "secciones": sections,
    }


def get_section_row_meta(*, fecha_publicacion: date, seccion_codigo: str) -> Optional[Tuple[int, str]]:
    """Devuelve (ai_prompt_version, resumen_texto) o None si no existe."""
    with get_db() as conn, conn.cursor() as cur:
        _ensure_table_cur(cur)
        cur.execute(
            f"""
            SELECT ai_prompt_version, resumen_texto
            FROM {TABLE}
            WHERE fecha_publicacion=%s AND seccion_codigo=%s
            """,
            (fecha_publicacion, seccion_codigo),
        )
        row = cur.fetchone()
        if not row:
            return None
        ver = int(row[0] or 0)
        txt = (row[1] or "").strip()
        return ver, txt


def upsert_section_summary(
    *,
    fecha_publicacion: date,
    seccion_codigo: str,
    seccion_nombre: str,
    total_entradas: int,
    resumen_texto: str,
    resumen_json: Optional[Dict[str, Any]],
    ai_model: str,
    ai_prompt_version: int,
    source_dept_counts: Optional[List[Tuple[str, int]]] = None,
    source_sample_items: Optional[List[Dict[str, Any]]] = None,
) -> None:
    with get_db() as conn, conn.cursor() as cur:
        _ensure_table_cur(cur)

        cur.execute(
            f"""
            INSERT INTO {TABLE} (
              fecha_publicacion,
              seccion_codigo,
              seccion_nombre,
              total_entradas,
              resumen_texto,
              resumen_json,
              ai_model,
              ai_prompt_version,
              source_dept_counts,
              source_sample_items,
              created_at,
              updated_at
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW()
            )
            ON CONFLICT (fecha_publicacion, seccion_codigo)
            DO UPDATE SET
              seccion_nombre=EXCLUDED.seccion_nombre,
              total_entradas=EXCLUDED.total_entradas,
              resumen_texto=EXCLUDED.resumen_texto,
              resumen_json=EXCLUDED.resumen_json,
              ai_model=EXCLUDED.ai_model,
              ai_prompt_version=EXCLUDED.ai_prompt_version,
              source_dept_counts=EXCLUDED.source_dept_counts,
              source_sample_items=EXCLUDED.source_sample_items,
              updated_at=NOW();
            """,
            (
                fecha_publicacion,
                str(seccion_codigo or "").strip(),
                str(seccion_nombre or "").strip(),
                int(total_entradas or 0),
                str(resumen_texto or "").strip(),
                _pg_json(resumen_json) if isinstance(resumen_json, dict) else None,
                (ai_model or "").strip() or None,
                int(ai_prompt_version or 1),
                _pg_json(source_dept_counts) if source_dept_counts is not None else None,
                _pg_json(source_sample_items) if source_sample_items is not None else None,
            ),
        )
