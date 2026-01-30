from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import List, Tuple

import psycopg2
from flask import Blueprint, Response, jsonify

seo_bp = Blueprint("seo_bp", __name__, url_prefix="/api/meta")
log = logging.getLogger(__name__)

# ✅ Solo indexamos /resumen y /resumen/YYYY-MM-DD
_STATIC_URLS = ["/resumen"]

# Defaults según tu DB actual
_DEFAULT_TABLE = "daily_section_summaries"
_DEFAULT_SCHEMA = "public"
_DEFAULT_DATE_COL = "fecha_publicacion"

# Columnas candidatas por compat (por si cambias el modelo)
_CANDIDATE_DATE_COLS = [
    "fecha_publicacion",
    "fecha",
    "day",
    "date",
    "published_date",
    "created_at",
]


# ───────────────────────── helpers ─────────────────────────

def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return (v.strip() if v else default).strip()


def _site_url() -> str:
    base = (
        os.getenv("SITE_URL")
        or os.getenv("PUBLIC_SITE_URL")
        or os.getenv("FRONTEND_URL")
        or "https://www.boetracker.com"
    ).strip()
    return base.rstrip("/")


def _db_url() -> str:
    # Railway / addons: intenta varias keys comunes
    for k in (
        "DATABASE_URL",
        "DATABASE_URL_PROD",
        "DATABASE_URL_READONLY",
        "POSTGRES_URL",
        "POSTGRESQL_URL",
        "RAILWAY_DATABASE_URL",
    ):
        v = os.getenv(k)
        if v and v.strip():
            return v.strip()
    return ""


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _sanitize_header_value(s: str, limit: int = 220) -> str:
    if not s:
        return ""
    x = str(s).strip().replace("\n", " ").replace("\r", " ")
    x = re.sub(r"\s+", " ", x)
    # Redacta URLs de postgres si por error apareciesen
    x = re.sub(r"(postgres(ql)?://)[^\s]+", r"\1***", x, flags=re.IGNORECASE)
    return x[:limit]


_ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _assert_ident(x: str, label: str) -> str:
    """
    Valida que schema/table/column sean identificadores simples (evita inyección vía env).
    """
    s = (x or "").strip()
    if not s or not _ident_re.match(s):
        raise RuntimeError(f"BAD_IDENT:{label}={s!r}")
    return s


def _table_exists(cur, schema: str, table: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_name = %s
          AND table_type IN ('BASE TABLE','VIEW')
        LIMIT 1
        """,
        (schema, table),
    )
    return cur.fetchone() is not None


def _detect_schema(cur, table: str) -> str:
    """
    - Usa SITEMAP_SCHEMA si existe
    - Si no, busca el schema donde existe la tabla (prefiere public)
    """
    schema_override = _env("SITEMAP_SCHEMA", "")
    if schema_override:
        return _assert_ident(schema_override, "schema")

    cur.execute(
        """
        SELECT table_schema
        FROM information_schema.tables
        WHERE table_name = %s
          AND table_type IN ('BASE TABLE','VIEW')
        ORDER BY (table_schema = 'public') DESC, table_schema ASC
        LIMIT 1
        """,
        (table,),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"NO_TABLE: no existe {table} (ni tabla ni vista) en la DB")
    return str(row[0])


def _detect_date_column(cur, schema: str, table: str) -> str:
    """
    - Usa SITEMAP_DATE_COL si existe y está presente.
    - Si no, intenta DEFAULT_DATE_COL y luego lista de candidatas.
    """
    forced = _env("SITEMAP_DATE_COL", "")
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        """,
        (schema, table),
    )
    cols = {r[0] for r in (cur.fetchall() or [])}

    if forced:
        forced = _assert_ident(forced, "date_col")
        if forced in cols:
            return forced
        raise RuntimeError(
            f"NO_DATE_COLUMN: SITEMAP_DATE_COL={forced} no existe en {schema}.{table}. "
            f"Columnas: {sorted(cols)}"
        )

    # preferimos tu columna real
    if _DEFAULT_DATE_COL in cols:
        return _DEFAULT_DATE_COL

    for c in _CANDIDATE_DATE_COLS:
        if c in cols:
            return c

    raise RuntimeError(
        f"NO_DATE_COLUMN: {schema}.{table} no tiene columna de fecha compatible. "
        f"Columnas: {sorted(cols)}"
    )


