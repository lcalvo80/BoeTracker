import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# ✅ AÑADIR el path del proyecto al principio (antes de los imports locales)1
sys.path.append(str(Path(__file__).resolve().parent))

# ✅ Importaciones de servicios después de añadir el path
from app.services.postgres import get_db
from app.services.boe_fetcher import fetch_boe_xml
from app.services.parser import parse_and_insert

# 🔧 Logging básico
logging.basicConfig(level=logging.INFO)

# ✅ Cargar .env solo si no estamos en GitHub Actions
if os.getenv("GITHUB_ACTIONS") != "true":
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        logging.info("🟢 .env file loaded.")
    else:
        logging.warning("⚠️ No .env file found.")

# 🔐 Verifica OPENAI_API_KEY
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    logging.error("❌ OPENAI_API_KEY not found. Check GitHub secret or .env file.")
    exit(1)

# 📦 Obtener número total de ítems
def get_item_count():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM items")
        return cur.fetchone()[0]

# 🚀 Ejecutar el proceso completo
if __name__ == "__main__":
    logging.info("🚀 Iniciando actualización del BOE...")
    initial_count = get_item_count()

    root = fetch_boe_xml()
    if root is None:
        logging.warning("⚠️ No se pudo obtener el XML del BOE.")
        exit(1)

    inserted_count = parse_and_insert(root)
    final_count = get_item_count()

    logging.info(f"🆕 Ítems nuevos insertados: {inserted_count}")
    logging.info(f"📦 Ítems totales en base de datos: {final_count}")
