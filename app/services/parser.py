# app/services/parser.py
from __future__ import annotations

import logging
import re
from datetime import datetime, date
from typing import Optional
from xml.etree import ElementTree as ET
from psycopg2 import sql

from app.services.openai_service import get_openai_responses
from app.services.postgres import get_db
from app.utils.compression import compress_json

# ───────────────────── Import robusto del lookup ─────────────────────
try:
    import app.services.lookup as _lookup_mod  # type: ignore
except Exception:
    _lookup_mod = None  # type: ignore

# ───────────────────── Enriquecedor de contenido ─────────────────────
try:
    from app.services.html_enricher import enrich_boe_text  # type: ignore
except Exception:
    # Fallback inocuo
    def enrich_boe_text(identificador, url_html, url_txt_candidate, url_pdf, base_text, min_gain_chars=800):
        return base_text, False  # type: ignore

def _fallback_normalize_code(code: str) -> str:
    s = "" if code is None else str(code).strip()
    s = re.sub(r"^0+", "", s)
    return s or "0"

def _fallback_ensure_lookup_table_cur(cur, table: str):
    schema, tbl = ("public", table.split(".", 1)[1]) if "." in table else ("public", table)
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {schema}.{table} (
                codigo TEXT PRIMARY KEY,
                nombre TEXT
            );
            """
        ).format(schema=sql.Identifier(schema), table=sql.Identifier(tbl))
    )

def _fallback_generic_cur(cur, table: str, codigo: str, nombre: str) -> str:
    _fallback_ensure_lookup_table_cur(cur, table)
    code_norm = _fallback_normalize_code(codigo)
    name_norm = (nombre or "").strip()
    schema, tbl = ("public", table.split(".", 1)[1]) if "." in table else ("public", table)

    cur.execute(
        sql.SQL("SELECT nombre FROM {schema}.{table} WHERE codigo = %s").format(
            schema=sql.Identifier(schema), table=sql.Identifier(tbl)
        ),
        (code_norm,),
    )
    row = cur.fetchone()
    if not row:
        cur.execute(
            sql.SQL(
                "INSERT INTO {schema}.{table} (codigo, nombre) VALUES (%s, %s) "
                "ON CONFLICT (codigo) DO NOTHING"
            ).format(schema=sql.Identifier(schema), table=sql.Identifier(tbl)),
            (code_norm, name_norm),
        )
        return "insert"

    current = (row[0] or "").strip()
    if name_norm and name_norm != current:
        cur.execute(
            sql.SQL("UPDATE {schema}.{table} SET nombre = %s WHERE codigo = %s").format(
                schema=sql.Identifier(schema), table=sql.Identifier(tbl)
            ),
            (name_norm, code_norm),
        )
        return "update_name"
    return "noop"

normalize_code = getattr(_lookup_mod, "normalize_code", _fallback_normalize_code)

def ensure_seccion_cur(cur, codigo: str, nombre: str) -> str:
    fn = getattr(_lookup_mod, "ensure_seccion_cur", None)
    return fn(cur, codigo, nombre) if callable(fn) else _fallback_generic_cur(cur, "public.secciones_lookup", codigo, nombre)

def ensure_departamento_cur(cur, codigo: str, nombre: str) -> str:
    fn = getattr(_lookup_mod, "ensure_departamento_cur", None)
    return fn(cur, codigo, nombre) if callable(fn) else _fallback_generic_cur(cur, "public.departamentos_lookup", codigo, nombre)

# ───────────────────── Lógica de parseo ─────────────────────

logger = logging.getLogger(__name__)

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
        return "Disposición"

def safe_date(text: str) -> Optional[date]:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except Exception:
        return None

def _emptyish(x) -> bool:
    if x is None:
        return True
    if isinstance(x, str):
        s = x.trim() if hasattr(x, "trim") else x.strip()
        return len(s) == 0 or s in ("{}", "[]")
    if isinstance(x, (list, dict)):
        return len(x) == 0
    return False

def _compose_text(item: ET.Element, seccion, dept, epigrafe) -> str:
    candidates = []
    for tag in ("contenido","texto","sumario","extracto","resumen","descripcion","descripción","cuerpo","detalle"):
        val = item.findtext(tag)
        if val and val.strip():
            candidates.append(val.strip())

    meta_parts = [
        (seccion.get("nombre", "").strip() if seccion is not None else ""),
        (dept.get("nombre", "").strip() if dept is not None else ""),
        (epigrafe.get("nombre", "").strip() if epigrafe is not None else ""),
        (item.findtext("control", "") or "").strip(),
    ]
    meta = " | ".join([m for m in meta_parts if m])
    if meta:
        candidates.append(meta)

    return "\n\n".join([c for c in candidates if c]).strip()

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

    # Normaliza códigos y asegura lookups
    sec_codigo_norm = normalize_code(seccion.get("codigo", "") if seccion else "")
    dep_codigo_norm = normalize_code(dept.get("codigo", "") if dept else "")

    if seccion is not None:
        sec_nombre = (seccion.get("nombre", "") or "").strip()
        act_sec = ensure_seccion_cur(cur, sec_codigo_norm, sec_nombre)
        if act_sec == "insert": counters["lookup_sec_insert"] += 1
        elif act_sec == "update_name": counters["lookup_sec_update"] += 1

    if dept is not None:
        dep_nombre = (dept.get("nombre", "") or "").strip()
        act_dep = ensure_departamento_cur(cur, dep_codigo_norm, dep_nombre)
        if act_dep == "insert": counters["lookup_dep_insert"] += 1
        elif act_dep == "update_name": counters["lookup_dep_update"] += 1

    # Texto base
    cuerpo_base = _compose_text(item, seccion, dept, epigrafe)

    # URLs útiles del feed
    url_pdf = (item.findtext("url_pdf", "") or "").strip()
    url_html = (item.findtext("url_html", "") or "").strip()
    url_xml = (item.findtext("url_xml", "") or "").strip()  # por ahora no usado en enriquecido

    # Enriquecer si aporta valor
    try:
        cuerpo_final, enriched = enrich_boe_text(
            identificador=identificador,
            url_html=url_html or None,
            url_txt_candidate=url_html or None,
            url_pdf=url_pdf or None,
            base_text=cuerpo_base,
            min_gain_chars=800,  # configurable
        )
        if enriched:
            logger.info(f"🧩 Enriquecido ({identificador}) usando contenido externo")
    except Exception as e:
        logger.warning(f"⚠️ Enriquecido falló ({identificador}): {e}")
        cuerpo_final = cuerpo_base

    # Fallback si quedó vacío
    if _emptyish(cuerpo_final):
        meta = " | ".join(filter(None, [
            seccion.get("nombre", "") if seccion is not None else "",
            dept.get("nombre", "") if dept is not None else "",
            epigrafe.get("nombre", "") if epigrafe is not None else "",
            (item.findtext("control", "") or "").strip(),
        ]))
        cuerpo_final = (titulo + ("\n\n" + meta if meta else "")).strip()

    # OpenAI (no bloqueante: si falla, seguimos insertando el ítem)
    try:
        titulo_resumen, resumen_json, impacto_json = get_openai_responses(titulo, cuerpo_final)
    except Exception as e:
        logger.error(f"❌ OpenAI error en '{identificador}': {e}")
        counters["fallos_openai"] += 1
        titulo_resumen, resumen_json, impacto_json = "", "", ""

    resumen_comp = None if _emptyish(resumen_json) else compress_json(resumen_json)
    impacto_comp = None if _emptyish(impacto_json) else compress_json(impacto_json)

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
        ON CONFLICT (identificador) DO NOTHING
        """,
        (
            identificador,
            titulo,
            titulo_resumen_final,
            resumen_comp,
            impacto_comp,
            url_pdf,
            url_html,
            url_xml,
            sec_codigo_norm if seccion else "",
            dep_codigo_norm if dept else "",
            (epigrafe.get("nombre", "") if epigrafe else "").strip(),
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
                sec_nombre = (seccion.get("nombre", "") or "").strip()
                clase_item = clasificar_item(sec_nombre)

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
