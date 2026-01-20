# app/scripts/refetch_missing_ai.py
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.services.postgres import get_db
from app.services.openai_service import get_openai_responses_from_pdf
from app.utils.compression import compress_json

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ───────────────── Config ─────────────────
AI_MAX_ATTEMPTS = int(os.getenv("AI_MAX_ATTEMPTS", "3") or "3")
AI_RECOVERY_LIMIT = int(os.getenv("AI_RECOVERY_LIMIT", "200") or "200")
AI_RECOVERY_COOLDOWN_HOURS = int(os.getenv("AI_RECOVERY_COOLDOWN_HOURS", "6") or "6")


@dataclass
class ItemRow:
    id: int
    identificador: str
    titulo: str
    url_pdf: str
    fecha_publicacion: Optional[date]
    created_at_date: date
    ai_status: str
    ai_attempts: int


def _ensure_ai_columns(cur) -> None:
    """
    Migración defensiva: crea columnas ai_* si no existen.
    Ajusta tipos a algo robusto para Postgres.
    """
    cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_status TEXT")
    cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_attempts INTEGER")
    cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_last_error TEXT")
    cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_last_attempt_at TIMESTAMP WITHOUT TIME ZONE")
    cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_done_at TIMESTAMP WITHOUT TIME ZONE")
    cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_updated_at TIMESTAMP WITHOUT TIME ZONE")


def _normalize_ai_defaults(cur) -> None:
    """
    Asegura defaults lógicos (sin depender de schema).
    """
    cur.execute(
        """
        UPDATE items
        SET
          ai_status = COALESCE(ai_status, CASE
            WHEN resumen IS NOT NULL AND informe_impacto IS NOT NULL THEN 'done'
            ELSE 'pending'
          END),
          ai_attempts = COALESCE(ai_attempts, 0)
        WHERE ai_status IS NULL OR ai_attempts IS NULL
        """
    )


def _fetch_today_yesterday_candidates(cur, today: date, yesterday: date) -> List[ItemRow]:
    cur.execute(
        """
        SELECT
          id,
          identificador,
          COALESCE(titulo, '') AS titulo,
          COALESCE(url_pdf, '') AS url_pdf,
          fecha_publicacion,
          (created_at::date) AS created_at_date,
          COALESCE(ai_status, 'pending') AS ai_status,
          COALESCE(ai_attempts, 0) AS ai_attempts
        FROM items
        WHERE
          COALESCE(fecha_publicacion, created_at::date) IN (%s, %s)
          AND COALESCE(ai_status, 'pending') <> 'done'
          AND COALESCE(ai_attempts, 0) < %s
        ORDER BY COALESCE(fecha_publicacion, created_at::date) DESC, id DESC
        """,
        (today, yesterday, AI_MAX_ATTEMPTS),
    )
    rows = cur.fetchall() or []
    return [
        ItemRow(
            id=int(r[0]),
            identificador=str(r[1]),
            titulo=str(r[2] or ""),
            url_pdf=str(r[3] or ""),
            fecha_publicacion=r[4],
            created_at_date=r[5],
            ai_status=str(r[6] or "pending"),
            ai_attempts=int(r[7] or 0),
        )
        for r in rows
    ]


def _fetch_recovery_candidates(cur) -> List[ItemRow]:
    """
    Cola de recuperación (profesional):
    - pending/failed
    - attempts < max
    - cooldown (no reintentar constantemente)
    - LIMIT fijo
    """
    cur.execute(
        f"""
        SELECT
          id,
          identificador,
          COALESCE(titulo, '') AS titulo,
          COALESCE(url_pdf, '') AS url_pdf,
          fecha_publicacion,
          (created_at::date) AS created_at_date,
          COALESCE(ai_status, 'pending') AS ai_status,
          COALESCE(ai_attempts, 0) AS ai_attempts
        FROM items
        WHERE
          COALESCE(ai_status, 'pending') IN ('pending', 'failed')
          AND COALESCE(ai_attempts, 0) < %s
          AND (
            ai_last_attempt_at IS NULL
            OR ai_last_attempt_at < (NOW() - INTERVAL '{AI_RECOVERY_COOLDOWN_HOURS} hours')
          )
        ORDER BY COALESCE(ai_last_attempt_at, '1970-01-01'::timestamp) ASC, id DESC
        LIMIT %s
        """,
        (AI_MAX_ATTEMPTS, AI_RECOVERY_LIMIT),
    )
    rows = cur.fetchall() or []
    return [
        ItemRow(
            id=int(r[0]),
            identificador=str(r[1]),
            titulo=str(r[2] or ""),
            url_pdf=str(r[3] or ""),
            fecha_publicacion=r[4],
            created_at_date=r[5],
            ai_status=str(r[6] or "pending"),
            ai_attempts=int(r[7] or 0),
        )
        for r in rows
    ]


def _mark_attempt(cur, item_id: int, new_attempts: int, status: str) -> None:
    cur.execute(
        """
        UPDATE items
        SET
          ai_attempts = %s,
          ai_status = %s,
          ai_last_attempt_at = NOW(),
          ai_updated_at = NOW()
        WHERE id = %s
        """,
        (new_attempts, status, item_id),
    )


