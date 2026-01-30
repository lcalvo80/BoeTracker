# app/blueprints/seo_sitemap.py
from __future__ import annotations

import logging
import os
from datetime import date
from typing import List, Optional, Tuple

import psycopg2
from flask import Blueprint, Response, jsonify

seo_bp = Blueprint("seo_bp", __name__, url_prefix="/api/meta")
log = logging.getLogger(__name__)

# Detectamos la columna de fecha en resumen_diario (por compat)
_CANDIDATE_DATE_COLS = [
    "fecha_publicacion",
    "fecha",
    "day",
    "date",
    "fecha_boe",
    "published_date",
    "created_at",
]


def _db_url() -> str:
    """
    Railway/Prod: a veces la URL no está en DATABASE_URL, así que soportamos aliases.
    """
    for key in (
        "DATABASE_URL",
        "DATABASE_URL_PROD",
        "POSTGRES_URL",
        "POSTGRESQL_URL",
        "RAILWAY_DATABASE_URL",
    ):
        v = os.getenv(key)
        if v and v.strip():
            return v.strip()
    return ""


def _detect_resumen_diario_schema(cur) -> str:
    """
    Encuentra el schema donde vive resumen_diario (preferimos public).
    """
    cur.execute(
        """
        SELECT table_schema
        FROM information_schema.tables
        WHERE table_name = 'resumen_diario'
          AND table_type = 'BASE TABLE'
        """
    )
    schemas = [r[0] for r in cur.fetchall() if r and r[0]]
    if not schemas:
        raise RuntimeError("NO_TABLE: no existe resumen_diario en la DB")

    if "public" in schemas:
        return "public"
    return schemas[0]


def _detect_date_column(cur, schema: str) -> str:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = 'resumen_diario'
        """,
        (schema,),
    )
    cols = {r[0] for r in cur.fetchall()}
    for c in _CANDIDATE_DATE_COLS:
        if c in cols:
            return c
    raise RuntimeError(f"NO_DATE_COL: resumen_diario sin columna fecha. cols={sorted(cols)}")


def _fetch_resumen_dates() -> Tuple[List[date], Optional[str]]:
    """
    Devuelve (fechas, motivo_fallback).
    - Si falla algo (DB missing, tabla, columna...), NO lanza 500: devolvemos fallback.
    """
    db = _db_url()
    if not db:
        return [], "NO_DB_URL"

    max_dates = int(os.getenv("SITEMAP_MAX_DATES", "0") or "0")  # 0 = sin límite
    timeout_ms = int(os.getenv("SITEMAP_DB_TIMEOUT_MS", "3000") or "3000")

    conn = None
    try:
        conn = psycopg2.connect(db, connect_timeout=5)
        with conn:
            with conn.cursor() as cur:
                # evita consultas colgadas si hay locks
                cur.execute(f"SET statement_timeout = {timeout_ms}")

                schema = _detect_resumen_diario_schema(cur)
                table_fq = f'"{schema}"."resumen_diario"'
                date_col = _detect_date_column(cur, schema)

                limit_sql = ""
                params: List[object] = []
                if max_dates > 0:
                    limit_sql = "LIMIT %s"
                    params.append(max_dates)

                sql = f"""
                    SELECT DISTINCT ({date_col})::date AS d
                    FROM {table_fq}
                    WHERE {date_col} IS NOT NULL
                    ORDER BY d DESC
                    {limit_sql}
                """
                cur.execute(sql, params)
                rows = cur.fetchall()

        out: List[date] = []
        for r in rows:
            if not r:
                continue
            d = r[0]
            if d is None:
                continue
            if isinstance(d, date):
                out.append(d)
            else:
                out.append(date.fromisoformat(str(d)))
        return out, None

    except Exception as e:
        log.exception("seo_sitemap: error obteniendo fechas de resumen_diario")
        # devolvemos fallback sin 500 (pero con header indicativo)
        return [], f"DB_ERROR:{type(e).__name__}"
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def _site_base() -> str:
    base = (
        os.getenv("SITE_URL")
        or os.getenv("PUBLIC_SITE_URL")
        or os.getenv("FRONTEND_URL")
        or "https://www.boetracker.com"
    )
    return str(base).rstrip("/")


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


@seo_bp.get("/resumen-dates")
def resumen_dates():
    dates, reason = _fetch_resumen_dates()
    payload = {"dates": [d.isoformat() for d in dates], "count": len(dates)}
    if reason:
        payload["fallback_reason"] = reason
    return jsonify(payload), 200


@seo_bp.get("/sitemap.xml")
def sitemap_xml():
    site = _site_base()

    dates, fallback_reason = _fetch_resumen_dates()

    # lastmod del índice /resumen: la última fecha publicada si existe
    lastmod_resumen = dates[0].isoformat() if dates else date.today().isoformat()

    lines: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f"  <url><loc>{_xml_escape(site + '/')}</loc></url>",
        f"  <url><loc>{_xml_escape(site + '/resumen')}</loc><lastmod>{lastmod_resumen}</lastmod></url>",
    ]

    for d in dates:
        iso = d.isoformat()
        loc = _xml_escape(site + "/resumen/" + iso)
        lines.append(f"  <url><loc>{loc}</loc><lastmod>{iso}</lastmod></url>")

    lines.append("</urlset>")
    xml = "\n".join(lines) + "\n"

    resp = Response(xml, mimetype="application/xml")
    resp.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
    resp.headers["X-Sitemap-Status"] = "FALLBACK" if fallback_reason else "OK"
    if fallback_reason:
        resp.headers["X-Sitemap-Fallback-Reason"] = fallback_reason[:120]
    resp.headers["X-Sitemap-Count"] = str(len(dates))
    return resp
