from __future__ import annotations

import os, json, time, logging, random, re, copy
from typing import Dict, Any, Tuple, List, Optional
from utils.helpers import extract_section, clean_code_block  # noqa: F401

_OPENAI_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", "45"))
_OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
_OPENAI_BACKOFF_BASE = float(os.getenv("OPENAI_BACKOFF_BASE", "1.5"))
_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
_OPENAI_BUDGET_SECS = float(os.getenv("OPENAI_BUDGET_SECS", "120"))
_OPENAI_DISABLE = os.getenv("OPENAI_DISABLE", "0") == "1"

_MODEL_TITLE = os.getenv("OPENAI_MODEL_TITLE", _OPENAI_MODEL)
_MODEL_SUMMARY = os.getenv("OPENAI_MODEL_SUMMARY", _OPENAI_MODEL)
_MODEL_IMPACT = os.getenv("OPENAI_MODEL_IMPACT", _OPENAI_MODEL)

# ─────────────────────────── Estructuras vacías ───────────────────────────
_EMPTY_RESUMEN = {
    "summary": "",
    "key_changes": [],
    "key_dates_events": [],
    "conclusion": "",
}
_EMPTY_IMPACTO = {
    "afectados": [],
    "cambios_operativos": [],
    "riesgos_potenciales": [],
    "beneficios_previstos": [],
    "recomendaciones": [],
}

# ─────────────────────────── JSON Schemas base ───────────────────────────
_RESUMEN_JSON_SCHEMA_BASE: Dict[str, Any] = {
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
                "maxItems": 10,
                "items": {"type": "string"},
            },
            "conclusion": {"type": "string", "maxLength": 300},
        },
        "required": ["summary", "key_changes", "key_dates_events", "conclusion"],
    },
}

_IMPACTO_JSON_SCHEMA: Dict[str, Any] = {
    "name": "boe_impacto",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
                "afectados": {"type": "array", "items": {"type": "string"}},
                "cambios_operativos": {"type": "array", "items": {"type": "string"}},
                "riesgos_potenciales": {"type": "array", "items": {"type": "string"}},
                "beneficios_previstos": {"type": "array", "items": {"type": "string"}},
                "recomendaciones": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "afectados",
            "cambios_operativos",
            "riesgos_potenciales",
            "beneficios_previstos",
            "recomendaciones",
        ],
    },
}

# ─────────────────────────── Heurísticas y regex ───────────────────────────
_MONTHS = r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
_DATE_PATTERNS = [
    re.compile(rf"\b(\d{{1,2}}\s+de\s+{_MONTHS}\s+de\s+\d{{4}})\b", re.I),
    re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b"),
]
_MONTH_YEAR_RX = re.compile(rf"\b{_MONTHS}\s+de\s+\d{{4}}\b", re.I)
_TIME_PAT = re.compile(r"\b(\d{1,2}:\d{2})\s*(h|horas)?\b", re.I)
_CONV_PAT = re.compile(r"\b(primera|segunda)\s+convocatoria\b", re.I)
_LOC_PAT = re.compile(r"\b(calle|avda\.?|avenida|plaza|edificio|local|sede|km\s*\d+|pol[íi]gono)\b.*", re.I | re.M)
_AGENDA_PAT = re.compile(r"(?im)^(primero|segundo|tercero|cuarto|quinto|sexto|s[eé]ptimo)[\.\-:]\s*(.+)$")
_KEYWORDS_DATES = re.compile(
    r"(entra\s+en\s+vigor|vigencia|firma[do]? en|publicaci[oó]n|plazo|presentaci[oó]n)", re.I
)
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")

