# app/services/daily_summary_ai.py
from __future__ import annotations

"""IA para Resumen Diario por sección.

Mejoras v2:
- Evitar textos/títulos cortados a mitad de palabra (word-safe truncation + "…").
- Top-items: validación estricta contra la MUESTRA (no se permiten IDs fuera de la muestra).
- Prompt más "editorial" y orientado a lectura (empresa/compliance), sin inventar.

Regla clave (anti-cortes y anti-hallucination):
- Nunca confiamos en top_items[].titulo del modelo.
- Siempre reconstruimos el título desde la MUESTRA (source of truth) usando el identificador.
"""

import os
import re
import json
from datetime import date
from typing import Any, Dict, List, Tuple

from app.services.openai_service import _make_client, _json_schema_completion_with_retry
from app.services.boe_daily_summary import SectionInput, SectionItem


PROMPT_VERSION = int(os.getenv("DAILY_SUMMARY_PROMPT_VERSION", "2"))

MODEL_DAILY = (
    os.getenv("OPENAI_MODEL_DAILY_SUMMARY")
    or os.getenv("OPENAI_MODEL_SUMMARY")
    or os.getenv("OPENAI_MODEL")
    or "gpt-4o"
).strip()

# Límites editoriales (display)
SUMMARY_MAX = int(os.getenv("DAILY_SUMMARY_SUMMARY_MAX", "900"))
HIGHLIGHT_MAX = int(os.getenv("DAILY_SUMMARY_HIGHLIGHT_MAX", "200"))
TITLE_MAX = int(os.getenv("DAILY_SUMMARY_TITLE_MAX", "260"))  # antes 220

# Reglas de cantidad (UI)
HIGHLIGHTS_MIN = int(os.getenv("DAILY_SUMMARY_HIGHLIGHTS_MIN", "3"))
TOP_ITEMS_MIN = int(os.getenv("DAILY_SUMMARY_TOP_ITEMS_MIN", "3"))
TOP_ITEMS_MAX = int(os.getenv("DAILY_SUMMARY_TOP_ITEMS_MAX", "6"))
SAMPLE_MAX_JSON = int(os.getenv("DAILY_SUMMARY_SAMPLE_MAX_JSON", "40"))

_WS_RE = re.compile(r"\s+")
_BULLET_PREFIX_RE = re.compile(r"^\s*([-*•]+)\s+")
_TRAIL_PUNCT_RE = re.compile(r"[ ,;:\-]+$")


def _schema() -> Dict[str, Any]:
    # Importante: maxLength del schema >= límites de salida para evitar truncados feos “por schema”.
    return {
        "name": "boe_daily_section_summary",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string", "maxLength": SUMMARY_MAX},
                "highlights": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "string", "maxLength": HIGHLIGHT_MAX},
                },
                "top_items": {
                    "type": "array",
                    "maxItems": TOP_ITEMS_MAX,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "identificador": {"type": "string", "maxLength": 64},
                            "titulo": {"type": "string", "maxLength": TITLE_MAX},
                        },
                        "required": ["identificador", "titulo"],
                    },
                },
            },
            "required": ["summary", "highlights", "top_items"],
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

    # Cortamos en bruto
    cut = s[:max_len].rstrip()

    # Si hemos cortado en mitad de palabra (alnum-alnum), retrocede al último espacio.
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
        key = _collapse_ws(s).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _format_dept_counts(counts: List[Tuple[str, int]]) -> str:
    if not counts:
        return "(sin datos)"
    lines = []
    for dept, n in counts:
        dept_s = _collapse_ws(str(dept or "")) or "(sin departamento)"
        lines.append(f"- {dept_s}: {int(n)}")
    return "\n".join(lines)


def _sample_items_json(items: List[SectionItem], *, max_items: int = SAMPLE_MAX_JSON) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for it in (items or [])[: max(1, int(max_items))]:
        out.append(
            {
                "identificador": _collapse_ws(it.identificador or ""),
                "titulo": _collapse_ws(it.titulo or ""),
                "departamento": _collapse_ws(it.departamento or ""),
                "epigrafe": _collapse_ws(it.epigrafe or ""),
            }
        )
    return out


def _build_sample_title_map(items: List[SectionItem]) -> Dict[str, str]:
    """Mapa ident -> título fuente (verdad)."""
    m: Dict[str, str] = {}
    for it in (items or []):
        ident = _collapse_ws(it.identificador or "")
        title = _collapse_ws(it.titulo or "")
        if ident and title and ident not in m:
            m[ident] = title
    return m


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
    # Para secciones masivas: frase genérica (no inventa)
    code = (section.seccion_codigo or "").upper()
    if code in {"2B", "5A", "5B"}:
        out.append("Conviene revisar oportunidades, convocatorias o anuncios relevantes para tu actividad.")
    return out


