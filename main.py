import os
import logging
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from services.database import create_databases, DB_ITEMS
from services.boe_fetcher import fetch_boe_xml
from services.parser import parse_and_insert

logging.basicConfig(level=logging.INFO)

# 🔐 Only load .env if not running inside GitHub Actions
if os.getenv("GITHUB_ACTIONS") != "true":
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        logging.info("🟢 .env file loaded for local development.")
    else:
        logging.warning("⚠️ No .env file found for local use.")

# Get the OpenAI API key from environment
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    logging.error("❌ OPENAI_API_KEY not found. Check GitHub secret or .env file.")
    exit(1)
else:
    logging.info("✅ OPENAI_API_KEY loaded successfully.")

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
