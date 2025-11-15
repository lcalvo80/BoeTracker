# app/scripts/refetch_missing_ai.py
from __future__ import annotations

import logging
import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

from app.services.openai_service import get_openai_responses_from_pdf
from app.utils.compression import compress_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_URL = os.getenv("DATABASE_URL")


def _emptyish(x) -> bool:
    """
    Considera vacío: None, "", "{}", "[]", listas/dicts vacíos.
    """
    if x is None:
        return True
    if isinstance(x, str):
        s = x.strip()
        return len(s) == 0 or s in ("{}", "[]")
    if isinstance(x, (list, dict)):
        return len(x) == 0
    return False


@contextmanager
def db():
    if not DB_URL:
        raise RuntimeError("DATABASE_URL no configurada en el entorno.")
    conn = psycopg2.connect(DB_URL)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def needs_ai_row(row: dict) -> bool:
    """
    Devuelve True si el registro necesita IA
    (resumen o informe_impacto nulos).
    """
    return not row.get("resumen") or not row.get("informe_impacto")


def main(limit: int = 200) -> None:
    """
    Reprocesa hasta `limit` items sin IA usando SIEMPRE el PDF del BOE.
    """
    if not DB_URL:
        logging.error("❌ DATABASE_URL no definida. Revisa el secret en GitHub / Railway.")
        return

    q = """
    SELECT identificador, titulo, url_pdf, resumen, informe_impacto
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
                logging.info("✅ No hay pendientes de IA.")
                return

            logging.info(f"🔁 Reintentando IA para {len(rows)} elementos…")
            updated = 0

            for r in rows:
                ident = r["identificador"]
                title = r["titulo"] or ""
                url_pdf = (r.get("url_pdf") or "").strip()

                if not needs_ai_row(r):
                    logging.info(f"⏭️ {ident} ya tiene IA completa. (inconsistencia de query)")
                    continue

                if not url_pdf:
                    logging.warning(
                        "⚠️ Item %s sin url_pdf. No se puede reprocesar con PDF; se omite.",
                        ident,
                    )
                    continue

                try:
                    # Usa SIEMPRE el texto del PDF del BOE
                    titulo_res, resumen_json, impacto_json = get_openai_responses_from_pdf(
                        identificador=ident,
                        titulo=title,
                        url_pdf=url_pdf,
                    )
                except Exception as e:
                    logging.error("❌ OpenAI falló en %s: %s", ident, e)
                    continue

                resumen_comp = None if _emptyish(resumen_json) else compress_json(resumen_json)
                impacto_comp = None if _emptyish(impacto_json) else compress_json(impacto_json)

                cur.execute(
                    """
                    UPDATE items
                    SET titulo_resumen = COALESCE(NULLIF(%s, ''), titulo_resumen),
                        resumen = %s,
                        informe_impacto = %s
                    WHERE identificador = %s
                    """,
                    (titulo_res, resumen_comp, impacto_comp, ident),
                )
                updated += 1

            conn.commit()
            logging.info(f"✅ Actualizados {updated} registros con IA.")


if __name__ == "__main__":
    main()