def _extract_hints(text: str, max_per_type: int = 6) -> Dict[str, List[str]]:
    def _uniq(lst):
        seen, out = set(), []
        for v in lst:
            if v not in seen:
                seen.add(v); out.append(v)
        return out[:max_per_type]

    dates, times, convoc, locs, agenda = [], [], [], [], []
    for rx in _DATE_PATTERNS:
        dates += [m.group(1).strip() for m in rx.finditer(text)]
    if _MONTH_YEAR_RX.search(text):
        dates += [m.group(0).strip() for m in _MONTH_YEAR_RX.finditer(text)]
    times += [m.group(1).strip() for m in _TIME_PAT.finditer(text)]
    convoc += [m.group(0).strip() for m in _CONV_PAT.finditer(text)]
    locs   += [m.group(0).strip() for m in _LOC_PAT.finditer(text)]
    agenda += [m.group(0).strip() for m in _AGENDA_PAT.finditer(text)]
    return {
        "dates": _uniq(dates),
        "times": _uniq(times),
        "convocatorias": _uniq(convoc),
        "locations": _uniq(locs),
        "agenda": _uniq(agenda),
    }

def _has_dates(text: str, hints: Dict[str, List[str]]) -> bool:
    if hints.get("dates") or hints.get("times"):
        return True
    return bool(_KEYWORDS_DATES.search(text))

# ─────────────────────────── utilidades OpenAI ───────────────────────────
def _sleep_with_retry_after(exc: Exception, attempt: int) -> None:
    ra = None
    try:
        ra = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After")
    except Exception:
        pass
    delay = float(ra) if ra else (_OPENAI_BACKOFF_BASE ** attempt)
    delay = max(0.5, min(delay * (0.85 + 0.3 * random.random()), 20.0))
    logging.warning(f"⏳ Backoff intento {attempt}: {delay:.1f}s…")
    time.sleep(delay)

def _make_client():
    try:
        import openai
    except Exception as e:
        logging.error(f"❌ No se pudo importar openai: {e}")
        return None
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logging.error("❌ Falta OPENAI_API_KEY.")
        return None
    try:
        return openai.OpenAI(api_key=api_key, timeout=_OPENAI_TIMEOUT, max_retries=0)
    except Exception as e:
        logging.error(f"❌ Error inicializando cliente OpenAI: {e}")
        return None

def _chat_completion_with_retry(client, *, messages, model=None, max_tokens=600, temperature=0.2, deadline_ts=None, seed: Optional[int]=7):
    use_model = model or _OPENAI_MODEL
    last_err = None
    for attempt in range(_OPENAI_MAX_RETRIES + 1):
        if deadline_ts is not None and time.time() >= deadline_ts:
            logging.error("⏰ Presupuesto de tiempo agotado (texto).")
            raise last_err or TimeoutError("Presupuesto agotado")
        try:
            return client.chat.completions.create(
                model=use_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                seed=seed,
            )
        except Exception as e:
            last_err = e
            code = getattr(getattr(e, "response", None), "status_code", None)
            if attempt < _OPENAI_MAX_RETRIES and (code in (429, 500, 502, 503, 504) or "timeout" in str(e).lower()):
                _sleep_with_retry_after(e, attempt + 1); continue
            logging.error(f"❌ OpenAI error (texto final): code={code} {e}")
            raise
    raise last_err  # pragma: no cover

def _json_completion_with_retry(client, *, messages, model=None, max_tokens=900, temperature=0.2, deadline_ts=None, seed: Optional[int]=7) -> Dict[str, Any]:
    """Fallback a json_object (compat)."""
    use_model = model or _OPENAI_MODEL
    last_err = None
    for attempt in range(_OPENAI_MAX_RETRIES + 1):
        if deadline_ts is not None and time.time() >= deadline_ts:
            logging.error("⏰ Presupuesto de tiempo agotado (JSON).")
            raise last_err or TimeoutError("Presupuesto agotado")
        try:
            resp = client.chat.completions.create(
                model=use_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format={"type": "json_object"},
                seed=seed,
            )
            content = (resp.choices[0].message.content or "").strip()
            try:
                return json.loads(content)
            except Exception:
                return json.loads(clean_code_block(content))
        except Exception as e:
            last_err = e
            code = getattr(getattr(e, "response", None), "status_code", None)
            if attempt < _OPENAI_MAX_RETRIES and (code in (429, 500, 502, 503, 504) or "timeout" in str(e).lower()):
                _sleep_with_retry_after(e, attempt + 1); continue
            logging.error(f"❌ OpenAI error (JSON final): code={code} {e}")
            raise
    raise last_err  # pragma: no cover

