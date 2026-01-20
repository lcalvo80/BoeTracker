# app/scripts/migrate_items_status_columns.py
from __future__ import annotations

import logging
import os

import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_URL = os.getenv("DATABASE_URL")


DDL = [
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS contenido TEXT",

    "ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_status TEXT",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_attempts INTEGER",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_last_error TEXT",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_last_attempt_at TIMESTAMP",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_completed_at TIMESTAMP",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS ai_source TEXT",

    "ALTER TABLE items ADD COLUMN IF NOT EXISTS pdf_status TEXT",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS pdf_attempts INTEGER",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS pdf_last_error TEXT",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS pdf_last_attempt_at TIMESTAMP",

    # Índices recomendados
    "CREATE INDEX IF NOT EXISTS idx_items_fecha_publicacion ON items (fecha_publicacion DESC)",
    "CREATE INDEX IF NOT EXISTS idx_items_ai_status_fecha ON items (ai_status, fecha_publicacion DESC)",
]


def main() -> int:
    if not DB_URL:
        log.error("❌ DATABASE_URL no definido.")
        return 1

    log.info("🔧 Migrando columnas/índices de estado en items…")
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            for stmt in DDL:
                cur.execute(stmt)
        conn.commit()
        log.info("✅ Migración completada.")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
