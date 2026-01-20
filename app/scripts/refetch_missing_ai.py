# app/scripts/refetch_missing_ai.py
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import date, timedelta
from typing import List, Tuple

import psycopg2
from psycopg2 import OperationalError, InterfaceError

from app.services.postgres import get_db
from app.services.openai_service import get_openai_responses_from_pdf


def _configure_logging() -> None:
    level_name = (os.getenv("LOG_LEVEL") or "INFO").upper().strip()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RECOVERY_LIMIT = 200

# Para no intentar procesar 540 items en una única corrida (muy caro y largo)
# Puedes ajustar en Actions con AI_BATCH_LIMIT=30 por ejemplo.
AI_BATCH_LIMIT = int((os.getenv("AI_BATCH_LIMIT") or "50").strip())


def _today_yesterday() -> Tuple[date, date]:
    today = date.today()
    return today, today - timedelta(days=1)


def _fetch_candidates() -> List[tuple]:
    """
    Lee candidatos en una conexión corta (solo SELECT).
    Devolvemos: [(id, identificador, titulo, url_pdf), ...]
    """
    today, yesterday = _today_yesterday()

    with get_db() as conn:
        with conn.cursor() as cur:
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
            main_rows = cur.fetchall()

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
            recovery_rows = cur.fetchall()

    # Junta (principal primero), dedup por id
    seen = set()
    out: List[tuple] = []
    for r in (main_rows + recovery_rows):
        if r[0] in seen:
            continue
        seen.add(r[0])
        out.append(r)

    if AI_BATCH_LIMIT > 0:
        out = out[:AI_BATCH_LIMIT]

    logger.info("Candidatos: principal=%s recuperación=%s total_dedup=%s batch_limit=%s",
                len(main_rows), len(recovery_rows), len(out), AI_BATCH_LIMIT)
    return out


def _db_write_with_retry(fn, *, attempts: int = 3, base_sleep: float = 0.5):
    """
    Reintenta escrituras DB ante fallos transitorios (SSL drop, etc.).
    """
    last_err = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except (OperationalError, InterfaceError) as e:
            last_err = e
            sleep_s = base_sleep * (2 ** (i - 1))
            logger.warning("DB write falló (intento %s/%s): %s. Reintentando en %.1fs",
                           i, attempts, e, sleep_s)
            time.sleep(sleep_s)
    raise last_err  # type: ignore[misc]


def _mark_processing(item_id: int) -> None:
    def _op():
        with get_db() as conn:
            with conn.cursor() as cur:
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
            conn.commit()

    _db_write_with_retry(_op)


def _mark_done(item_id: int, titulo_r: str, resumen_json: str, impacto_json: str) -> None:
    def _op():
        with get_db() as conn:
            with conn.cursor() as cur:
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
            conn.commit()

    _db_write_with_retry(_op)


def _mark_failed(item_id: int, err: str) -> None:
    def _op():
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE items
                    SET
                      ai_status = 'failed',
                      ai_last_error = %s,
                      ai_updated_at = NOW()
                    WHERE id = %s
                    """,
                    (err, item_id),
                )
            conn.commit()

    _db_write_with_retry(_op)


def run() -> int:
    _configure_logging()

    today, yesterday = _today_yesterday()
    logger.info("Arrancando refetch_missing_ai")
    logger.info("Ventana principal: %s y %s", today, yesterday)
    logger.info("MAX_ATTEMPTS=%s RECOVERY_LIMIT=%s AI_BATCH_LIMIT=%s", MAX_ATTEMPTS, RECOVERY_LIMIT, AI_BATCH_LIMIT)

    processed = 0
    done = 0
    failed = 0

    candidates = _fetch_candidates()
    logger.info("Procesando lote: %s items", len(candidates))

    for (item_id, identificador, titulo, url_pdf) in candidates:
        processed += 1
        logger.info("Procesando IA %s (id=%s)", identificador, item_id)

        if not url_pdf:
            failed += 1
            _mark_failed(item_id, "url_pdf vacío o NULL")
            continue

        try:
            # 1) DB: marcar processing (con commit) en conexión corta
            _mark_processing(item_id)

            # 2) OpenAI (sin DB abierta)
            titulo_r, resumen_json, impacto_json = get_openai_responses_from_pdf(
                identificador=identificador,
                titulo=titulo,
                url_pdf=url_pdf,
            )

            # 3) DB: persistir done (con commit) en conexión corta
            _mark_done(item_id, titulo_r, resumen_json, impacto_json)
            done += 1
            logger.info("IA OK %s", identificador)

        except Exception as e:
            failed += 1
            logger.exception("IA falló %s", identificador)
            # Importante: incluso si DB “tiembla”, _mark_failed reintenta.
            _mark_failed(item_id, str(e))

    logger.info("Fin refetch_missing_ai: processed=%s done=%s failed=%s", processed, done, failed)

    fail_on_errors = (os.getenv("FAIL_ON_ERRORS") or "0").strip() == "1"
    if fail_on_errors and failed > 0:
        logger.error("FAIL_ON_ERRORS=1 y hubo fallos. Exit code 1.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(run())
