#!/usr/bin/env python
from __future__ import annotations
import os, json, logging
from contextlib import contextmanager
import psycopg2
from psycopg2.extras import RealDictCursor

from app.services.html_enricher import enrich_boe_text
from app.services.openai_service import get_openai_responses

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_URL = os.getenv("DATABASE_URL")

@contextmanager
def db():
    conn = psycopg2.connect(DB_URL)
    try:
        yield conn
    finally:
        conn.close()

def needs_ai_row(row):
    return not row["resumen"] or not row["informe_impacto"]

def main(limit:int=200):
    q = """
    SELECT identificador, titulo, url_pdf, url_html, url_xml, resumen, informe_impacto
    FROM items
    WHERE (resumen IS NULL OR informe_impacto IS NULL)
    ORDER BY fecha_publicacion DESC NULLS LAST
    LIMIT %s
    """
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(q, (limit,))
            rows = cur.fetchall()
            if not rows:
                logging.info("✅ No hay pendientes.")
                return
            logging.info(f"🔁 Reintentando {len(rows)} elementos…")
            updated = 0
            for r in rows:
                ident = r["identificador"]
                title = r["titulo"] or ""

                # Usa título/urls existentes; el cuerpo base será el propio titulo (mínimo) si no hay otra cosa
                base_text = title
                text, enriched = enrich_boe_text(
                    identificador=ident,
                    url_html=r["url_html"],
                    url_txt_candidate=r["url_html"],  # ignorado internamente
                    url_pdf=r["url_pdf"],
                    base_text=base_text,
                    min_gain_chars=400,  # más laxo en reintento
                )
                try:
                    t_res, resumen_json, impacto_json = get_openai_responses(title, text or base_text)
                except Exception as e:
                    logging.error(f"OpenAI falló en {ident}: {e}")
                    continue

                cur.execute(
                    """
                    UPDATE items
                    SET titulo_resumen = COALESCE(NULLIF(%s,''), titulo_resumen),
                        resumen = %s,
                        informe_impacto = %s
                    WHERE identificador = %s
                    """,
                    (t_res, resumen_json, impacto_json, ident),
                )
                updated += 1
            conn.commit()
            logging.info(f"✅ Actualizados {updated} registros.")

if __name__ == "__main__":
    main()
