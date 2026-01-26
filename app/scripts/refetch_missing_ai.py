# app/scripts/refetch_missing_ai.py
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, timedelta
from typing import Any, List, Optional, Tuple

from psycopg2 import InterfaceError, OperationalError

from app.services.postgres import get_db
from app.services.openai_service import get_openai_responses_from_pdf_with_taxonomy as get_openai_responses_from_pdf


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

# Considera "processing" huérfano si lleva más de X horas.
PROCESSING_STALE_HOURS = int((os.getenv("PROCESSING_STALE_HOURS") or "2").strip())

# Taxonomía actual
TAXONOMY_VERSION = 1


def _truthy_env(name: str, default: str = "0") -> bool:
    return (os.getenv(name) or default).strip() == "1"


def _today_yesterday() -> Tuple[date, date]:
    today = date.today()
    return today, today - timedelta(days=1)


def _db_write_with_retry(fn, *, attempts: int = 3, base_sleep: float = 0.5):
    last_err = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except (OperationalError, InterfaceError) as e:
            last_err = e
            sleep_s = base_sleep * (2 ** (i - 1))
            logger.warning(
                "DB write falló (intento %s/%s): %s. Reintentando en %.1fs",
                i,
                attempts,
                e,
                sleep_s,
            )
            time.sleep(sleep_s)
    raise last_err  # type: ignore[misc]


def _safe_parse_json(s: str) -> Optional[Any]:
    if not s:
        return None
    ss = s.strip()
    if not (ss.startswith("{") or ss.startswith("[")):
        return None
    try:
        return json.loads(ss)
    except Exception:
        return None


