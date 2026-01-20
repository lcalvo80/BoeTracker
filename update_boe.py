# update_boe.py
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from app.services.postgres import get_db
from app.services.boe_fetcher import fetch_boe_xml
from app.services.parser import parse_and_insert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# ✅ Cargar .env solo en desarrollo/local (no en GitHub Actions)
if os.getenv("GITHUB_ACTIONS") != "true":
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        logging.info("🟢 .env file loaded.")
    else:
        logging.warning("⚠️ No .env file found.")


def get_item_count() -> int:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM items")
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0


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
    current = start
    delta = timedelta(days=1)
    while current <= end:
        yield current
        current += delta


def _parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Ingesta del BOE en PostgreSQL (INGESTA ONLY, sin OpenAI)."
    )
    parser.add_argument(
        "--from-date",
        dest="from_date",
        help="Fecha inicio (YYYY-MM-DD o YYYYMMDD). Vacío = hoy.",
    )
    parser.add_argument(
        "--to-date",
        dest="to_date",
        help="Fecha fin (YYYY-MM-DD o YYYYMMDD). Vacío = from-date.",
    )
    return parser.parse_args(argv)


def _run_single_day(d: date | None = None) -> int:
    if d is None:
        logging.info("🗓️ Ejecutando ingesta SOLO para hoy (por defecto).")
    else:
        logging.info("🗓️ Ejecutando ingesta para la fecha %s.", d.isoformat())

    initial_count = get_item_count()
    logging.info("📦 Ítems antes: %s", initial_count)

    try:
        root = fetch_boe_xml(d) if d is not None else fetch_boe_xml()
    except Exception:
        logging.exception("❌ Error descargando sumario del BOE.")
        return 1

    if root is None:
        logging.warning(
            "ℹ️ No hay sumario disponible para la fecha objetivo (BOE 404). "
            "Proceso completado sin cambios."
        )
        final_count = get_item_count()
        logging.info("📦 Total actual en BD: %s", final_count)
        return 0

    try:
        inserted = parse_and_insert(root)
    except Exception:
        logging.exception("❌ Error parseando/inserando el BOE.")
        return 1

    final_count = get_item_count()
    logging.info("🆕 Ítems nuevos insertados: %s", inserted)
    logging.info("📦 Total actual en BD: %s", final_count)
    logging.info("✅ Proceso de ingesta BOE completado con éxito.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not args.from_date and not args.to_date:
        return _run_single_day(None)

    try:
        if args.from_date:
            start_date = _parse_input_date(args.from_date)
        elif args.to_date:
            start_date = _parse_input_date(args.to_date)
        else:
            return _run_single_day(None)

        if args.to_date:
            end_date = _parse_input_date(args.to_date)
        else:
            end_date = start_date

    except ValueError as e:
        logging.error("❌ %s", e)
        return 1

    if end_date < start_date:
        logging.warning(
            "⚠️ to-date (%s) es anterior a from-date (%s). Intercambiando.",
            end_date,
            start_date,
        )
        start_date, end_date = end_date, start_date

    logging.info(
        "🗓️ Ejecutando ingesta para el rango %s → %s (inclusive).",
        start_date,
        end_date,
    )

    initial_count = get_item_count()
    logging.info("📦 Ítems antes: %s", initial_count)

    total_inserted = 0
    for d in _iter_dates(start_date, end_date):
        logging.info("📅 Procesando BOE del %s…", d.isoformat())
        try:
            root = fetch_boe_xml(d)
        except Exception:
            logging.exception("❌ Error descargando sumario del BOE para %s.", d)
            continue

        if root is None:
            logging.info("ℹ️ No hay sumario disponible para %s (BOE 404). Se omite.", d.isoformat())
            continue

        try:
            inserted = parse_and_insert(root)
            total_inserted += inserted
            logging.info("✅ BOE %s procesado. Ítems nuevos insertados: %s.", d.isoformat(), inserted)
        except Exception:
            logging.exception("❌ Error parseando/inserando BOE para la fecha %s.", d)

    final_count = get_item_count()
    logging.info("🆕 Ítems nuevos insertados en el rango: %s", total_inserted)
    logging.info("📦 Total actual en BD: %s", final_count)
    logging.info("✅ Proceso de ingesta BOE (rango) completado con éxito.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
