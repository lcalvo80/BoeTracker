# app/services/daily_summary_ai.py
from __future__ import annotations

"""IA para Resumen Diario por sección.

Requisitos:
- Responder en español.
- Salida JSON estricta (json_schema) para facilitar persistencia + UI.
- NO inventar: la fuente de verdad es el listado de títulos/identificadores y conteos.

Este módulo está pensado para ejecución batch (GitHub Actions / cron) y no
depende de Clerk/Stripe ni de endpoints autenticados.
"""

import os
from datetime import date
from typing import Any, Dict, List, Tuple

from app.services.openai_service import _make_client, _json_schema_completion_with_retry
from app.services.boe_daily_summary import SectionInput, SectionItem


PROMPT_VERSION = int(os.getenv("DAILY_SUMMARY_PROMPT_VERSION", "1"))
MODEL_DAILY = (
    os.getenv("OPENAI_MODEL_DAILY_SUMMARY")
    or os.getenv("OPENAI_MODEL_SUMMARY")
    or os.getenv("OPENAI_MODEL")
    or "gpt-4o"
).strip()


def _schema() -> Dict[str, Any]:
    return {
        "name": "boe_daily_section_summary",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string", "maxLength": 900},
                "highlights": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "string", "maxLength": 200},
                },
                "top_items": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "identificador": {"type": "string", "maxLength": 64},
                            "titulo": {"type": "string", "maxLength": 220},
                        },
                        "required": ["identificador", "titulo"],
                    },
                },
            },
            "required": ["summary", "highlights", "top_items"],
        },
    }


def _format_dept_counts(counts: List[Tuple[str, int]]) -> str:
    if not counts:
        return "(sin datos)"
    lines = []
    for dept, n in counts:
        dept_s = str(dept or "").strip() or "(sin departamento)"
        lines.append(f"- {dept_s}: {int(n)}")
    return "\n".join(lines)


def _format_sample_items(items: List[SectionItem]) -> str:
    if not items:
        return "(sin items)"
    lines = []
    for it in items:
        dept = (it.departamento or "").strip()
        ep = (it.epigrafe or "").strip()
        prefix_parts = [p for p in [dept, ep] if p]
        prefix = f"[{' / '.join(prefix_parts)}] " if prefix_parts else ""
        lines.append(f"- {prefix}{it.titulo} ({it.identificador})")
    return "\n".join(lines)


def generate_section_summary(*, fecha_publicacion: date, section: SectionInput) -> Dict[str, Any]:
    """Genera el resumen IA de una sección."""
    client = _make_client()
    if client is None:
        raise RuntimeError("OPENAI_API_KEY no disponible o cliente OpenAI no inicializable")

    dept_counts_txt = _format_dept_counts(section.dept_counts)
    sample_items_txt = _format_sample_items(section.sample_items)

    system = (
        "Eres un asistente editorial que redacta un resumen diario del BOE por secciones. "
        "Debes responder SOLO con JSON válido conforme al schema. "
        "NO inventes hechos: la fuente de verdad es únicamente el listado y los conteos que se te proporcionan."
    )

    user = f"""=== CONTEXTO ===
Fecha de publicación: {fecha_publicacion.isoformat()}
Sección: {section.seccion_codigo} — {section.seccion_nombre}
Total de entradas en la sección: {section.total_entradas}

=== DISTRIBUCIÓN POR DEPARTAMENTO (TOP) ===
{dept_counts_txt}

=== MUESTRA DE TÍTULOS (FUENTE DE VERDAD) ===
{sample_items_txt}

=== INSTRUCCIONES (DURO) ===
- Redacta "summary" en 2–4 frases, español claro y orientado a empresa/compliance.
- Si la sección es masiva (p. ej. oposiciones/anuncios), resume en términos generales (temas, tipos de actos), sin intentar enumerar todo.
- "highlights": 3–6 bullets (frases cortas) con lo más relevante que SÍ se deduce de los títulos.
- "top_items": 3–6 items destacados, elige SOLO de la MUESTRA. Copia literalmente identificador y título.
- Si no hay suficiente información, sé conservador: evita afirmaciones específicas.
"""

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    obj = _json_schema_completion_with_retry(
        client,
        messages=messages,
        schema=_schema(),
        model=MODEL_DAILY,
        max_tokens=800,
        temperature=0.2,
        seed=7,
    )

    summary = str(obj.get("summary") or "").strip()
    highlights = obj.get("highlights") if isinstance(obj.get("highlights"), list) else []
    top_items = obj.get("top_items") if isinstance(obj.get("top_items"), list) else []

    highlights_out: List[str] = []
    for x in highlights:
        s = str(x or "").strip()
        if s:
            highlights_out.append(s[:200])
        if len(highlights_out) >= 6:
            break

    top_out: List[Dict[str, str]] = []
    for it in top_items:
        if not isinstance(it, dict):
            continue
        ident = str(it.get("identificador") or "").strip()
        titulo = str(it.get("titulo") or "").strip()
        if not ident or not titulo:
            continue
        top_out.append({"identificador": ident[:64], "titulo": titulo[:220]})
        if len(top_out) >= 6:
            break

    return {
        "summary": summary[:900],
        "highlights": highlights_out,
        "top_items": top_out,
        "ai_model": MODEL_DAILY,
        "ai_prompt_version": PROMPT_VERSION,
    }
