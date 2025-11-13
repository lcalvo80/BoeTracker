# app/services/ai_prompts.py
from __future__ import annotations

import re
from typing import Any, Dict

# Mensajes de sistema
SYSTEM_JSON_STRICT = (
    "Responde SOLO con JSON válido conforme al schema. Nada fuera del JSON. "
    "Usa SOLO el CONTENIDO."
)
SYSTEM_TITLE = (
    "Eres un asistente que redacta títulos del BOE. Usa EXCLUSIVAMENTE el CONTENIDO como fuente de verdad."
)


# ───────────────── Heurísticas ─────────────────


def detect_has_dates(text: str) -> bool:
    """
    Replica la heurística de fechas de Postman para decidir MIN_ITEMS_DATES.
    """
    rx_date = re.compile(
        r"\b\d{1,2}\s+de\s+[a-záéíóú]+(?:\s+de\s+\d{4})?\b", re.IGNORECASE
    )  # 21 de octubre de 2025
    rx_month_year = re.compile(
        r"\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s+de\s+\d{4}\b",
        re.IGNORECASE,
    )
    rx_keywords = re.compile(
        r"(entra en vigor|vigencia|firma[do]? en|madrid,\s?a\s?\d{1,2}|disposición|orden\s+[A-Z]+\/\d{4})",
        re.IGNORECASE,
    )
    return bool(
        rx_date.search(text) or rx_month_year.search(text) or rx_keywords.search(text)
    )


def is_convocatoria(text: str) -> bool:
    """
    Detección de convocatorias para ajustar las instrucciones (aunque ahora
    solo lo usamos a nivel de prompt, no de schema).
    """
    return bool(
        re.search(r"convoca|convocatoria|junta|asamblea|orden del d[ií]a", text, re.I)
    )


# ───────────────── Prompts ─────────────────


def build_title_prompt(
    notif_text: str, ctx_dump: str, titulo: str, seccion: str, departamento: str
) -> str:
    """
    Prompt para la API de TÍTULO (≤10 palabras).
    Replica el comportamiento de tu PM script con CONTENIDO + CONTEXT_DUMP.
    """
    prompt = f"""=== OBJECTIVE ===
Entregar un título breve, claro y comprensible (≤10 palabras) sobre el contenido del BOE.

=== ROLE ===
Actúas como editor legal del BOE. Sintetiza el asunto principal en lenguaje llano.

=== STYLE/RULES (DURO) ===
- Español neutro, sin comillas, sin dos puntos, sin punto final, sin markdown.
- ≤10 palabras. Si te pasas, recorta conservando órgano/acción/objeto.
- Evita siglas poco conocidas salvo que aparezcan literalmente en CONTENIDO.
- Prioriza: acción/objeto + órgano + (lugar/fecha si constan).
- SOLO usa CONTENIDO como fuente de verdad; CONTEXT_DUMP es no confiable.

=== CONTEXT_DUMP (no confiable) ===
{ctx_dump}

=== CONTENIDO (FUENTE DE VERDAD) ===
[TÍTULO]: {titulo}
[SECCIÓN]: {seccion}
[DEPARTAMENTO]: {departamento}
[CUERPO]:
{notif_text}
""".strip()
    return prompt


