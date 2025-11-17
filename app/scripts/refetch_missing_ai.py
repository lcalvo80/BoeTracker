# app/scripts/refetch_missing_ai.py
from __future__ import annotations

import logging
import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

from app.services.openai_service import (
    get_openai_responses,
    get_openai_responses_from_pdf,
)
from app.utils.compression import compress_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_URL")


@contextmanager
def db():
    conn = psycopg2.connect(DB_URL)
    try:
        yield conn
    finally:
        conn.close()


def _emptyish(x) -> bool:
    if x is None:
        return True
    if isinstance(x, str):
        s = x.strip()
        return len(s) == 0 or s in ("{}", "[]")
    if isinstance(x, (list, dict)):
        return len(x) == 0
    return False


def main(limit: int = 200):
    """
    Reprocesa items sin IA (resumen o informe_impacto nulos) usando, siempre que se pueda, el PDF.
    """
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
                logger.info("✅ No hay pendientes.")
                return

            logger.info(f"🔁 Reintentando {len(rows)} elementos…")
            updated = 0

            for r in rows:
                ident = r["identificador"]
                title = r["titulo"] or ""
                url_pdf = (r.get("url_pdf") or "").strip()
                url_html = (r.get("url_html") or "").strip()

                try:
                    if url_pdf:
                        # Camino principal: PDF
                        t_res, resumen_json, impacto_json = get_openai_responses_from_pdf(
                            identificador=ident,
                            titulo=title,
                            url_pdf=url_pdf,
                        )
                    else:
                        # Fallback: si no hay PDF, al menos usamos algo de texto base
                        base_text = title
                        logger.warning(
                            "⚠️ %s sin url_pdf en refetch_missing_ai. Uso título/base_text.",
                            ident,
                        )
                        t_res, resumen_json, impacto_json = get_openai_responses(
                            title, base_text
                        )
                except Exception as e:
                    logger.error(f"❌ OpenAI falló en {ident}: {e}")
                    continue

                resumen_comp = None if _emptyish(resumen_json) else compress_json(resumen_json)
                impacto_comp = None if _emptyish(impacto_json) else compress_json(impacto_json)

                cur.execute(
                    """
                    UPDATE items
                    SET
                        titulo_resumen = COALESCE(NULLIF(%s, ''), titulo_resumen),
                        resumen = %s,
                        informe_impacto = %s
                    WHERE identificador = %s
                    """,
                    (t_res, resumen_comp, impacto_comp, ident),
                )
                updated += 1

            conn.commit()
            logger.info(f"✅ Actualizados {updated} registros.")


if __name__ == "__main__":
    main()