def _build_sitemap_xml(base: str, dates: List[date]) -> str:
    parts: List[str] = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')

    latest = dates[0].isoformat() if dates else None

    # /resumen
    for p in _STATIC_URLS:
        loc = _xml_escape(f"{base}{p}")
        parts.append("  <url>")
        parts.append(f"    <loc>{loc}</loc>")
        if latest:
            parts.append(f"    <lastmod>{latest}</lastmod>")
        parts.append("    <changefreq>daily</changefreq>")
        parts.append("    <priority>1.0</priority>")
        parts.append("  </url>")

    # /resumen/YYYY-MM-DD
    for d in dates:
        iso = d.isoformat()
        loc = _xml_escape(f"{base}/resumen/{iso}")
        parts.append("  <url>")
        parts.append(f"    <loc>{loc}</loc>")
        parts.append(f"    <lastmod>{iso}</lastmod>")
        parts.append("    <changefreq>daily</changefreq>")
        parts.append("    <priority>0.7</priority>")
        parts.append("  </url>")

    parts.append("</urlset>")
    return "\n".join(parts) + "\n"


def _resp_xml(xml: str, status: str, count: int, reason: str = "", debug: str = "") -> Response:
    resp = Response(xml, mimetype="application/xml; charset=utf-8")
    resp.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
    resp.headers["X-Sitemap-Status"] = status
    resp.headers["X-Sitemap-Count"] = str(int(count))
    if reason:
        resp.headers["X-Sitemap-Fallback-Reason"] = _sanitize_header_value(reason)
    if debug:
        resp.headers["X-Sitemap-Debug"] = _sanitize_header_value(debug, 180)
    return resp


def _fetch_dates() -> Tuple[List[date], str]:
    db = _db_url()
    if not db:
        raise RuntimeError("NO_DB_URL: DATABASE_URL (o equivalente) no está configurada")

    table = _env("SITEMAP_TABLE", _DEFAULT_TABLE) or _DEFAULT_TABLE
    table = _assert_ident(table, "table")

    conn = psycopg2.connect(db, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            # timeouts defensivos
            try:
                cur.execute("SET statement_timeout TO 4000")
            except Exception:
                pass

            # schema: override o autodetect
            schema = _env("SITEMAP_SCHEMA", "").strip()
            schema = _assert_ident(schema, "schema") if schema else _detect_schema(cur, table)

            if not _table_exists(cur, schema, table):
                # Si el override apunta mal, intentamos autodetect para salvar
                schema = _detect_schema(cur, table)

            date_col = _detect_date_column(cur, schema, table)

            schema_q = f'"{schema}"'
            table_q = f'"{table}"'
            date_col_q = f'"{date_col}"'

            # DISTINCT por día (hay muchas filas por sección)
            cur.execute(
                f"""
                SELECT DISTINCT ({date_col_q})::date AS d
                FROM {schema_q}.{table_q}
                WHERE {date_col_q} IS NOT NULL
                ORDER BY d DESC
                """
            )
            rows = cur.fetchall() or []
            dates = [r[0] for r in rows if r and r[0]]
            debug = f"table={schema}.{table} date_col={date_col}"
            return dates, debug
    finally:
        conn.close()


# ───────────────────────── routes ─────────────────────────

@seo_bp.get("/resumen-dates")
def resumen_dates():
    try:
        dates, _debug = _fetch_dates()
        return jsonify([d.isoformat() for d in dates])
    except Exception:
        log.exception("[seo_sitemap] resumen-dates failed")
        return jsonify([])


@seo_bp.get("/sitemap.xml")
def sitemap_xml():
    base = _site_url()
    try:
        dates, debug = _fetch_dates()
        xml = _build_sitemap_xml(base, dates)
        return _resp_xml(xml, status="OK", count=len(dates), debug=debug)
    except Exception as e:
        log.exception("[seo_sitemap] sitemap.xml failed")
        xml = _build_sitemap_xml(base, [])
        msg = str(e) or type(e).__name__
        return _resp_xml(xml, status="FALLBACK", count=0, reason=msg)
