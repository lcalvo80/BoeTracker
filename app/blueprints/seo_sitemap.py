# app/blueprints/seo_sitemap.py
from __future__ import annotations

import os
from datetime import date
from typing import List

import psycopg2
from flask import Blueprint, Response, jsonify

seo_bp = Blueprint("seo_bp", __name__, url_prefix="/api/meta")

# Detectamos la columna de fecha en resumen_diario (por compat)
_CANDIDATE_DATE_COLS = [
    "fecha_publicacion",
    "fecha",
    "day",
    "date",
    "published_date",
    "created_at",
]

# ✅ SOLO indexamos /resumen y /resumen/YYYY-MM-DD
_STATIC_URLS = ["/resumen"]


def _site_url() -> str:
    base = (os.getenv("SITE_URL") or os.getenv("PUBLIC_SITE_URL") or "https://www.boetracker.com").strip()
    return base.rstrip("/")


def _db_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def _detect_date_column(cur) -> str:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'resumen_diario'
        """
    )
    cols = {r[0] for r in cur.fetchall()}
    for c in _CANDIDATE_DATE_COLS:
        if c in cols:
            return c
    raise RuntimeError(
        f"No se encontró columna de fecha en resumen_diario. Columnas detectadas: {sorted(cols)}"
    )


def _fetch_resumen_dates() -> List[date]:
    db = _db_url()
    if not db:
        raise RuntimeError("DATABASE_URL no configurada")

    conn = psycopg2.connect(db, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            date_col = _detect_date_column(cur)
            cur.execute(
                f"""
                SELECT DISTINCT ({date_col})::date AS d
                FROM resumen_diario
                WHERE {date_col} IS NOT NULL
                ORDER BY d DESC
                """
            )
            rows = cur.fetchall()
            return [r[0] for r in rows if r and r[0]]
    finally:
        conn.close()


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
    # Endpoint opcional (debug/inspección)
    dates = _fetch_resumen_dates()
    return jsonify([d.isoformat() for d in dates])


@seo_bp.get("/sitemap.xml")
def sitemap_xml():
    base = _site_url()
    dates = _fetch_resumen_dates()

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')

    # /resumen
    latest = dates[0].isoformat() if dates else None
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
    xml = "\n".join(parts) + "\n"

    resp = Response(xml, mimetype="application/xml; charset=utf-8")
    resp.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
    return resp