def _json_schema_completion_with_retry(client, *, messages, schema: Dict[str, Any], model=None, max_tokens=900, temperature=0.2, deadline_ts=None, seed: Optional[int]=7) -> Dict[str, Any]:
    """Intenta json_schema y cae a json_object si no está soportado."""
    use_model = model or _OPENAI_MODEL
    last_err = None
    for attempt in range(_OPENAI_MAX_RETRIES + 1):
        if deadline_ts is not None and time.time() >= deadline_ts:
            logging.error("⏰ Presupuesto de tiempo agotado (JSON Schema).")
            raise last_err or TimeoutError("Presupuesto agotado")
        try:
            resp = client.chat.completions.create(
                model=use_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format={"type": "json_schema", "json_schema": schema},
                seed=seed,
            )
            content = (resp.choices[0].message.content or "").strip()
            return json.loads(content)
        except Exception as e:
            last_err = e
            text = f"{e}"
            code = getattr(getattr(e, "response", None), "status_code", None)
            if "response_format" in text or "json_schema" in text or code == 400:
                logging.warning("⚠️ response_format json_schema no soportado. Fallback a json_object.")
                return _json_completion_with_retry(
                    client, messages=messages, model=use_model, max_tokens=max_tokens, temperature=temperature, deadline_ts=deadline_ts, seed=seed
                )
            if attempt < _OPENAI_MAX_RETRIES and (code in (429, 500, 502, 503, 504) or "timeout" in str(e).lower()):
                _sleep_with_retry_after(e, attempt + 1); continue
            logging.error(f"❌ OpenAI error (JSON schema): code={code} {e}")
            raise
    raise last_err  # pragma: no cover

# ─────────────────────────── Normalización/limpieza ───────────────────────────
def _normalize_content(content: str, hard_limit_chars: int = 28000) -> str:
    if not isinstance(content, str):
        return ""
    s = content.replace("\u00A0", " ")
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    if len(s) <= hard_limit_chars:
        return s
    return f"{s[:24000]}\n...\n{s[-4000:]}"

