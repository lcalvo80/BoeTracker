# update_resumen_diario.py
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from app.services.boe_fetcher import fetch_boe_xml
from app.services.boe_daily_summary import build_section_inputs
from app.services.daily_summary_ai import generate_section_summary
from app.services.daily_summary_svc import (
    ensure_table,
    get_section_row_meta,
    upsert_section_summary,
)


def _configure_logging() -> None:
    level_name = (os.getenv("LOG_LEVEL") or "INFO").upper().strip()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")


_configure_logging()

# En local cargamos .env; en GitHub Actions NO
if os.getenv("GITHUB_ACTIONS") != "true":
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        logging.info("🟢 .env file loaded.")
    else:
        logging.warning("⚠️ No .env file found.")


def _parse_input_date(value: str) -> date:
    v = (value or "").strip()
    if not v:
        raise ValueError("Fecha vacía")
    if len(v) == 8 and v.isdigit():
        return datetime.strptime(v, "%Y%m%d").date()
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(f"Fecha inválida '{value}'. Usa YYYY-MM-DD o YYYYMMDD.") from e


def _iter_dates(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _parse_args(argv: list[str] | None = None):
    p = argparse.ArgumentParser(
        description="Genera y guarda el Resumen Diario del BOE por secciones (OpenAI → Postgres)."
    )
    p.add_argument(
        "--from-date",
        dest="from_date",
        help="Fecha inicio (YYYY-MM-DD o YYYYMMDD). Vacío = hoy+ayer.",
    )
    p.add_argument(
        "--to-date",
        dest="to_date",
        help="Fecha fin (YYYY-MM-DD o YYYYMMDD). Vacío = from-date.",
    )
    return p.parse_args(argv)


def _run_for_date(d: date) -> int:
    logging.info("📅 Resumen diario — procesando BOE del %s…", d.isoformat())

    try:
        root = fetch_boe_xml(d)
    except Exception:
        logging.exception("❌ Error descargando sumario del BOE para %s.", d)
        return 1

    if root is None:
        logging.info("ℹ️ No hay sumario disponible para %s (BOE 404).", d.isoformat())
        return 0

    ensure_table()

    sections = build_section_inputs(root)
    if not sections:
        logging.warning("⚠️ No se encontraron secciones en el XML para %s.", d.isoformat())
        return 0

    force = os.getenv("FORCE_DAILY_SUMMARY_REBUILD", "0") == "1"
    target_ver = int(os.getenv("DAILY_SUMMARY_PROMPT_VERSION", "1"))

    processed = 0
    skipped = 0

    for s in sections:
        meta = get_section_row_meta(fecha_publicacion=d, seccion_codigo=s.seccion_codigo)
        if meta and not force:
            cur_ver, cur_txt = meta
            if int(cur_ver) == int(target_ver) and (cur_txt or "").strip():
                skipped += 1
                continue

        try:
            ai = generate_section_summary(fecha_publicacion=d, section=s)
            resumen_txt = (ai.get("summary") or "").strip()
            resumen_json = {
                "summary": resumen_txt,
                "highlights": ai.get("highlights") or [],
                "top_items": ai.get("top_items") or [],
            }

            source_dept_counts = [(k, int(v)) for (k, v) in (s.dept_counts or [])]
            source_sample_items = [
                {
                    "identificador": it.identificador,
                    "titulo": it.titulo,
                    "departamento": it.departamento,
                    "epigrafe": it.epigrafe,
                }
                for it in (s.sample_items or [])
            ]

            upsert_section_summary(
                fecha_publicacion=d,
                seccion_codigo=s.seccion_codigo,
                seccion_nombre=s.seccion_nombre,
                total_entradas=s.total_entradas,
                resumen_texto=resumen_txt,
                resumen_json=resumen_json,
                ai_model=str(ai.get("ai_model") or ""),
                ai_prompt_version=int(ai.get("ai_prompt_version") or target_ver),
                source_dept_counts=source_dept_counts,
                source_sample_items=source_sample_items,
            )

            processed += 1
            if processed % 3 == 0:
                logging.info("… progreso: processed=%s skipped=%s", processed, skipped)

            time.sleep(float(os.getenv("DAILY_SUMMARY_SLEEP_SECS", "0.2")))

        except Exception:
            logging.exception("❌ Error generando/guardando resumen para sección %s (%s)", s.seccion_codigo, d)
            continue

    logging.info(
        "✅ Resumen diario %s completado. processed=%s skipped=%s total_sections=%s",
        d.isoformat(),
        processed,
        skipped,
        len(sections),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    had_error = False

    if not args.from_date and not args.to_date:
        today = date.today()
        yesterday = today - timedelta(days=1)
        logging.info("🗓️ Ejecutando resumen diario por defecto para AYER + HOY.")
        rc1 = _run_for_date(yesterday)
        rc2 = _run_for_date(today)
        had_error = (rc1 != 0) or (rc2 != 0)
        return 1 if had_error else 0

    try:
        if args.from_date:
            start_date = _parse_input_date(args.from_date)
        else:
            start_date = _parse_input_date(args.to_date)

        end_date = _parse_input_date(args.to_date) if args.to_date else start_date
    except ValueError as e:
        logging.error("❌ %s", e)
        return 1

    if end_date < start_date:
        logging.warning("⚠️ to-date (%s) < from-date (%s). Intercambiando.", end_date, start_date)
        start_date, end_date = end_date, start_date

    logging.info("🗓️ Ejecutando resumen diario para el rango %s → %s (inclusive).", start_date, end_date)
    for d in _iter_dates(start_date, end_date):
        rc = _run_for_date(d)
        if rc != 0:
            had_error = True

    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