def _mark_failed(cur, item_id: int, new_attempts: int, err: str) -> None:
    cur.execute(
        """
        UPDATE items
        SET
          ai_attempts = %s,
          ai_status = 'failed',
          ai_last_error = %s,
          ai_last_attempt_at = NOW(),
          ai_updated_at = NOW()
        WHERE id = %s
        """,
        (new_attempts, (err or "")[:1500], item_id),
    )


def _mark_done(cur, item_id: int) -> None:
    cur.execute(
        """
        UPDATE items
        SET
          ai_status = 'done',
          ai_last_error = NULL,
          ai_done_at = NOW(),
          ai_updated_at = NOW()
        WHERE id = %s
        """,
        (item_id,),
    )


def _save_ai_payload(cur, item_id: int, resumen_json: str, impacto_json: str) -> None:
    resumen_comp = compress_json(resumen_json) if resumen_json else None
    impacto_comp = compress_json(impacto_json) if impacto_json else None

    cur.execute(
        """
        UPDATE items
        SET
          resumen = %s,
          informe_impacto = %s,
          updated_at = NOW()
        WHERE id = %s
        """,
        (resumen_comp, impacto_comp, item_id),
    )


def _process_item(cur, row: ItemRow) -> Tuple[bool, str]:
    """
    Devuelve (ok, message). PDF-first estricto.
    """
    if not row.url_pdf:
        return False, "Item sin url_pdf: PDF-first estricto (no se llama a OpenAI)."

    # Subimos attempts y marcamos processing
    new_attempts = row.ai_attempts + 1
    _mark_attempt(cur, row.id, new_attempts, "processing")

    try:
        titulo_resumen, resumen_json, impacto_json = get_openai_responses_from_pdf(
            identificador=row.identificador,
            titulo=row.titulo,
            url_pdf=row.url_pdf,
        )

        # get_openai_responses_from_pdf devuelve JSON vacíos si no pudo extraer texto del PDF.
        # Esto NO debe contarse como "done".
        if not resumen_json or resumen_json.strip() in ("{}", "[]") or not impacto_json or impacto_json.strip() in ("{}", "[]"):
            return False, "Extracción PDF insuficiente: OpenAI omitido (JSON vacío)."

        _save_ai_payload(cur, row.id, resumen_json, impacto_json)

        # Guardar titulo_resumen si viene (sin forzar)
        if titulo_resumen and titulo_resumen.strip():
            cur.execute(
                "UPDATE items SET titulo_resumen = %s, updated_at = NOW() WHERE id = %s",
                (titulo_resumen.strip(), row.id),
            )

        _mark_done(cur, row.id)
        return True, "OK"

    except Exception as e:
        return False, f"Error IA/PDF: {e}"


def main() -> int:
    today = date.today()
    yesterday = today - timedelta(days=1)

    logger.info("🧠 Backfill IA: hoy=%s ayer=%s max_attempts=%s", today, yesterday, AI_MAX_ATTEMPTS)

    with get_db() as conn:
        with conn.cursor() as cur:
            _ensure_ai_columns(cur)
            _normalize_ai_defaults(cur)

            # 1) Scope principal: HOY + AYER
            primary = _fetch_today_yesterday_candidates(cur, today, yesterday)
            logger.info("📌 Candidatos HOY/AYER: %s", len(primary))

            ok_count = 0
            fail_count = 0

            for row in primary:
                logger.info("➡️ IA item %s (attempts=%s status=%s)", row.identificador, row.ai_attempts, row.ai_status)
                ok, msg = _process_item(cur, row)
                if ok:
                    ok_count += 1
                    logger.info("✅ IA DONE %s", row.identificador)
                else:
                    fail_count += 1
                    new_attempts = row.ai_attempts + 1
                    _mark_failed(cur, row.id, new_attempts, msg)
                    logger.warning("⚠️ IA FAILED %s: %s", row.identificador, msg)

            # 2) Recuperación: cola de pendientes/failed antiguos
            recovery = _fetch_recovery_candidates(cur)
            logger.info("🧯 Candidatos RECOVERY: %s (limit=%s cooldown=%sh)", len(recovery), AI_RECOVERY_LIMIT, AI_RECOVERY_COOLDOWN_HOURS)

            for row in recovery:
                logger.info("➡️ RECOVERY item %s (attempts=%s status=%s)", row.identificador, row.ai_attempts, row.ai_status)
                ok, msg = _process_item(cur, row)
                if ok:
                    ok_count += 1
                    logger.info("✅ IA DONE %s", row.identificador)
                else:
                    fail_count += 1
                    new_attempts = row.ai_attempts + 1
                    _mark_failed(cur, row.id, new_attempts, msg)
                    logger.warning("⚠️ IA FAILED %s: %s", row.identificador, msg)

        conn.commit()

    logger.info("🏁 Backfill IA completado. ok=%s failed=%s", ok_count, fail_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
