import os
import logging
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from services.database import create_databases, DB_ITEMS
from services.boe_fetcher import fetch_boe_xml
from services.parser import parse_and_insert

# Cargar .env desde la ubicación del archivo actual
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

# Verificar si la clave fue cargada
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    logging.error("❌ La variable OPENAI_API_KEY no se ha cargado. Verifica tu archivo .env")
    exit(1)
else:
    logging.info("✅ OPENAI_API_KEY cargada correctamente.")

logging.basicConfig(level=logging.INFO)

def get_db_item_count():
    with sqlite3.connect(DB_ITEMS) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM items")
        return cursor.fetchone()[0]

if __name__ == "__main__":
    logging.info("🚀 Iniciando proceso de actualización del BOE...")
    create_databases()

    initial_count = get_db_item_count()
    logging.info(f"📦 Ítems en base de datos al inicio: {initial_count}")

    root = fetch_boe_xml()
    if root is not None:
        boe_items_count = len(root.findall(".//item"))
        logging.info(f"📨 Ítems obtenidos desde la API del BOE: {boe_items_count}")

        inserted_count = parse_and_insert(root)
        final_count = get_db_item_count()

        logging.info(f"🆕 Ítems nuevos insertados en la base de datos: {inserted_count}")
        logging.info(f"📦 Ítems en base de datos al finalizar: {final_count}")
    else:
        logging.warning("⚠️ No se pudo obtener el XML del BOE.")
