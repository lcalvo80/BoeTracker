import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# 🧠 Asegura que se pueda importar el paquete app/
# (La carpeta donde está este fichero es el root del proyecto; app/ cuelga de aquí)
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

# ✅ Imports locales
from app.services.postgres import get_db
from app.services.boe_fetcher import fetch_boe_xml
from app.services.parser import parse_and_insert

# 🪵 Logging básico
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# ✅ Cargar .env solo en desarrollo local (no en GitHub Actions)
if os.getenv("GITHUB_ACTIONS") != "true":
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        logging.info("🟢 .env file loaded.")
    else:
        logging.warning("⚠️ No .env file found.")

# 🔐 Verificar API Key (necesaria para las llamadas a OpenAI desde el backend)
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    logging.error("❌ OPENAI_API_KEY not found. Check .env or GitHub secret.")
    sys.exit(1)


# 📦 Contar ítems en la tabla items
def get_item_count() -> int:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM items")
            return cur.fetchone()[0]


# 🚀 Ejecutar proceso completo
if __name__ == "__main__":
    logging.info("🚀 Iniciando actualización del BOE (sumario + inserción en BD)...")

    try:
        initial_count = get_item_count()
        logging.info(f"📦 Ítems antes: {initial_count}")

        # 1️⃣ Descargar sumario XML del BOE para la fecha objetivo (por defecto hoy)
        root = fetch_boe_xml()
        if root is None:
            logging.warning(
                "ℹ️ No hay sumario disponible para hoy (BOE 404). "
                "Proceso completado sin cambios."
            )
            sys.exit(0)  # ✅ no fallamos el job si no hay BOE

        # 2️⃣ Parsear el XML e insertar ítems en BD.
        #    Dentro de parse_and_insert es donde se llamará al pipeline de IA
        #    (título, resumen, impacto) que ahora usa SIEMPRE el PDF del BOE
        #    a través de tus servicios / APIs internos.
        inserted = parse_and_insert(root)

        final_count = get_item_count()

        logging.info(f"🆕 Ítems nuevos insertados: {inserted}")
        logging.info(f"📦 Total actual en BD: {final_count}")
        logging.info("✅ Proceso de actualización del BOE completado con éxito.")
        sys.exit(0)

    except Exception as e:
        logging.exception(f"❌ Error general en la actualización del BOE: {e}")
        sys.exit(1)
