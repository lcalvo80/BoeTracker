# app/services/daily_summary_ai.py
from __future__ import annotations

"""IA para Resumen Diario por sección.

Mejoras v3 (hardening):
- Evitar textos/títulos cortados o demasiado largos (word-safe truncation + "…").
- Anti-hallucination: el modelo NO devuelve títulos. Solo devuelve IDs (top_item_ids).
- Regla clave: top_items[].titulo SIEMPRE se reconstruye desde la MUESTRA (source of truth).
- Highlights: limpieza + dedupe + fallback conservador si el modelo devuelve pocos.
- Prompt más editorial y escaneable (empresa/compliance), sin inventar.

Nota:
- El output final expuesto por la API mantiene el shape anterior:
  {summary, highlights, top_items[{identificador,titulo}], ai_model, ai_prompt_version}
"""

import os
import re
import json
from datetime import date
from typing import Any, Dict, List, Tuple

from app.services.openai_service import _make_client, _json_schema_completion_with_retry
from app.services.boe_daily_summary import SectionInput, SectionItem


PROMPT_VERSION = int(os.getenv("DAILY_SUMMARY_PROMPT_VERSION", "3"))

MODEL_DAILY = (
    os.getenv("OPENAI_MODEL_DAILY_SUMMARY")
    or os.getenv("OPENAI_MODEL_SUMMARY")
    or os.getenv("OPENAI_MODEL")
    or "gpt-4o"
).strip()

# Límites editoriales (display)
SUMMARY_MAX = int(os.getenv("DAILY_SUMMARY_SUMMARY_MAX", "700"))  # 👈 más corto = mejor lectura
HIGHLIGHT_MAX = int(os.getenv("DAILY_SUMMARY_HIGHLIGHT_MAX", "190"))
TITLE_MAX = int(os.getenv("DAILY_SUMMARY_TITLE_MAX", "220"))  # 👈 más corto en UI; full title existe en fuente

# Reglas de cantidad (UI)
HIGHLIGHTS_MIN = int(os.getenv("DAILY_SUMMARY_HIGHLIGHTS_MIN", "3"))
TOP_ITEMS_MIN = int(os.getenv("DAILY_SUMMARY_TOP_ITEMS_MIN", "3"))
TOP_ITEMS_MAX = int(os.getenv("DAILY_SUMMARY_TOP_ITEMS_MAX", "6"))
SAMPLE_MAX_JSON = int(os.getenv("DAILY_SUMMARY_SAMPLE_MAX_JSON", "40"))
PROMPT_TITLE_MAX = int(os.getenv("DAILY_SUMMARY_PROMPT_TITLE_MAX", "240"))  # para reducir tokens del prompt

_WS_RE = re.compile(r"\s+")
_BULLET_PREFIX_RE = re.compile(r"^\s*([-*•]+)\s+")
_TRAIL_PUNCT_RE = re.compile(r"[ ,;:\-]+$")


def _schema() -> Dict[str, Any]:
    """Schema que usa OpenAI.

    Importante:
    - El modelo NO devuelve títulos (evita truncados/hallucination).
    - Solo devuelve IDs seleccionados.
    """
    return {
        "name": "boe_daily_section_summary_v3",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string", "maxLength": max(200, SUMMARY_MAX)},
                "highlights": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "string", "maxLength": max(80, HIGHLIGHT_MAX)},
                },
                "top_item_ids": {
                    "type": "array",
                    "maxItems": TOP_ITEMS_MAX,
                    "items": {"type": "string", "maxLength": 64},
                },
            },
            "required": ["summary", "highlights", "top_item_ids"],
        },
    }


