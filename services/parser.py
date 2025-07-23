import sqlite3
import logging
from datetime import datetime
from xml.etree import ElementTree as ET
from services.openai_service import get_openai_responses
from services.database import DB_ITEMS

# Configurar logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

current_date = datetime.now().strftime('%Y-%m-%d')


def clasificar_item(nombre_seccion):
    nombre = nombre_seccion.lower()
    
    if "anuncio" in nombre:
        return "Anuncio"
    elif "disposición" in nombre or "disposicion" in nombre or "otras disposiciones" in nombre:
        return "Disposición"
    elif "notificación" in nombre or "notificacion" in nombre:
        return "Notificación"
    elif "edicto" in nombre or "judicial" in nombre:
        return "Edicto judicial"
    elif "personal" in nombre or "nombramiento" in nombre or "concurso" in nombre:
        return "Personal"
    elif "otros" in nombre:
        return "Otros anuncios"
    else:
        return "Disposición"  # fallback más claro en vez de "Otro"



def procesar_item(cursor, item, seccion, dept, epigrafe, clase_item, counters):
    identificador = item.findtext("identificador", "").strip()
    titulo = item.findtext("titulo", "").strip()
    control = item.findtext("control", "")
    url_pdf = item.findtext("url_pdf", "")
    url_html = item.findtext("url_html", "")
    url_xml = item.findtext("url_xml", "")

    if not titulo or not identificador:
        logger.warning(f"❗ Título o identificador vacío en ítem, omitido.")
        counters['omitidos_vacios'] += 1
        return

    cursor.execute("SELECT 1 FROM items WHERE identificador = ?", (identificador,))
    if cursor.fetchone():
        logger.info(f"⏭️  Ya procesado: {identificador}")
        counters['omitidos_existentes'] += 1
        return

    try:
        titulo_resumen, resumen_json, impacto_json = get_openai_responses(titulo, titulo)
    except Exception as e:
        logger.error(f"❌ Error con OpenAI para '{identificador}': {e}")
        counters['fallos_openai'] += 1
        return

    if titulo_resumen.endswith("."):
        titulo_resumen = titulo_resumen.rstrip(".")

    cursor.execute('''
        INSERT OR IGNORE INTO items (
            identificador, titulo, titulo_resumen, resumen, informe_impacto,
            url_pdf, url_html, url_xml,
            seccion_codigo, seccion_nombre,
            departamento_codigo, departamento_nombre,
            epigrafe, control, fecha_publicacion, clase_item
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        identificador, titulo, titulo_resumen, resumen_json, impacto_json,
        url_pdf, url_html, url_xml,
        seccion.get("codigo", ""), seccion.get("nombre", ""),
        dept.get("codigo", "") if dept is not None else "",
        dept.get("nombre", "") if dept is not None else "",
        epigrafe.get("nombre", "") if epigrafe is not None else "",
        control, current_date, clase_item
    ))

    logger.info(f"✅ Guardado: {identificador} ({clase_item})")
    counters['insertados'] += 1


def parse_and_insert(xml_data: ET.Element):
    counters = {
        'insertados': 0,
        'omitidos_existentes': 0,
        'omitidos_vacios': 0,
        'fallos_openai': 0,
        'huerfanos_en_seccion': 0
    }

    with sqlite3.connect(DB_ITEMS) as conn:
        cursor = conn.cursor()

        for seccion in xml_data.findall(".//seccion"):
            clase_item = clasificar_item(seccion.get("nombre", ""))

            for dept in seccion.findall("departamento"):
                for epigrafe in dept.findall("epigrafe"):
                    for item in epigrafe.findall("item"):
                        procesar_item(cursor, item, seccion, dept, epigrafe, clase_item, counters)

                for item in dept.findall("item"):  # ítems directos bajo departamento
                    procesar_item(cursor, item, seccion, dept, None, clase_item, counters)

            for item in seccion.findall("item"):  # ítems directos bajo sección
                procesar_item(cursor, item, seccion, None, None, clase_item, counters)
                counters['huerfanos_en_seccion'] += 1

        conn.commit()

    # Log final
    logger.info("📊 RESUMEN FINAL:")
    logger.info(f"   🔢 Total ítems encontrados: {sum(counters.values())}")
    logger.info(f"   ➕ Nuevos registros insertados: {counters['insertados']}")
    logger.info(f"   ⏭️  Ya existentes (omitidos): {counters['omitidos_existentes']}")
    logger.info(f"   ❗ Título o identificador vacío (omitidos): {counters['omitidos_vacios']}")
    logger.info(f"   ❌ Fallos al generar resumen OpenAI: {counters['fallos_openai']}")
    logger.info(f"   📎 Ítems directos en <seccion> (huérfanos): {counters['huerfanos_en_seccion']}")

    return counters['insertados']
