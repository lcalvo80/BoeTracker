# app/scripts/refetch_missing_ai.py
from __future__ import annotations

import logging
from datetime import date, timedelta

from app.services.postgres import get_db
from app.services.openai_service import get_openai_responses_from_pdf

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RECOVERY_LIMIT = 200


def _today_yesterday():
    today = date.today()
    return today, today - timedelta(days=1)


def run():
    today, yesterday = _today_yesterday()

    with get_db() as conn:
        with conn.cursor() as cur:

            # ─────────────────────────────────────────────
            # 1️⃣ Ventana principal: HOY + AYER
            # ─────────────────────────────────────────────
            cur.execute(
                """
                SELECT id, identificador, titulo, url_pdf
                FROM items
                WHERE
                  COALESCE(fecha_publicacion, created_at::date) IN (%s, %s)
                  AND ai_status IN ('pending', 'failed')
                  AND ai_attempts < %s
                ORDER BY id
                """,
                (today, yesterday, MAX_ATTEMPTS),
            )
            rows = cur.fetchall()
            logger.info("IA principal: %s items", len(rows))

            for row in rows:
                _process_item(cur, row)

            # ─────────────────────────────────────────────
            # 2️⃣ Recuperación controlada
            # ─────────────────────────────────────────────
            cur.execute(
                """
                SELECT id, identificador, titulo, url_pdf
                FROM items
                WHERE
                  ai_status IN ('pending', 'failed')
                  AND ai_attempts < %s
                  AND (
                    ai_last_attempt_at IS NULL
                    OR ai_last_attempt_at < NOW() - INTERVAL '6 hours'
                  )
                ORDER BY ai_last_attempt_at NULLS FIRST
                LIMIT %s
                """,
                (MAX_ATTEMPTS, RECOVERY_LIMIT),
            )
            rows = cur.fetchall()
            logger.info("IA recuperación: %s items", len(rows))

            for row in rows:
                _process_item(cur, row)

        conn.commit()


def _process_item(cur, row):
    item_id, identificador, titulo, url_pdf = row
    logger.info("Procesando IA %s", identificador)

    try:
        cur.execute(
            """
            UPDATE items
            SET
              ai_status = 'processing',
              ai_attempts = COALESCE(ai_attempts, 0) + 1,
              ai_last_attempt_at = NOW(),
              ai_updated_at = NOW()
            WHERE id = %s
            """,
            (item_id,),
        )

        titulo_r, resumen_json, impacto_json = get_openai_responses_from_pdf(
            identificador=identificador,
            titulo=titulo,
            url_pdf=url_pdf,
        )

        cur.execute(
            """
            UPDATE items
            SET
              titulo_resumen = %s,
              resumen = %s,
              informe_impacto = %s,
              ai_status = 'done',
              ai_done_at = NOW(),
              ai_updated_at = NOW(),
              ai_last_error = NULL
            WHERE id = %s
            """,
            (titulo_r, resumen_json, impacto_json, item_id),
        )

    except Exception as e:
        logger.error("IA falló %s: %s", identificador, e)
        cur.execute(
            """
            UPDATE items
            SET
              ai_status = 'failed',
              ai_last_error = %s,
              ai_updated_at = NOW()
            WHERE id = %s
            """,
            (str(e), item_id),
        )