def _collapse_ws(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").strip())


def _strip_bullet_prefix(s: str) -> str:
    s = _collapse_ws(s)
    return _BULLET_PREFIX_RE.sub("", s).strip()


def _truncate_words(s: str, max_len: int, *, ellipsis: str = "…") -> str:
    """Recorta a max_len sin partir palabras. Si recorta, añade ellipsis."""
    s = _collapse_ws(s)
    if not s:
        return ""
    if len(s) <= max_len:
        return s

    cut = s[:max_len].rstrip()

    # Si cortamos en mitad de palabra (alnum-alnum), retroceder al último espacio.
    if max_len < len(s):
        left = cut[-1] if cut else ""
        right = s[max_len] if max_len < len(s) else ""
        if left and right and left.isalnum() and right.isalnum():
            if " " in cut:
                cut = cut.rsplit(" ", 1)[0].rstrip()

    # Evitar terminar con separadores feos
    cut = _TRAIL_PUNCT_RE.sub("", cut)

    # Fallback si nos quedamos demasiado cortos
    if len(cut) < 12:
        cut = _TRAIL_PUNCT_RE.sub("", s[:max_len].rstrip())

    return cut + ellipsis


def _dedupe_keep_order(strings: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for s in strings:
        s2 = _collapse_ws(s)
        key = s2.lower()
        if not s2 or key in seen:
            continue
        seen.add(key)
        out.append(s2)
    return out


def _format_dept_counts(counts: List[Tuple[str, int]]) -> str:
    if not counts:
        return "(sin datos)"
    lines = []
    for dept, n in counts:
        dept_s = _collapse_ws(str(dept or "")) or "(sin departamento)"
        lines.append(f"- {dept_s}: {int(n)}")
    return "\n".join(lines)


def _build_sample_title_map(items: List[SectionItem]) -> Dict[str, str]:
    """Mapa ident -> título fuente (verdad)."""
    m: Dict[str, str] = {}
    for it in (items or []):
        ident = _collapse_ws(it.identificador or "")
        title = _collapse_ws(it.titulo or "")
        if ident and title and ident not in m:
            m[ident] = title
    return m


def _sample_items_json(items: List[SectionItem], *, max_items: int = SAMPLE_MAX_JSON) -> List[Dict[str, str]]:
    """Muestra compacta para el prompt (reduce tokens).

    - titulo en prompt: truncado word-safe para no meter tochos.
    - El título FULL se mantiene en sample_titles (source of truth).
    """
    out: List[Dict[str, str]] = []
    for it in (items or [])[: max(1, int(max_items))]:
        titulo_prompt = _truncate_words(_collapse_ws(it.titulo or ""), PROMPT_TITLE_MAX)
        out.append(
            {
                "identificador": _collapse_ws(it.identificador or ""),
                "titulo": titulo_prompt,
                "departamento": _collapse_ws(it.departamento or ""),
                "epigrafe": _collapse_ws(it.epigrafe or ""),
            }
        )
    return out


def _fallback_highlights(section: SectionInput) -> List[str]:
    """Highlights conservadores deducibles (sin inventar) si el modelo devuelve pocos."""
    out: List[str] = []
    total = int(section.total_entradas or 0)
    if total > 0:
        out.append(f"Se publican {total} entradas en esta sección.")
    if section.dept_counts:
        top = [str(d or "").strip() for d, _ in section.dept_counts[:3] if str(d or "").strip()]
        if top:
            out.append("Mayor actividad por departamento: " + ", ".join(top) + ".")
    code = (section.seccion_codigo or "").upper()
    if code in {"2B", "5A", "5B"}:
        out.append("Revisa si hay convocatorias/anuncios que afecten a tu actividad o licitaciones de interés.")
    return out


def generate_section_summary(*, fecha_publicacion: date, section: SectionInput) -> Dict[str, Any]:
    """Genera el resumen IA de una sección."""
    client = _make_client()
    if client is None:
        raise RuntimeError("OPENAI_API_KEY no disponible o cliente OpenAI no inicializable")

    dept_counts_txt = _format_dept_counts(section.dept_counts)

    # Source of truth (FULL titles)
    sample_titles = _build_sample_title_map(section.sample_items)

    # Prompt sample (compacta)
    sample_json = _sample_items_json(section.sample_items)
    sample_id_list = [x["identificador"] for x in sample_json if x.get("identificador")]

    system = (
        "Eres un asistente editorial que redacta un resumen diario del BOE por secciones. "
        "Debes responder SOLO con JSON válido conforme al schema. "
        "NO inventes: la fuente de verdad son ÚNICAMENTE los conteos y la MUESTRA de títulos/identificadores."
    )

    user = f"""=== CONTEXTO ===
Fecha de publicación: {fecha_publicacion.isoformat()}
Sección: {section.seccion_codigo} — {section.seccion_nombre}
Total de entradas en la sección: {section.total_entradas}

=== DISTRIBUCIÓN POR DEPARTAMENTO (TOP) ===
{dept_counts_txt}

=== MUESTRA (JSON) — FUENTE DE VERDAD ===
{json.dumps(sample_json, ensure_ascii=False)}

=== INSTRUCCIONES (DURO) ===
- summary: 2–3 frases, español claro, escaneable y orientado a empresa/compliance.
- No uses "hoy". No repitas la fecha si no aporta.
- Si la sección es masiva (oposiciones/anuncios), describe tipos de actos/temas de forma general.
- highlights: 3–6 bullets útiles y conservadores. No "opines" ni atribuyas relevancia profesional específica si no se deduce del título.
  Ejemplos válidos: "Se publican convocatorias y listas de admitidos/excluidos." / "Hay anuncios de licitación y formalización de contratos."
- top_item_ids: devuelve 3–6 identificadores destacados, escogidos SOLO de esta lista (exactos):
  {json.dumps(sample_id_list, ensure_ascii=False)}
- No inventes fechas, plazos o requisitos salvo que se vean claramente en un título de la muestra.
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
        max_tokens=650,   # output más pequeño
        temperature=0.2,
        seed=7,
    )

    # ─────────────────────────────
    # Post-procesado editorial (robusto)
    # ─────────────────────────────

    # Summary (word-safe + límite)
    summary_in = _collapse_ws(str(obj.get("summary") or ""))
    summary_out = _truncate_words(summary_in, SUMMARY_MAX) if summary_in else ""

    # Highlights: limpieza + truncado + dedupe
    highlights_in = obj.get("highlights") if isinstance(obj.get("highlights"), list) else []
    highlights_raw: List[str] = []
    for x in highlights_in:
        s = _strip_bullet_prefix(str(x or ""))
        if not s:
            continue
        highlights_raw.append(_truncate_words(s, HIGHLIGHT_MAX))

    highlights_out = _dedupe_keep_order([h for h in highlights_raw if h])

    # Fallback conservador si faltan
    if len(highlights_out) < max(1, HIGHLIGHTS_MIN):
        highlights_out = _dedupe_keep_order(highlights_out + _fallback_highlights(section))

    highlights_out = highlights_out[:6]

    # Top items: modelo devuelve SOLO IDs. Nosotros reconstruimos títulos desde la MUESTRA (truth).
    top_ids_in = obj.get("top_item_ids") if isinstance(obj.get("top_item_ids"), list) else []
    top_out: List[Dict[str, str]] = []
    used: set[str] = set()

    def add_top_id(ident: str):
        ident2 = _collapse_ws(ident or "")
        if not ident2 or ident2 in used:
            return
        if ident2 not in sample_titles:
            return
        used.add(ident2)
        title_full = sample_titles[ident2]
        title_out = _truncate_words(title_full, TITLE_MAX)
        top_out.append({"identificador": ident2[:64], "titulo": title_out})

    # 1) IDs sugeridos por el modelo (si son válidos)
    for ident in top_ids_in:
        add_top_id(str(ident or ""))
        if len(top_out) >= TOP_ITEMS_MAX:
            break

    # 2) Relleno determinista si faltan mínimos
    if len(top_out) < max(1, TOP_ITEMS_MIN):
        for ident in sample_id_list:
            add_top_id(ident)
            if len(top_out) >= max(1, TOP_ITEMS_MIN):
                break

    top_out = top_out[:TOP_ITEMS_MAX]

    return {
        "summary": summary_out,
        "highlights": highlights_out,
        "top_items": top_out,
        "ai_model": MODEL_DAILY,
        "ai_prompt_version": PROMPT_VERSION,
    }
