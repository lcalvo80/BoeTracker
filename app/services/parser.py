# app/services/parser.py
from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Optional
from xml.etree import ElementTree as ET

from app.services.openai_service import get_openai_responses
from app.services.postgres import get_db
from app.utils.compression import compress_json
from app.services.lookup import ensure_seccion_cur, ensure_departamento_cur

logger = logging.getLogger(__name__)

# ------------------------------ utilidades ------------------------------

def clasificar_item(nombre_seccion: str) -> str:
    nombre = (nombre_seccion or "").lower()
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

def _emptyish(x) -> bool:
    if x is None:
        return True
    if isinstance(x, str):
        s = x.strip()
        return len(s) == 0 or s in ("{}", "[]")
    if isinstance(x, (list, dict)):
        return len(x) == 0
    return False

def _compose_text(item: ET.Element, seccion, dept, epigrafe) -> str:
    """
    Construye un cuerpo útil para OpenAI: posibles campos de texto + metadatos.
    """
    candidates = []

    # Campos comunes en el XML del BOE (cubrimos varias denominaciones)
    preferred_tags = (
        "contenido",
        "texto",
        "sumario",
        "extracto",
        "resumen",
        "descripcion",
        "descripción",
        "cuerpo",
        "detalle",
    )
    for tag in preferred_tags:
        val = item.findtext(tag)
        if val and val.strip():
            candidates.append(val.strip())

    # Metadatos
    meta_parts = [
        (seccion.get("nombre", "").strip() if seccion is not None else ""),
        (dept.get("nombre", "").strip() if dept is not None else ""),
        (epigrafe.get("nombre", "").strip() if epigrafe is not None else ""),
        (item.findtext("control", "") or "").strip(),
    ]
    meta = " | ".join([m for m in meta_parts if m])
    if meta:
        candidates.append(meta)

    text = "\n\n".join([c for c in candidates if c]).strip()
    return text

# ------------------------------ pipeline ------------------------------

def procesar_item(cur, item, seccion, dept, epigrafe, clase_item, counters):
    identificador = (item.findtext("identificador", "") or "").strip()
    titulo = (item.findtext("titulo", "") or "").strip()

    if not identificador or not titulo:
        logger.warning("❗ Ítem omitido por identificador o título vacío.")
        counters["omitidos_vacios"] += 1
        return

    cur.execute("SELECT 1 FROM items WHERE identificador = %s", (identificador,))
    if cur.fetchone():
        logger.info(f"⏭️  Ya procesado: {identificador}")
        counters["omitidos_existentes"] += 1
        return

    cuerpo = _compose_text(item, seccion, dept, epigrafe)
    if _emptyish(cuerpo):
        meta = " | ".join(filter(None, [
            seccion.get("nombre", "") if seccion is not None else "",
            dept.get("nombre", "") if dept is not None else "",
            epigrafe.get("nombre", "") if epigrafe is not None else "",
            (item.findtext("control", "") or "").strip(),
        ]))
        cuerpo = (titulo + ("\n\n" + meta if meta else "")).strip()

    try:
        titulo_resumen, resumen_json, impacto_json = get_openai_responses(titulo, cuerpo)
    except Exception as e:
        logger.error(f"❌ OpenAI error en '{identificador}': {e}")
        counters["fallos_openai"] += 1
        return

    resumen_comp = None if _emptyish(resumen_json) else compress_json(resumen_json)
    impacto_comp = None if _emptyish(impacto_json) else compress_json(impacto_json)

    if resumen_comp is None:
        logger.debug(f"ℹ️  Resumen vacío para {identificador} (no se guarda).")
    if impacto_comp is None:
        logger.debug(f"ℹ️  Impacto vacío para {identificador} (no se guarda).")

    fecha_publicacion = safe_date((item.findtext("fecha_publicacion", "") or "").strip())
    titulo_resumen_final = (titulo_resumen or "").strip().rstrip(".") or titulo

    cur.execute(
        """
        INSERT INTO items (
            identificador, titulo, titulo_resumen, resumen, informe_impacto,
            url_pdf, url_html, url_xml,
            seccion_codigo, departamento_codigo,
            epigrafe, control, fecha_publicacion, clase_item
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            identificador,
            titulo,
            titulo_resumen_final,
            resumen_comp,                 # NULL si vacío
            impacto_comp,                 # NULL si vacío
            (item.findtext("url_pdf", "") or "").strip(),
            (item.findtext("url_html", "") or "").strip(),
            (item.findtext("url_xml", "") or "").strip(),
            seccion.get("codigo", "") if seccion else "",
            dept.get("codigo", "") if dept else "",
            epigrafe.get("nombre", "") if epigrafe else "",
            (item.findtext("control", "") or "").strip(),
            fecha_publicacion,
            clase_item,
        ),
    )

    logger.info(f"✅ Insertado: {identificador}")
    counters["insertados"] += 1

def parse_and_insert(root: ET.Element) -> int:
    counters = {
        "insertados": 0,
        "omitidos_existentes": 0,
        "omitidos_vacios": 0,
        "fallos_openai": 0,
        "huerfanos_en_seccion": 0,
        "lookup_sec_insert": 0,
        "lookup_sec_update": 0,
        "lookup_dep_insert": 0,
        "lookup_dep_update": 0,
    }

    with get_db() as conn:
        with conn.cursor() as cur:
            for seccion in root.findall(".//seccion"):
                # Lookup seccion
                sec_codigo = (seccion.get("codigo", "") or "").strip()
                sec_nombre = (seccion.get("nombre", "") or "").strip()
                act_sec = ensure_seccion_cur(cur, sec_codigo, sec_nombre)
                if act_sec == "insert":
                    counters["lookup_sec_insert"] += 1
                elif act_sec == "update_name":
                    counters["lookup_sec_update"] += 1

                clase_item = clasificar_item(sec_nombre)

                for dept in seccion.findall("departamento"):
                    # Lookup dept
                    dep_codigo = (dept.get("codigo", "") or "").strip()
                    dep_nombre = (dept.get("nombre", "") or "").strip()
                    act_dep = ensure_departamento_cur(cur, dep_codigo, dep_nombre)
                    if act_dep == "insert":
                        counters["lookup_dep_insert"] += 1
                    elif act_dep == "update_name":
                        counters["lookup_dep_update"] += 1

                    for epigrafe in dept.findall("epigrafe"):
                        for item in epigrafe.findall("item"):
                            procesar_item(cur, item, seccion, dept, epigrafe, clase_item, counters)

                    for item in dept.findall("item"):
                        procesar_item(cur, item, seccion, dept, None, clase_item, counters)

                for item in seccion.findall("item"):
                    procesar_item(cur, item, seccion, None, None, clase_item, counters)
                    counters["huerfanos_en_seccion"] += 1

    logger.info("📊 RESUMEN FINAL:")
    for k, v in counters.items():
        logger.info(f"   {k}: {v}")

    return counters["insertados"]