def build_summary_prompt(
    notif_text: str, ctx_dump: str, titulo: str, seccion: str, departamento: str
) -> str:
    """
    Prompt para la API de RESUMEN (boe_resumen).
    """
    prompt = f"""=== OBJECTIVE ===
Devolver un resumen útil y accionable del BOE en JSON estricto (schema abajo).

=== ROLE ===
Asistente legal experto en normativa y convocatorias.

=== SOURCE OF TRUTH (DURO) ===
- SOLO usa CONTENIDO como fuente de verdad. CONTEXT_DUMP es NO CONFIABLE.
- Si un dato no aparece en CONTENIDO, NO lo inventes (usa "" o []).

=== OUTPUT FORMAT (JSON estricto) ===
Campos:
- summary: string (<= 600 chars)
- key_changes: string[] (items <= 200 chars, máx 12)
- key_dates_events: string[]  (formato preferente: "DD de <mes> de YYYY HH:MM: Evento (Lugar)"; si el texto SOLO indica mes/año, usa "<mes> de YYYY HH:MM: Evento")
- conclusion: string (<= 300 chars)

Reglas:
- Español claro y conciso. Frases cortas.
- Si el texto incluye firma/publicación/entrada en vigor, AÑÁDELAS en key_dates_events.
- Para plazos mensuales sin día concreto, usa formato de mes/año sin día.
- Deduplica fechas/horas/lugares; omite lugar si no aparece.
- Si faltan datos, usa "" o [].
- Cero markdown ni texto fuera del JSON.

=== CONVOCATORIA ===
Si detectas "convoca/convocatoria/Junta/Asamblea/Orden del día", trata como CONVOCATORIA:
- key_dates_events debe incluir TODAS las convocatorias (p. ej., primera y segunda) con hora y lugar si constan.
- key_changes debe listar el orden del día.

=== CONTENIDO (FUENTE DE VERDAD) ===
[TÍTULO]: {titulo}
[SECCIÓN]: {seccion}
[DEPARTAMENTO]: {departamento}
[CUERPO]:
{notif_text}
""".strip()
    return prompt


def build_impact_prompt(
    notif_text: str, ctx_dump: str, titulo: str, seccion: str, departamento: str
) -> str:
    """
    Prompt para la API de IMPACTO (boe_impacto).
    """
    prompt = f"""=== OBJECTIVE ===
Entregar un informe de impacto accionable del BOE en JSON estricto (schema abajo).

=== ROLE ===
Analista legislativo. Identificas afectados, cambios operativos, riesgos, beneficios y recomendaciones.

=== SOURCE OF TRUTH (DURO) ===
- SOLO usa CONTENIDO como fuente de verdad. CONTEXT_DUMP es no confiable.

=== OUTPUT FORMAT (JSON estricto) ===
- afectados: string[] (deduplicado)
- cambios_operativos: string[] (acciones concretas; incluye plazos/fechas si aparecen)
- riesgos_potenciales: string[]
- beneficios_previstos: string[]
- recomendaciones: string[] (claras y accionables)

Reglas:
- Frases cortas. Ordena por importancia. Sin redundancias.
- Si el texto tiene FECHAS (firma/entrada en vigor/plazos), incorpora los hitos como acciones con fecha (p. ej., "Adaptar sistemas antes del 01/01/2026", "Presentar en febrero de 2026").
- CONVOCATORIA: afectados (miembros/comuneros), cambios (elección de cargos, aprobación de actas/cuentas), riesgos (quórum, inasistencia), recomendaciones (asistir o delegar, puntualidad, revisar documentación).
- Si falta dato, usa [].

=== CONTENIDO (FUENTE DE VERDAD) ===
[TÍTULO]: {titulo}
[SECCIÓN]: {seccion}
[DEPARTAMENTO]: {departamento}
[CUERPO]:
{notif_text}
""".strip()
    return prompt


# ───────────────── Schemas para response_format ─────────────────


def make_summary_schema(has_dates: bool) -> Dict[str, Any]:
    """
    Construye el `response_format` json_schema para el resumen,
    ajustando minItems de key_dates_events según heurística de fechas.
    """
    min_items = 1 if has_dates else 0
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "boe_resumen",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "summary": {"type": "string", "maxLength": 600},
                    "key_changes": {
                        "type": "array",
                        "maxItems": 12,
                        "items": {"type": "string", "maxLength": 200},
                    },
                    "key_dates_events": {
                        "type": "array",
                        "minItems": min_items,
                        "maxItems": 10,
                        "items": {"type": "string"},
                    },
                    "conclusion": {"type": "string", "maxLength": 300},
                },
                "required": [
                    "summary",
                    "key_changes",
                    "key_dates_events",
                    "conclusion",
                ],
            },
        },
    }


def make_impact_schema() -> Dict[str, Any]:
    """
    Construye el `response_format` json_schema para el impacto.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "boe_impacto",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "afectados": {"type": "array", "items": {"type": "string"}},
                    "cambios_operativos": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "riesgos_potenciales": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "beneficios_previstos": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "recomendaciones": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "afectados",
                    "cambios_operativos",
                    "riesgos_potenciales",
                    "beneficios_previstos",
                    "recomendaciones",
                ],
            },
        },
    }