def _ensure_resumen_shape(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Alinea el objeto al schema actual.
    Acepta 'summary' (nuevo) o 'context' (legacy) y mapea a 'summary'.
    """
    out = dict(_EMPTY_RESUMEN)
    if isinstance(obj, dict):
        summary = obj.get("summary", None)
        if (summary is None or str(summary).strip() == "") and "context" in obj:
            summary = obj.get("context")  # retro-compat
        out["summary"] = str(summary or "").strip()
        out["key_changes"] = [str(x).strip() for x in obj.get("key_changes", []) if str(x).strip()]
        out["key_dates_events"] = [str(x).strip() for x in obj.get("key_dates_events", []) if str(x).strip()]
        out["conclusion"] = str(obj.get("conclusion", "")).strip()
    return out

def _ensure_impacto_shape(obj: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(_EMPTY_IMPACTO)
    if isinstance(obj, dict):
        out["afectados"] = [str(x).strip() for x in obj.get("afectados", []) if str(x).strip()]
        out["cambios_operativos"] = [str(x).strip() for x in obj.get("cambios_operativos", []) if str(x).strip()]
        out["riesgos_potenciales"] = [str(x).strip() for x in obj.get("riesgos_potenciales", []) if str(x).strip()]
        out["beneficios_previstos"] = [str(x).strip() for x in obj.get("beneficios_previstos", []) if str(x).strip()]
        out["recomendaciones"] = [str(x).strip() for x in obj.get("recomendaciones", []) if str(x).strip()]
    return out

# ─────────────────────────── Título: grader/normalizador ───────────────────────────
_STOP_PUNCT_RE = re.compile(r"[\"“”'’`´]+")
def _grade_title(s: str, max_words: int = 10) -> str:
    """Normaliza y asegura reglas: sin comillas, sin ':', sin punto final, ≤10 palabras."""
    if not isinstance(s, str):
        s = ""
    s = s.strip()
    s = clean_code_block(s).strip()      # quita fences
    s = _STOP_PUNCT_RE.sub("", s)        # quita comillas
    s = s.replace(":", " ")              # quita dos puntos
    s = _WHITESPACE_RE.sub(" ", s).strip()
    if s.endswith("."):                  # quita punto final
        s = s[:-1].rstrip()
    parts = s.split()
    if len(parts) > max_words:
        low_info = {"de","la","del","al","y","en","por","para","el","los","las","un","una","unos","unas"}
        kept: List[str] = []
        for w in parts:
            if len(kept) >= max_words:
                break
            if w.lower() in low_info and len(parts) - len(kept) > (max_words - len(kept)):
                continue
            kept.append(w)
        s = " ".join(kept[:max_words])
    return s

# ─────────────────────────── API principal ───────────────────────────
def get_openai_responses(title: str, content: str) -> Tuple[str, str, str]:
    """
    Devuelve:
      - titulo_resumen (texto plano)
      - resumen_json (string JSON alineado al schema)
      - impacto_json (string JSON alineado al schema)
    """
    if _OPENAI_DISABLE:
        logging.warning("⚠️ OPENAI_DISABLE=1: omitidas llamadas.")
        return "", json.dumps(_EMPTY_RESUMEN, ensure_ascii=False), json.dumps(_EMPTY_IMPACTO, ensure_ascii=False)

    client = _make_client()
    if client is None:
        return "", json.dumps(_EMPTY_RESUMEN, ensure_ascii=False), json.dumps(_EMPTY_IMPACTO, ensure_ascii=False)

    start_ts = time.time()
    deadline_ts = start_ts + _OPENAI_BUDGET_SECS if _OPENAI_BUDGET_SECS > 0 else None

    content_norm = _normalize_content(content or "")
    hints = _extract_hints(content_norm)
    has_dates = _has_dates(content_norm, hints)

    # ───────────────────────── 1) Título (texto) ─────────────────────────
    title_messages = [
        {
            "role": "system",
            "content": (
                "Eres un asistente que redacta títulos del BOE en español claro. "
                "SOLO texto plano; sin comillas; sin dos puntos; sin punto final; máximo 10 palabras; no inventes. "
                "Usa EXCLUSIVAMENTE el TÍTULO OFICIAL que te paso y simplifícalo."
            ),
        },
        {
            "role": "user",
            "content": (
                "Resume este título oficial en ≤10 palabras, directo y comprensible. "
                "Evita tecnicismos y siglas poco comunes. Sin dos puntos, sin comillas, sin punto final.\n\n"
                "<<<TÍTULO>>>\n" + (title or "")
            ),
        },
    ]
    title_resp = _chat_completion_with_retry(
        client, messages=title_messages, model=_MODEL_TITLE, max_tokens=40, temperature=0.2, deadline_ts=deadline_ts, seed=7
    )
    titulo_resumen_raw = (title_resp.choices[0].message.content or "").strip()
    titulo_resumen = _grade_title(titulo_resumen_raw)

    # ───────────────────────── 2) Resumen (JSON) ─────────────────────────
    resumen_schema = copy.deepcopy(_RESUMEN_JSON_SCHEMA_BASE)
    resumen_schema["schema"]["properties"]["key_dates_events"]["minItems"] = 1 if has_dates else 0

    resumen_system = (
        "Eres un asistente legal experto en el BOE (España). "
        "Responde EXCLUSIVAMENTE en JSON válido conforme al esquema. "
        "No añadas texto fuera del JSON. No inventes datos. "
        "Usa SOLO el CONTENIDO como fuente de verdad; CONTEXT_DUMP/PISTAS son orientativos."
    )

    resumen_user_lines = [
        "Devuelve EXACTAMENTE este objeto conforme al esquema.",
        "- Español claro y conciso. Frases cortas.",
        "- Si el texto incluye firma/publicación/entrada en vigor, AÑÁDELAS en key_dates_events.",
        "- Si solo hay mes/año (sin día), usa el formato '<mes> de YYYY 00:00: Evento'.",
        "- Deduplica fechas/horas/lugares; omite lugar si no aparece.",
        "- Si es CONVOCATORIA: incluye TODAS las convocatorias (primera/segunda) con hora y lugar si constan, y el orden del día en key_changes.",
        "- Si falta un dato, usa \"\" o [].",
        "",
        "<<<CONTENIDO>>>",
        content_norm,
        "",
        "<<<PISTAS_DETECTADAS>>>",
        json.dumps(hints, ensure_ascii=False),
    ]
    resumen_user = "\n".join(resumen_user_lines)

    resumen_messages = [
        {"role": "system", "content": resumen_system},
        {"role": "user", "content": resumen_user},
    ]
    resumen_obj = _json_schema_completion_with_retry(
        client,
        messages=resumen_messages,
        schema=resumen_schema,
        model=_MODEL_SUMMARY,
        max_tokens=900,
        temperature=0.1,
        deadline_ts=deadline_ts,
        seed=7,
    )
    resumen_obj = _ensure_resumen_shape(resumen_obj)

    # ───────────────────────── 3) Impacto (JSON) ─────────────────────────
    impacto_system = (
        "Eres un analista legislativo. Responde EXCLUSIVAMENTE en JSON válido conforme al esquema. "
        "No añadas nada fuera del JSON. No inventes. Usa SOLO el CONTENIDO."
    )
    impacto_user_lines = [
        "Devuelve EXACTAMENTE este objeto con el esquema.",
        "Guía:",
        "- CONVOCATORIA: afectados (miembros/propietarios/etc.), cambios (elección de cargos, aprobación de cuentas…), riesgos (quórum/inasistencia), recomendaciones (asistir/delegar, puntualidad, revisar documentación).",
        "- LICITACIÓN: plazos, documentación, solvencia/garantías; riesgos de forma/plazos; recomendaciones para no quedar excluido.",
        "- RESOLUCIÓN/NOMBR.: obligaciones/efectos y recomendaciones de cumplimiento.",
        "- Listas ordenadas por importancia. Frases cortas. Sin redundancias.",
        "- Si falta dato, usa [].",
        "",
        "- Si el texto contiene FECHAS (firma/entrada en vigor/plazos), incluye acciones con hito en cambios_operativos.",
        "",
        "<<<CONTENIDO>>>",
        content_norm,
        "",
        "<<<PISTAS_DETECTADAS>>>",
        json.dumps(hints, ensure_ascii=False),
    ]
    impacto_user = "\n".join(impacto_user_lines)

    impacto_messages = [
        {"role": "system", "content": impacto_system},
        {"role": "user", "content": impacto_user},
    ]
    impacto_obj = _json_schema_completion_with_retry(
        client,
        messages=impacto_messages,
        schema=_IMPACTO_JSON_SCHEMA,
        model=_MODEL_IMPACT,
        max_tokens=900,
        temperature=0.1,
        deadline_ts=deadline_ts,
        seed=7,
    )
    impacto_obj = _ensure_impacto_shape(impacto_obj)

    return (
        titulo_resumen,
        json.dumps(resumen_obj, ensure_ascii=False),
        json.dumps(impacto_obj, ensure_ascii=False),
    )
