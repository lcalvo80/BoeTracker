# app/scripts/refetch_missing_ai.py
from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta
from typing import Tuple

from app.services.postgres import get_db
from app.services.openai_service import get_openai_responses_from_pdf


def _configure_logging() -> None:
    """
    GitHub Actions a menudo no muestra INFO si no configuras logging explícitamente.
    Además, PYTHONUNBUFFERED=1 ayuda a que no haya buffering en CI.
    """
    level_name = (os.getenv("LOG_LEVEL") or "INFO").upper().strip()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RECOVERY_LIMIT = 200


def _today_yesterday() -> Tuple[date, date]:
    today = date.today()
    return today, today - timedelta(days=1)


def run() -> int:
    _configure_logging()

    today, yesterday = _today_yesterday()

    logger.info("Arrancando refetch_missing_ai")
    logger.info("Ventana principal: %s y %s", today, yesterday)
    logger.info("MAX_ATTEMPTS=%s RECOVERY_LIMIT=%s", MAX_ATTEMPTS, RECOVERY_LIMIT)

    processed = 0
    done = 0
    failed = 0

    with get_db() as conn:
        with conn.cursor() as cur:
            # ─────────────────────────────────────────────
            # 1) Ventana principal: HOY + AYER
            # ─────────────────────────────────────────────
            cur.execute(
                """
                SELECT id, identificador, titulo, url_pdf
                FROM items
                WHERE
                  COALESCE(fecha_publicacion, created_at::date) IN (%s, %s)
                  AND ai_status IN ('pending', 'failed')
                  AND COALESCE(ai_attempts, 0) < %s
                ORDER BY id
                """,
                (today, yesterday, MAX_ATTEMPTS),
            )
            rows = cur.fetchall()
            logger.info("IA principal: candidatos=%s", len(rows))

            for row in rows:
                processed += 1
                ok = _process_item(cur, row)
                if ok:
                    done += 1
                else:
                    failed += 1

            # ─────────────────────────────────────────────
            # 2) Recuperación controlada (antiguos)
            # ─────────────────────────────────────────────
            cur.execute(
                """
                SELECT id, identificador, titulo, url_pdf
                FROM items
                WHERE
                  ai_status IN ('pending', 'failed')
                  AND COALESCE(ai_attempts, 0) < %s
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
            logger.info("IA recuperación: candidatos=%s", len(rows))

            for row in rows:
                processed += 1
                ok = _process_item(cur, row)
                if ok:
                    done += 1
                else:
                    failed += 1

        conn.commit()

    logger.info("Fin refetch_missing_ai: processed=%s done=%s failed=%s", processed, done, failed)

    # Si quieres que el workflow sea rojo cuando haya fallos: FAIL_ON_ERRORS=1
    fail_on_errors = (os.getenv("FAIL_ON_ERRORS") or "0").strip() == "1"
    if fail_on_errors and failed > 0:
        logger.error("FAIL_ON_ERRORS=1 y hubo fallos. Saliendo con código 1.")
        return 1

    return 0


def _process_item(cur, row) -> bool:
    item_id, identificador, titulo, url_pdf = row

    if not url_pdf:
        msg = "url_pdf vacío o NULL"
        logger.error("IA falló %s: %s", identificador, msg)
        cur.execute(
            """
            UPDATE items
            SET
              ai_status = 'failed',
              ai_last_error = %s,
              ai_updated_at = NOW()
            WHERE id = %s
            """,
            (msg, item_id),
        )
        return False

    logger.info("Procesando IA %s", identificador)

    try:
        # Marca como processing + incrementa intentos
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

        # Pipeline PDF-first (tu service se encarga de descargar/parsear PDF y pedir a OpenAI)
        titulo_r, resumen_json, impacto_json = get_openai_responses_from_pdf(
            identificador=identificador,
            titulo=titulo,
            url_pdf=url_pdf,
        )

        # Persiste resultados
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

        logger.info("IA OK %s", identificador)
        return True

    except Exception as e:
        logger.exception("IA falló %s", identificador)
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
        return False


if __name__ == "__main__":
    sys.exit(run())
