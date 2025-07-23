import logging
from datetime import datetime, date
from typing import Optional
from xml.etree import ElementTree as ET
from app.services.openai_service import get_openai_responses
from app.services.postgres import get_db

logger = logging.getLogger(__name__)


def clasificar_item(nombre_seccion: str) -> str:
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
        return "Disposición"  # fallback


def safe_date(text: str) -> Optional[date]:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def procesar_item(cur, item, seccion, dept, epigrafe, clase_item, counters):
    identificador = item.findtext("identificador", "").strip()
    titulo = item.findtext("titulo", "").strip()

    if not identificador or not titulo:
        logger.warning("❗ Ítem omitido por identificador o título vacío.")
        counters["omitidos_vacios"] += 1
        return

    cur.execute("SELECT 1 FROM items WHERE identificador = %s", (identificador,))
    if cur.fetchone():
        logger.info(f"⏭️  Ya procesado: {identificador}")
        counters["omitidos_existentes"] += 1
        return

    try:
        titulo_resumen, resumen_json, impacto_json = get_openai_responses(titulo, titulo)
    except Exception as e:
        logger.error(f"❌ OpenAI error en '{identificador}': {e}")
        counters["fallos_openai"] += 1
        return

    fecha_publicacion = safe_date(item.findtext("fecha_publicacion", "").strip())

    cur.execute("""
        INSERT INTO items (
            identificador, titulo, titulo_resumen, resumen, informe_impacto,
            url_pdf, url_html, url_xml,
            seccion_codigo, seccion_nombre,
            departamento_codigo, departamento_nombre,
            epigrafe, control, fecha_publicacion, clase_item
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        identificador,
        titulo,
        titulo_resumen.rstrip("."),
        resumen_json,
        impacto_json,
        item.findtext("url_pdf", "").strip(),
        item.findtext("url_html", "").strip(),
        item.findtext("url_xml", "").strip(),
        seccion.get("codigo", "") if seccion is not None else "",
        seccion.get("nombre", "") if seccion is not None else "",
        dept.get("codigo", "") if dept is not None else "",
        dept.get("nombre", "") if dept is not None else "",
        epigrafe.get("nombre", "") if epigrafe is not None else "",
        item.findtext("control", "").strip(),
        fecha_publicacion,
        clase_item
    ))

    logger.info(f"✅ Insertado: {identificador}")
    counters["insertados"] += 1


def parse_and_insert(root: ET.Element) -> int:
    counters = {
        "insertados": 0,
        "omitidos_existentes": 0,
        "omitidos_vacios": 0,
        "fallos_openai": 0,
        "huerfanos_en_seccion": 0,
    }

    with get_db() as conn:
        cur = conn.cursor()

        for seccion in root.findall(".//seccion"):
            clase_item = clasificar_item(seccion.get("nombre", ""))

            for dept in seccion.findall("departamento"):
                for epigrafe in dept.findall("epigrafe"):
                    for item in epigrafe.findall("item"):
                        procesar_item(cur, item, seccion, dept, epigrafe, clase_item, counters)

                for item in dept.findall("item"):
                    procesar_item(cur, item, seccion, dept, None, clase_item, counters)

            for item in seccion.findall("item"):
                procesar_item(cur, item, seccion, None, None, clase_item, counters)
                counters["huerfanos_en_seccion"] += 1

        conn.commit()

    logger.info("📊 RESUMEN FINAL:")
    for k, v in counters.items():
        logger.info(f"   {k}: {v}")

    return counters["insertados"]
