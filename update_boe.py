import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# 🧠 Asegura que se pueda importar desde app/
sys.path.append(str(Path(__file__).resolve().parent / "app"))

# ✅ Imports locales
from app.services.postgres import get_db
from app.services.boe_fetcher import fetch_boe_xml
from app.services.parser import parse_and_insert

# 🪵 Logging
logging.basicConfig(level=logging.INFO)

# ✅ Cargar .env solo en desarrollo local
if os.getenv("GITHUB_ACTIONS") != "true":
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        logging.info("🟢 .env file loaded.")
    else:
        logging.warning("⚠️ No .env file found.")

# 🔐 Verificar API Key
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    logging.error("❌ OPENAI_API_KEY not found. Check .env or GitHub secret.")
    exit(1)

# 📦 Contar ítems
def get_item_count():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM items")
        return cur.fetchone()[0]

# 🚀 Ejecutar proceso completo
if __name__ == "__main__":
    logging.info("🚀 Iniciando actualización del BOE...")

    try:
        initial_count = get_item_count()
        logging.info(f"📦 Ítems antes: {initial_count}")

        root = fetch_boe_xml()
        if root is None:
            logging.warning("⚠️ No se pudo obtener el XML del BOE.")
            exit(1)

        inserted = parse_and_insert(root)
        final_count = get_item_count()

        logging.info(f"🆕 Ítems nuevos insertados: {inserted}")
        logging.info(f"📦 Total actual en BD: {final_count}")

    except Exception as e:
        logging.error(f"❌ Error general: {e}")
        exit(1)
