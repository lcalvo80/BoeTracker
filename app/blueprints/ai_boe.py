# app/blueprints/ai_boe.py
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from app.services.boe_text_extractor import extract_boe_text
from app.services.openai_service import (
    generate_title,
    generate_summary,
    generate_impact,
)

_LOG = logging.getLogger(__name__)

bp = Blueprint("ai_boe", __name__, url_prefix="/api/ai")


def _extract_or_400(payload: Dict[str, Any]):
    """
    Extrae campos del payload y obtiene SIEMPRE el texto del PDF.
    Reglas:
    - url_pdf es obligatorio (no inventamos sin fuente).
    - identificador/titulo sólo se usan como hints y logs.
    """
    ident = (payload or {}).get("identificador") or ""
    url_pdf = (payload or {}).get("url_pdf") or ""
    titulo = (payload or {}).get("titulo") or ""

    if not url_pdf:
        return None, "Se requiere 'url_pdf' para poder descargar el PDF del BOE."

    try:
        text = extract_boe_text(identificador=ident, url_pdf=url_pdf)
    except Exception as e:
        _LOG.exception("Fallo extrayendo texto del PDF (%s)", ident or url_pdf)
        return None, f"No se pudo extraer el texto del PDF: {type(e).__name__}"

    if not text:
        return None, "El PDF no contiene texto utilizable."

    return {
        "text": text,
        "titulo": titulo,
        "identificador": ident,
        "url_pdf": url_pdf,
    }, None


@bp.route("/title", methods=["POST"])
def ai_title():
    """
    Genera un título corto (≤10 palabras) basado en el contenido del PDF del BOE.
    """
    payload = request.get_json(force=True, silent=True) or {}
    extracted, err = _extract_or_400(payload)
    if err:
        return jsonify({"error": err}), 400

    try:
        title = generate_title(title_hint=extracted["titulo"], content=extracted["text"])
        # saneo mínimo
        title = title.strip(' "\'.,;:-')
        return jsonify({"title": title})
    except Exception as e:
        _LOG.exception("OpenAI title error")
        return jsonify({"title": "", "error": f"Fallo al generar el título: {type(e).__name__}"}), 502


@bp.route("/summary", methods=["POST"])
def ai_summary():
    """
    Genera el resumen estructurado (boe_resumen) usando SIEMPRE el PDF.
    """
    payload = request.get_json(force=True, silent=True) or {}
    extracted, err = _extract_or_400(payload)
    if err:
        # Entregamos forma válida aunque vacía para que el pipeline no rompa
        return jsonify(
            {
                "summary": "",
                "key_changes": [],
                "key_dates_events": [],
                "conclusion": f"Error: {err}",
            }
        ), 400

    try:
        resumen_obj = generate_summary(content=extracted["text"], title_hint=extracted["titulo"])
        return jsonify(resumen_obj)
    except Exception as e:
        _LOG.exception("OpenAI summary error")
        return jsonify(
            {
                "summary": "Error de generación",
                "key_changes": [],
                "key_dates_events": [],
                "conclusion": f"Fallo al generar el resumen: {type(e).__name__}",
            }
        ), 502


@bp.route("/impact", methods=["POST"])
def ai_impact():
    """
    Genera el informe de impacto (boe_impacto) usando SIEMPRE el PDF.
    """
    payload = request.get_json(force=True, silent=True) or {}
    extracted, err = _extract_or_400(payload)
    if err:
        return jsonify(
            {
                "afectados": [],
                "cambios_operativos": [],
                "riesgos_potenciales": [],
                "beneficios_previstos": [],
                "recomendaciones": [],
                "error": err,
            }
        ), 400

    try:
        impacto_obj = generate_impact(content=extracted["text"], title_hint=extracted["titulo"])
        return jsonify(impacto_obj)
    except Exception as e:
        _LOG.exception("OpenAI impact error")
        return jsonify(
            {
                "afectados": [],
                "cambios_operativos": [],
                "riesgos_potenciales": [],
                "beneficios_previstos": [],
                "recomendaciones": [],
                "error": f"Fallo al generar el impacto: {type(e).__name__}",
            }
        ), 502
