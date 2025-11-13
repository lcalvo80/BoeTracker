# app/blueprints/ai_boe.py
from __future__ import annotations

import json
import logging
import os

from flask import Blueprint, jsonify, request

from app.services.ai_prompts import (
    SYSTEM_JSON_STRICT,
    SYSTEM_TITLE,
    build_impact_prompt,
    build_summary_prompt,
    build_title_prompt,
    detect_has_dates,
    make_impact_schema,
    make_summary_schema,
)
from app.services.boe_text_extractor import extract_boe_text
from app.services.openai_client import (
    OPENAI_MODEL_IMPACT,
    OPENAI_MODEL_SUMMARY,
    OPENAI_MODEL_TITLE,
    OPENAI_SEED,
    OPENAI_TIMEOUT,
    client,
)

_LOG = logging.getLogger(__name__)

bp = Blueprint("ai_boe", __name__, url_prefix="/api/ai")


def _extract_or_400(payload):
    """
    Extrae campos básicos del payload y obtiene SIEMPRE texto del PDF.

    Reglas:
    - url_pdf es obligatorio.
    - identificador se usa para logs (si viene).
    """
    ident = (payload or {}).get("identificador") or ""
    url_pdf = (payload or {}).get("url_pdf") or ""
    titulo = (payload or {}).get("titulo") or ""
    seccion = (payload or {}).get("seccion") or ""
    departamento = (payload or {}).get("departamento") or ""
    ctx_dump = (payload or {}).get("ctx_dump") or ""

    if not url_pdf:
        return None, "Se requiere 'url_pdf' para poder descargar el PDF del BOE."

    text = extract_boe_text(identificador=ident, url_pdf=url_pdf)

    return {
        "text": text,
        "titulo": titulo,
        "seccion": seccion,
        "departamento": departamento,
        "ctx_dump": ctx_dump,
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

    notif_text = extracted["text"]
    if not notif_text:
        # No inventamos título si no hemos podido extraer el texto del PDF
        return jsonify({"title": "", "warning": "No se pudo extraer el texto del PDF."})

    prompt = build_title_prompt(
        notif_text=notif_text,
        ctx_dump=extracted["ctx_dump"],
        titulo=extracted["titulo"],
        seccion=extracted["seccion"],
        departamento=extracted["departamento"],
    )

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL_TITLE,
            temperature=0.1,
            max_tokens=16,
            seed=OPENAI_SEED,
            messages=[
                {"role": "system", "content": SYSTEM_TITLE},
                {"role": "user", "content": prompt},
            ],
            timeout=OPENAI_TIMEOUT,
        )
        title = (resp.choices[0].message.content or "").strip()
        # saneo mínimo: quitar comillas y signos si se colaran
        title = title.strip(' "\'.,;:-')
        return jsonify({"title": title})
    except Exception as e:
        _LOG.exception("OpenAI title error")
        return (
            jsonify(
                {
                    "title": "",
                    "error": f"Fallo al generar el título: {type(e).__name__}",
                }
            ),
            502,
        )


@bp.route("/summary", methods=["POST"])
def ai_summary():
    """
    Genera el resumen estructurado (boe_resumen) usando SIEMPRE el PDF.
    """
    payload = request.get_json(force=True, silent=True) or {}
    extracted, err = _extract_or_400(payload)
    if err:
        return jsonify({"error": err}), 400

    notif_text = extracted["text"]
    if not notif_text:
        return jsonify(
            {
                "summary": "No hay contenido suficiente para resumir.",
                "key_changes": [],
                "key_dates_events": [],
                "conclusion": "No se pudo extraer el texto del anuncio desde el PDF.",
            }
        )

    has_dates = detect_has_dates(notif_text)
    schema = make_summary_schema(has_dates)

    prompt = build_summary_prompt(
        notif_text=notif_text,
        ctx_dump=extracted["ctx_dump"],
        titulo=extracted["titulo"],
        seccion=extracted["seccion"],
        departamento=extracted["departamento"],
    )

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL_SUMMARY,
            temperature=0.1,
            max_tokens=900,
            seed=OPENAI_SEED,
            response_format=schema,
            messages=[
                {"role": "system", "content": SYSTEM_JSON_STRICT},
                {"role": "user", "content": prompt},
            ],
            timeout=OPENAI_TIMEOUT,
        )
        data = json.loads(resp.choices[0].message.content)

        # Si el texto era largo y la respuesta es "no se han detectado..." → marcar como sospechosa
        if (
            len(notif_text) > 1000
            and (
                not data.get("summary")
                or data["summary"].strip().lower().startswith("no se han detectado")
            )
        ):
            data["conclusion"] = (
                "Revisión necesaria: el modelo devolvió un resumen vacío pese a haber contenido suficiente."
            )

        return jsonify(data)
    except Exception as e:
        _LOG.exception("OpenAI summary error")
        return (
            jsonify(
                {
                    "summary": "Error de generación",
                    "key_changes": [],
                    "key_dates_events": [],
                    "conclusion": f"Fallo al generar el resumen: {type(e).__name__}",
                }
            ),
            502,
        )


@bp.route("/impact", methods=["POST"])
def ai_impact():
    """
    Genera el informe de impacto estructurado (boe_impacto) usando SIEMPRE el PDF.
    """
    payload = request.get_json(force=True, silent=True) or {}
    extracted, err = _extract_or_400(payload)
    if err:
        return jsonify({"error": err}), 400

    notif_text = extracted["text"]
    if not notif_text:
        return jsonify(
            {
                "afectados": [],
                "cambios_operativos": [],
                "riesgos_potenciales": [],
                "beneficios_previstos": [],
                "recomendaciones": [],
                "warning": "No se pudo extraer el texto del anuncio desde el PDF.",
            }
        )

    schema = make_impact_schema()
    prompt = build_impact_prompt(
        notif_text=notif_text,
        ctx_dump=extracted["ctx_dump"],
        titulo=extracted["titulo"],
        seccion=extracted["seccion"],
        departamento=extracted["departamento"],
    )

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL_IMPACT,
            temperature=0.1,
            max_tokens=900,
            seed=OPENAI_SEED,
            response_format=schema,
            messages=[
                {"role": "system", "content": SYSTEM_JSON_STRICT},
                {"role": "user", "content": prompt},
            ],
            timeout=OPENAI_TIMEOUT,
        )
        data = json.loads(resp.choices[0].message.content)
        return jsonify(data)
    except Exception as e:
        _LOG.exception("OpenAI impact error")
        return (
            jsonify(
                {
                    "afectados": [],
                    "cambios_operativos": [],
                    "riesgos_potenciales": [],
                    "beneficios_previstos": [],
                    "recomendaciones": [],
                    "error": f"Fallo al generar el impacto: {type(e).__name__}",
                }
            ),
            502,
        )
