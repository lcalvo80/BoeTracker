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

if os.getenv("GITHUB_ACTIONS") != "true":
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        logging.info("🟢 .env file loaded.")
    else:
        logging.warning("⚠️ No .env file found.")

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    logging.error("❌ OPENAI_API_KEY not found. Check .env o GitHub secret.")
    sys.exit(1)


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
        description="Ingesta del BOE en PostgreSQL (solo inserta + marca pending IA)."
    )
    parser.add_argument(
        "--from-date",
        dest="from_date",
        help="Fecha inicio (YYYY-MM-DD o YYYYMMDD). Vacío = hoy+ayer.",
    )
    parser.add_argument(
        "--to-date",
        dest="to_date",
        help="Fecha fin (YYYY-MM-DD o YYYYMMDD). Vacío = from-date.",
    )
    return parser.parse_args(argv)


def _run_for_date(d: date) -> int:
    logging.info("📅 Procesando BOE del %s…", d.isoformat())

    try:
        root = fetch_boe_xml(d)
    except Exception:
        logging.exception("❌ Error descargando sumario del BOE para %s.", d)
        return 1

    if root is None:
        logging.info("ℹ️ No hay sumario disponible para %s (BOE 404).", d.isoformat())
        return 0

    try:
        inserted = parse_and_insert(root, run_date=d)
        logging.info("✅ BOE %s procesado. Ítems nuevos insertados: %s.", d.isoformat(), inserted)
        return 0
    except Exception:
        logging.exception("❌ Error parseando/inserando el BOE para %s.", d)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    initial_count = get_item_count()
    logging.info("📦 Ítems antes: %s", initial_count)

    # Caso A: sin fechas -> HOY + AYER
    if not args.from_date and not args.to_date:
        today = date.today()
        yesterday = today - timedelta(days=1)

        logging.info("🗓️ Ejecutando ingesta por defecto para AYER + HOY.")
        rc1 = _run_for_date(yesterday)
        rc2 = _run_for_date(today)

        final_count = get_item_count()
        logging.info("📦 Total actual en BD: %s", final_count)
        logging.info("✅ Proceso de ingesta BOE completado (ayer+hoy).")
        return 0 if (rc1 == 0 and rc2 == 0) else 1

    # Caso B: rango explícito
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

    logging.info("🗓️ Ejecutando ingesta para el rango %s → %s (inclusive).", start_date, end_date)

    for d in _iter_dates(start_date, end_date):
        _run_for_date(d)

    final_count = get_item_count()
    logging.info("📦 Total actual en BD: %s", final_count)
    logging.info("✅ Proceso de ingesta BOE (rango) completado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