def _sanitize_str(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    v = str(s).strip()
    return v or None


def _sanitize_category_l2(v: Any, *, max_items: int = 5) -> Optional[List[str]]:
    if v is None:
        return None

    items: List[str] = []
    if isinstance(v, (list, tuple)):
        for x in v:
            sx = _sanitize_str(x if isinstance(x, str) else str(x))
            if sx:
                items.append(sx)
    elif isinstance(v, str):
        # Permite strings tipo "A;B;C" o "A,B,C" como fallback
        raw = v.replace(";", ",")
        for part in raw.split(","):
            sx = _sanitize_str(part)
            if sx:
                items.append(sx)
    else:
        sx = _sanitize_str(str(v))
        if sx:
            items.append(sx)

    # Dedup preservando orden
    seen = set()
    out: List[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
        if len(out) >= max_items:
            break

    return out or None


def _extract_taxonomy_from_resumen_json(resumen_json: str) -> Tuple[Optional[str], Optional[List[str]]]:
    """
    Extrae category_l1/category_l2 si el resumen JSON las incluye.
    """
    parsed = _safe_parse_json(resumen_json)
    if not isinstance(parsed, dict):
        return None, None

    cat_l1 = _sanitize_str(parsed.get("category_l1"))
    cat_l2 = _sanitize_category_l2(parsed.get("category_l2"))
    return cat_l1, cat_l2


# Candidate tuple:
# (id, identificador, titulo, url_pdf, ai_status, titulo_resumen, category_l1, ai_taxonomy_version)
def _fetch_candidates() -> List[tuple]:
    """
    Devuelve candidatos deduplicados:
    - Principal: hoy+ayer en pending/failed (y done si necesita backfill de taxonomía o FORCE_TAXONOMY_REBUILD)
    - Recuperación: pending/failed antiguos (y done si necesita backfill), limit RECOVERY_LIMIT
    - Recuperación extra: processing "stale" (huérfanos) con last_attempt_at viejo
    """
    today, yesterday = _today_yesterday()
    force_taxonomy = _truthy_env("FORCE_TAXONOMY_REBUILD", "0")

    with get_db() as conn:
        with conn.cursor() as cur:
            # Principal: hoy+ayer (incluye done si necesita backfill de taxonomía o force)
            cur.execute(
                """
                SELECT id, identificador, titulo, url_pdf, ai_status, titulo_resumen, category_l1, ai_taxonomy_version
                FROM items
                WHERE
                  COALESCE(fecha_publicacion, created_at::date) IN (%s, %s)
                  AND (
                    (
                      ai_status IN ('pending', 'failed')
                      AND COALESCE(ai_attempts, 0) < %s
                    )
                    OR
                    (
                      ai_status = 'done'
                      AND (
                        %s = TRUE
                        OR titulo_resumen IS NULL
                        OR category_l1 IS NULL
                        OR ai_taxonomy_version IS DISTINCT FROM %s
                      )
                    )
                  )
                  AND NOT (
                    %s = FALSE
                    AND ai_status = 'done'
                    AND titulo_resumen IS NOT NULL
                    AND category_l1 IS NOT NULL
                    AND ai_taxonomy_version = %s
                  )
                ORDER BY id
                """,
                (today, yesterday, MAX_ATTEMPTS, force_taxonomy, TAXONOMY_VERSION, force_taxonomy, TAXONOMY_VERSION),
            )
            main_rows = cur.fetchall()

            # Recuperación: pending/failed antiguos (y done si necesita backfill), limit
            cur.execute(
                """
                SELECT id, identificador, titulo, url_pdf, ai_status, titulo_resumen, category_l1, ai_taxonomy_version
                FROM items
                WHERE
                  (
                    (
                      ai_status IN ('pending', 'failed')
                      AND COALESCE(ai_attempts, 0) < %s
                      AND (
                        ai_last_attempt_at IS NULL
                        OR ai_last_attempt_at < NOW() - INTERVAL '6 hours'
                      )
                    )
                    OR
                    (
                      ai_status = 'done'
                      AND (
                        %s = TRUE
                        OR titulo_resumen IS NULL
                        OR category_l1 IS NULL
                        OR ai_taxonomy_version IS DISTINCT FROM %s
                      )
                      AND (
                        ai_last_attempt_at IS NULL
                        OR ai_last_attempt_at < NOW() - INTERVAL '6 hours'
                      )
                    )
                  )
                  AND NOT (
                    %s = FALSE
                    AND ai_status = 'done'
                    AND titulo_resumen IS NOT NULL
                    AND category_l1 IS NOT NULL
                    AND ai_taxonomy_version = %s
                  )
                ORDER BY ai_last_attempt_at NULLS FIRST
                LIMIT %s
                """,
                (MAX_ATTEMPTS, force_taxonomy, TAXONOMY_VERSION, force_taxonomy, TAXONOMY_VERSION, RECOVERY_LIMIT),
            )
            recovery_rows = cur.fetchall()

            # Recuperación: processing huérfanos
            cur.execute(
                """
                SELECT id, identificador, titulo, url_pdf, ai_status, titulo_resumen, category_l1, ai_taxonomy_version
                FROM items
                WHERE
                  ai_status = 'processing'
                  AND COALESCE(ai_attempts, 0) < %s
                  AND ai_last_attempt_at < NOW() - (%s || ' hours')::interval
                ORDER BY ai_last_attempt_at ASC
                """,
                (MAX_ATTEMPTS, PROCESSING_STALE_HOURS),
            )
            stale_processing_rows = cur.fetchall()

    seen = set()
    out: List[tuple] = []
    for r in (main_rows + recovery_rows + stale_processing_rows):
        if r[0] in seen:
            continue
        seen.add(r[0])
        out.append(r)

    logger.info(
        "Candidatos: principal=%s recuperación=%s processing_stale=%s total_dedup=%s (FORCE_TAXONOMY_REBUILD=%s)",
        len(main_rows),
        len(recovery_rows),
        len(stale_processing_rows),
        len(out),
        "1" if force_taxonomy else "0",
    )
    return out


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


def _mark_done(
    item_id: int,
    titulo_r: Optional[str],
    resumen_json: str,
    impacto_json: str,
    category_l1: Optional[str],
    category_l2: Optional[List[str]],
    ai_taxonomy_version: Optional[int],
    *,
    force_overwrite_taxonomy: bool,
) -> None:
    """
    Guarda IA + taxonomía.
    - Si force_overwrite_taxonomy=True, sobreescribe taxonomía/título con lo calculado.
    - Si False, NO pisa valores existentes si le pasamos None (por eso usamos COALESCE en SQL).
    """
    def _op():
        with get_db() as conn:
            with conn.cursor() as cur:
                if force_overwrite_taxonomy:
                    cur.execute(
                        """
                        UPDATE items
                        SET
                          titulo_resumen = %s,
                          resumen = %s,
                          informe_impacto = %s,
                          category_l1 = %s,
                          category_l2 = %s,
                          ai_taxonomy_version = %s,
                          ai_status = 'done',
                          ai_done_at = NOW(),
                          ai_updated_at = NOW(),
                          ai_last_error = NULL
                        WHERE id = %s
                        """,
                        (titulo_r, resumen_json, impacto_json, category_l1, category_l2, ai_taxonomy_version, item_id),
                    )
                else:
                    # Solo rellena si viene valor (si pasamos None, mantiene el existente)
                    cur.execute(
                        """
                        UPDATE items
                        SET
                          titulo_resumen = COALESCE(%s, titulo_resumen),
                          resumen = %s,
                          informe_impacto = %s,
                          category_l1 = COALESCE(%s, category_l1),
                          category_l2 = COALESCE(%s, category_l2),
                          ai_taxonomy_version = COALESCE(%s, ai_taxonomy_version),
                          ai_status = 'done',
                          ai_done_at = COALESCE(ai_done_at, NOW()),
                          ai_updated_at = NOW(),
                          ai_last_error = NULL
                        WHERE id = %s
                        """,
                        (titulo_r, resumen_json, impacto_json, category_l1, category_l2, ai_taxonomy_version, item_id),
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

    force_taxonomy = _truthy_env("FORCE_TAXONOMY_REBUILD", "0")

    today, yesterday = _today_yesterday()
    logger.info("Arrancando refetch_missing_ai")
    logger.info("Ventana principal: %s y %s", today, yesterday)
    logger.info(
        "MAX_ATTEMPTS=%s RECOVERY_LIMIT=%s PROCESSING_STALE_HOURS=%s TAXONOMY_VERSION=%s FORCE_TAXONOMY_REBUILD=%s",
        MAX_ATTEMPTS,
        RECOVERY_LIMIT,
        PROCESSING_STALE_HOURS,
        TAXONOMY_VERSION,
        "1" if force_taxonomy else "0",
    )

    processed = 0
    done = 0
    failed = 0

    candidates = _fetch_candidates()
    logger.info("Procesando lote: %s items (sin límite)", len(candidates))

    for (item_id, identificador, titulo, url_pdf, ai_status, titulo_exist, cat_l1_exist, tax_ver_exist) in candidates:
        processed += 1
        logger.info("Procesando IA %s (id=%s, status=%s)", identificador, item_id, ai_status)

        # Guardia final (por seguridad): si ya está completo y no forzamos, skip
        if (
            not force_taxonomy
            and ai_status == "done"
            and titulo_exist
            and cat_l1_exist
            and (tax_ver_exist == TAXONOMY_VERSION)
        ):
            logger.info("Skip %s: ya tiene titulo+taxonomía v%s", identificador, TAXONOMY_VERSION)
            continue

        if not url_pdf:
            failed += 1
            _mark_failed(item_id, "url_pdf vacío o NULL")
            continue

        try:
            _mark_processing(item_id)

            resp = get_openai_responses_from_pdf(
                identificador=identificador,
                titulo=titulo,
                url_pdf=url_pdf,
            )

            # Compatibilidad: resp puede ser (titulo, resumen, impacto) o (titulo, resumen, impacto, cat_l1, cat_l2)
            titulo_r: Optional[str]
            resumen_json: str
            impacto_json: str
            cat_l1_new: Optional[str] = None
            cat_l2_new: Optional[List[str]] = None

            if isinstance(resp, tuple) and len(resp) >= 3:
                titulo_r = _sanitize_str(resp[0])
                resumen_json = str(resp[1] or "")
                impacto_json = str(resp[2] or "")
                if len(resp) >= 5:
                    cat_l1_new = _sanitize_str(resp[3])
                    cat_l2_new = _sanitize_category_l2(resp[4])
                else:
                    # Alternativa: extraer del resumen_json si viene embebido
                    cat_l1_new, cat_l2_new = _extract_taxonomy_from_resumen_json(resumen_json)
            else:
                raise RuntimeError("get_openai_responses_from_pdf() devolvió un formato inesperado")

            # Decide qué guardar para NO pisar title/tags si ya existen (y no forzamos)
            if not force_taxonomy and titulo_exist:
                titulo_to_save = None
            else:
                titulo_to_save = titulo_r

            # Si ya hay taxonomía v1, no la pises (salvo force). Si versión distinta o NULL, sí actualiza.
            if not force_taxonomy and cat_l1_exist and (tax_ver_exist == TAXONOMY_VERSION):
                cat_l1_to_save = None
                cat_l2_to_save = None
                tax_ver_to_save = None
            else:
                cat_l1_to_save = cat_l1_new
                cat_l2_to_save = cat_l2_new
                tax_ver_to_save = TAXONOMY_VERSION if cat_l1_new else None

            _mark_done(
                item_id,
                titulo_to_save,
                resumen_json,
                impacto_json,
                cat_l1_to_save,
                cat_l2_to_save,
                tax_ver_to_save,
                force_overwrite_taxonomy=force_taxonomy,
            )
            done += 1
            logger.info("IA OK %s", identificador)

        except Exception as e:
            failed += 1
            logger.exception("IA falló %s", identificador)
            _mark_failed(item_id, str(e))

    logger.info("Fin refetch_missing_ai: processed=%s done=%s failed=%s", processed, done, failed)

    fail_on_errors = (os.getenv("FAIL_ON_ERRORS") or "0").strip() == "1"
    if fail_on_errors and failed > 0:
        logger.error("FAIL_ON_ERRORS=1 y hubo fallos. Exit code 1.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(run())