def generate_section_summary(*, fecha_publicacion: date, section: SectionInput) -> Dict[str, Any]:
    """Genera el resumen IA de una sección."""
    client = _make_client()
    if client is None:
        raise RuntimeError("OPENAI_API_KEY no disponible o cliente OpenAI no inicializable")

    dept_counts_txt = _format_dept_counts(section.dept_counts)
    sample_json = _sample_items_json(section.sample_items)
    sample_titles = _build_sample_title_map(section.sample_items)
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
- summary: 2–4 frases, español claro y "escaneable", orientado a empresa/compliance.
- Evita frases plantilla tipo "La sección incluye..." si puedes abrir con lo relevante.
- Si la sección es masiva (oposiciones/anuncios), describe tipos de actos/temas, sin intentar enumerar todo.
- highlights: 3–6 bullets útiles (qué es, por qué importa, y/o acción sugerida) SIN inventar.
- top_items: 3–6 destacados. Debes elegir SOLO identificadores de esta lista:
  {json.dumps(sample_id_list, ensure_ascii=False)}
- top_items[].identificador: copia EXACTO.
- top_items[].titulo: usa el título de la muestra. Si es largo, recórtalo SIN partir palabras y con "…".
- NO afirmes cosas específicas que no estén en títulos/epígrafes (ej. fechas/plazos) salvo que aparezcan claramente.
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
        max_tokens=900,
        temperature=0.2,
        seed=7,
    )

    # ─────────────────────────────
    # Post-procesado editorial (robusto)
    # ─────────────────────────────

    # Summary
    summary_in = _collapse_ws(str(obj.get("summary") or ""))
    summary_out = _truncate_words(summary_in, SUMMARY_MAX) if summary_in else ""

    # Highlights (limpieza + dedupe + word-safe)
    highlights_in = obj.get("highlights") if isinstance(obj.get("highlights"), list) else []
    highlights_raw: List[str] = []
    for x in highlights_in:
        s = _strip_bullet_prefix(str(x or ""))
        if not s:
            continue
        # truncado word-safe (aunque el schema ya limita)
        highlights_raw.append(_truncate_words(s, HIGHLIGHT_MAX))

    highlights_out = _dedupe_keep_order([h for h in highlights_raw if h])

    # Si el modelo devuelve pocos highlights, añadimos fallbacks conservadores
    if len(highlights_out) < max(1, HIGHLIGHTS_MIN):
        highlights_out = _dedupe_keep_order(highlights_out + _fallback_highlights(section))

    # recortar a max 6
    highlights_out = highlights_out[:6]

    # Top items: validación estricta por ID + título SIEMPRE desde muestra
    top_items_in = obj.get("top_items") if isinstance(obj.get("top_items"), list) else []
    top_out: List[Dict[str, str]] = []
    used_ids: set[str] = set()

    def add_top(ident: str):
        ident = _collapse_ws(ident or "")
        if not ident or ident in used_ids:
            return
        if ident not in sample_titles:
            return
        used_ids.add(ident)
        title_src = sample_titles[ident]
        title_out = _truncate_words(title_src, TITLE_MAX)
        top_out.append({"identificador": ident[:64], "titulo": title_out})

    # 1) Lo que sugiera el modelo (solo IDs válidos)
    for it in top_items_in:
        if not isinstance(it, dict):
            continue
        ident = str(it.get("identificador") or "")
        add_top(ident)
        if len(top_out) >= TOP_ITEMS_MAX:
            break

    # 2) Si faltan, rellenamos determinísticamente desde la muestra (source of truth)
    if len(top_out) < max(1, TOP_ITEMS_MIN):
        for ident in sample_id_list:
            add_top(ident)
            if len(top_out) >= max(1, TOP_ITEMS_MIN):
                break

    # 3) Si aún quedan menos de TOP_ITEMS_MIN (muestra pequeña), dejamos lo que haya (no inventamos).
    top_out = top_out[:TOP_ITEMS_MAX]

    return {
        "summary": summary_out,
        "highlights": highlights_out,
        "top_items": top_out,
        "ai_model": MODEL_DAILY,
        "ai_prompt_version": PROMPT_VERSION,
    }
