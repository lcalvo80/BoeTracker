# app/services/openai_service.py
from __future__ import annotations

import os
import json
import time
import logging
import random
import re
import copy
from typing import Dict, Any, Tuple, List, Optional

from app.utils.helpers import clean_code_block, extract_section  # noqa: F401
from app.services.boe_text_extractor import extract_boe_text  # PDF → texto

# ─────────────────────────── Config ───────────────────────────
_OPENAI_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", "45"))
_OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
_OPENAI_BACKOFF_BASE = float(os.getenv("OPENAI_BACKOFF_BASE", "1.5"))
_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
_OPENAI_BUDGET_SECS = float(os.getenv("OPENAI_BUDGET_SECS", "120"))
_OPENAI_DISABLE = os.getenv("OPENAI_DISABLE", "0") == "1"

_MODEL_TITLE = os.getenv("OPENAI_MODEL_TITLE", _OPENAI_MODEL)
_MODEL_SUMMARY = os.getenv("OPENAI_MODEL_SUMMARY", _OPENAI_MODEL)
_MODEL_IMPACT = os.getenv("OPENAI_MODEL_IMPACT", _OPENAI_MODEL)

# Chunking
_OPENAI_CHUNK_SIZE_CHARS = int(os.getenv("OPENAI_CHUNK_SIZE_CHARS", "12000"))
_OPENAI_CHUNK_OVERLAP_CHARS = int(os.getenv("OPENAI_CHUNK_OVERLAP_CHARS", "500"))
_OPENAI_MAX_CHUNKS = int(os.getenv("OPENAI_MAX_CHUNKS", "12"))

# Fallbacks en timeout
_OPENAI_JSON_FALLBACK_FACTOR = float(os.getenv("OPENAI_JSON_FALLBACK_FACTOR", "0.6"))
_OPENAI_JSON_FALLBACK_MAX_TOKENS = int(os.getenv("OPENAI_JSON_FALLBACK_MAX_TOKENS", "350"))

# ─────────────────────────── Estructuras vacías ───────────────────────────
_EMPTY_RESUMEN: Dict[str, Any] = {
    "summary": "",
    "key_changes": [],
    "key_dates_events": [],
    "conclusion": "",
}
_EMPTY_IMPACTO: Dict[str, Any] = {
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
            "key_dates_events": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
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
_MONTHS = (
    r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
)
_DATE_PATTERNS = [
    re.compile(rf"\b(\d{{1,2}}\s+de\s+{_MONTHS}\s+de\s+\d{{4}})\b", re.I),
    re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", re.I),
]
_MONTH_YEAR_RX = re.compile(rf"\b{_MONTHS}\s+de\s+\d{{4}}\b", re.I)
_TIME_PAT = re.compile(r"\b(\d{1,2}:\d{2})\s*(h|horas)?\b", re.I)
_CONV_PAT = re.compile(r"\b(primera|segunda)\s+convocatoria\b", re.I)
_LOC_PAT = re.compile(r"\b(calle|avda\.?|avenida|plaza|edificio|local|sede|km\s*\d+|pol[íi]gono)\b.*", re.I | re.M)
_AGENDA_PAT = re.compile(r"(?im)^(primero|segundo|tercero|cuarto|quinto|sexto|s[eé]ptimo)[\.\-:]\s*(.+)$")
_KEYWORDS_DATES = re.compile(
    r"(entra\s+en\s+vigor|vigencia|firma[do]? en|publicaci[oó]n|plazo|presentaci[oó]n|"
    r"disposici[oó]n|orden\s+[A-ZÁÉÍÓÚ]+/\d{4})",
    re.I,
)
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")

# ─────────────────────────── Utils ───────────────────────────
def _extract_hints(text: str, max_per_type: int = 6) -> Dict[str, List[str]]:
    def _uniq(lst: List[str]) -> List[str]:
        seen, out = set(), []
        for v in lst:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out[:max_per_type]

    dates: List[str] = []
    times: List[str] = []
    convoc: List[str] = []
    locs: List[str] = []
    agenda: List[str] = []

    for rx in _DATE_PATTERNS:
        dates += [m.group(1).strip() for m in rx.finditer(text)]
    if _MONTH_YEAR_RX.search(text):
        dates += [m.group(0).strip() for m in _MONTH_YEAR_RX.finditer(text)]

    times += [m.group(1).strip() for m in _TIME_PAT.finditer(text)]
    convoc += [m.group(0).strip() for m in _CONV_PAT.finditer(text)]
    locs += [m.group(0).strip() for m in _LOC_PAT.finditer(text)]
    agenda += [m.group(0).strip() for m in _AGENDA_PAT.finditer(text)]

    is_convocatoria = bool(re.search(r"convoca|convocatoria|junta|asamblea|orden del d[ií]a", text, re.I))

    return {
        "dates": _uniq(dates),
        "times": _uniq(times),
        "convocatorias": _uniq(convoc),
        "locations": _uniq(locs),
        "agenda": _uniq(agenda),
        "is_convocatoria": [str(is_convocatoria)],
    }


def _has_dates(text: str, hints: Dict[str, List[str]]) -> bool:
    if hints.get("dates") or hints.get("times"):
        return True
    return bool(_KEYWORDS_DATES.search(text))


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


def _is_timeout_error(e: Exception) -> bool:
    try:
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code == 408:
            return True
    except Exception:
        pass
    t = f"{e}".lower()
    return "timeout" in t or "timed out" in t or "request timed out" in t


# ─────────────────────────── OpenAI client ───────────────────────────
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


# ─────────────────────────── Wrappers con reintentos ───────────────────────────
def _chat_completion_with_retry(
    client,
    *,
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    max_tokens: int = 600,
    temperature: float = 0.2,
    deadline_ts: Optional[float] = None,
    seed: Optional[int] = 7,
):
    use_model = model or _OPENAI_MODEL
    last_err: Optional[Exception] = None

    for attempt in range(_OPENAI_MAX_RETRIES + 1):
        if deadline_ts is not None and time.time() >= deadline_ts:
            logging.warning("⏰ Presupuesto de tiempo agotado (texto).")
            if last_err:
                raise last_err
            raise TimeoutError("Presupuesto agotado")

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
            if attempt < _OPENAI_MAX_RETRIES and (code in (429, 500, 502, 503, 504) or _is_timeout_error(e)):
                _sleep_with_retry_after(e, attempt + 1)
                continue
            logging.error(f"❌ OpenAI error (texto final): code={code} {e}")
            raise

    raise last_err  # pragma: no cover


def _json_completion_with_retry(
    client,
    *,
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    max_tokens: int = 900,
    temperature: float = 0.2,
    deadline_ts: Optional[float] = None,
    seed: Optional[int] = 7,
) -> Dict[str, Any]:
    use_model = model or _OPENAI_MODEL
    last_err: Optional[Exception] = None

    for attempt in range(_OPENAI_MAX_RETRIES + 1):
        if deadline_ts is not None and time.time() >= deadline_ts:
            logging.warning("⏰ Presupuesto de tiempo agotado (JSON).")
            if last_err:
                raise last_err
            raise TimeoutError("Presupuesto agotado")

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
            if attempt < _OPENAI_MAX_RETRIES and (code in (429, 500, 502, 503, 504) or _is_timeout_error(e)):
                _sleep_with_retry_after(e, attempt + 1)
                continue
            logging.error(f"❌ OpenAI error (JSON final): code={code} {e}")
            raise

    raise last_err  # pragma: no cover


def _json_schema_completion_with_retry(
    client,
    *,
    messages: List[Dict[str, Any]],
    schema: Dict[str, Any],
    model: Optional[str] = None,
    max_tokens: int = 900,
    temperature: float = 0.2,
    deadline_ts: Optional[float] = None,
    seed: Optional[int] = 7,
    fallback_to_json_object_on_timeout: bool = True,
) -> Dict[str, Any]:
    """
    Intenta con json_schema. Si tras reintentos hay timeout y fallback activo,
    reintenta una vez con json_object y tokens reducidos para evitar timeouts.
    """
    use_model = model or _OPENAI_MODEL
    last_err: Optional[Exception] = None

    for attempt in range(_OPENAI_MAX_RETRIES + 1):
        if deadline_ts is not None and time.time() >= deadline_ts:
            logging.warning("⏰ Presupuesto agotado (JSON Schema). Paso a fallback si procede.")
            break

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

            # Fallback si el backend no soporta json_schema
            if "response_format" in text or "json_schema" in text or code == 400:
                logging.warning("⚠️ json_schema no soportado. Fallback a json_object.")
                return _json_completion_with_retry(
                    client,
                    messages=messages,
                    model=use_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    deadline_ts=deadline_ts,
                    seed=seed,
                )

            if attempt < _OPENAI_MAX_RETRIES and (code in (429, 500, 502, 503, 504) or _is_timeout_error(e)):
                _sleep_with_retry_after(e, attempt + 1)
                continue

            if not _is_timeout_error(e):
                logging.error(f"❌ OpenAI error (JSON schema): code={code} {e}")
                raise

            # Timeout final → salimos para aplicar fallback
            break

    # ───── Fallback por timeout ─────
    if fallback_to_json_object_on_timeout:
        fb_tokens = min(int(max_tokens * _OPENAI_JSON_FALLBACK_FACTOR), _OPENAI_JSON_FALLBACK_MAX_TOKENS)
        logging.warning(f"⏱️ Timeout con json_schema. Reintentando con json_object (max_tokens={fb_tokens})…")
        try:
            return _json_completion_with_retry(
                client,
                messages=messages,
                model=use_model,
                max_tokens=fb_tokens,
                temperature=temperature,
                deadline_ts=deadline_ts,
                seed=seed,
            )
        except Exception as e2:
            logging.error(f"❌ Fallback json_object también falló: {e2}")
            raise last_err or e2

    # Sin fallback → error final
    raise last_err or TimeoutError("Timeout en json_schema")


# ─────────────────────────── Normalización/merge ───────────────────────────
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


def _split_chunks(text: str, size: int, overlap: int) -> List[str]:
    if size <= 0:
        return [text]
    chunks: List[str] = []
    i = 0
    n = max(0, size - overlap)
    while i < len(text) and len(chunks) < _OPENAI_MAX_CHUNKS:
        chunks.append(text[i : i + size])
        i += n if n > 0 else size
    if i < len(text):
        chunks.append(text[-size:])
    return chunks


def _uniq_keep_order(seq: List[str], limit: Optional[int] = None) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in seq:
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if limit and len(out) >= limit:
            break
    return out


def _merge_resumen_objs(parts: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not parts:
        return dict(_EMPTY_RESUMEN)

    all_changes: List[str] = []
    all_dates: List[str] = []
    summs: List[str] = []
    concls: List[str] = []

    for p in parts:
        p = _ensure_resumen_shape(p)
        if p.get("summary"):
            summs.append(p["summary"])
        all_changes.extend(p.get("key_changes", []) or [])
        all_dates.extend(p.get("key_dates_events", []) or [])
        if p.get("conclusion"):
            concls.append(p["conclusion"])

    summary_join = " ".join(s for s in summs if s).strip()
    conclusion_join = " ".join(s for s in concls if s).strip()

    merged = {
        "summary": summary_join[:600],
        "key_changes": _uniq_keep_order(all_changes, limit=12),
        "key_dates_events": _uniq_keep_order(all_dates, limit=10),
        "conclusion": conclusion_join[:300],
    }
    return _ensure_resumen_shape(merged)


def _merge_impacto_objs(parts: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not parts:
        return dict(_EMPTY_IMPACTO)

    keys = ["afectados", "cambios_operativos", "riesgos_potenciales", "beneficios_previstos", "recomendaciones"]
    agg: Dict[str, List[str]] = {k: [] for k in keys}

    for p in parts:
        p = _ensure_impacto_shape(p)
        for k in keys:
            agg[k].extend(p.get(k, []) or [])

    merged = {k: _uniq_keep_order(v, limit=20) for k, v in agg.items()}
    return _ensure_impacto_shape(merged)


_STOP_PUNCT_RE = re.compile(r"[\"“”'’`´]+")
def _grade_title(s: str, max_words: int = 10) -> str:
    if not isinstance(s, str):
        s = ""
    s = s.strip()
    s = clean_code_block(s).strip()
    s = _STOP_PUNCT_RE.sub("", s)
    s = s.replace(":", " ")
    s = _WHITESPACE_RE.sub(" ", s).strip()
    if s.endswith("."):
        s = s[:-1].rstrip()

    parts = s.split()
    if len(parts) > max_words:
        low_info = {"de", "la", "del", "al", "y", "en", "por", "para", "el", "los", "las", "un", "una", "unos", "unas"}
        kept: List[str] = []
        for w in parts:
            if len(kept) >= max_words:
                break
            if w.lower() in low_info and len(parts) - len(kept) > (max_words - len(kept)):
                continue
            kept.append(w)
        s = " ".join(kept[:max_words])
    return s


# ─────────────────────────── API principal (mantenida por compatibilidad) ───────────────────────────
def get_openai_responses(title: str, content: str) -> Tuple[str, str, str]:
    """
    Devuelve: (titulo_resumen, resumen_json_str, impacto_json_str)
    Mantener por compatibilidad. Internamente usa las funciones nuevas generate_*.
    """
    titulo = generate_title(title_hint=title, content=content)
    resumen = generate_summary(content=content, title_hint=title)
    impacto = generate_impact(content=content, title_hint=title)
    return (
        titulo,
        json.dumps(resumen, ensure_ascii=False),
        json.dumps(impacto, ensure_ascii=False),
    )


def get_openai_responses_from_pdf(identificador: str, titulo: str, url_pdf: str) -> Tuple[str, str, str]:
    """
    Variante que usa SIEMPRE el texto del PDF del BOE como contenido.
    """
    content = ""
    if url_pdf:
        try:
            content = extract_boe_text(identificador=identificador, url_pdf=url_pdf)
        except Exception as e:
            logging.error("❌ Error extrayendo texto del PDF (%s): %s", identificador, e)

    if not content:
        logging.warning("⚠️ No se pudo extraer texto del PDF para %s. Uso título como contenido.", identificador)
        content = (titulo or "").strip()

    return get_openai_responses(titulo, content)


# ─────────────────────────── NUEVO: funciones públicas por endpoint ───────────────────────────
def generate_title(*, title_hint: str, content: str) -> str:
    """
    Genera título (≤10 palabras) usando el contenido (texto del PDF) y un hint opcional del título oficial.
    """
    if _OPENAI_DISABLE:
        logging.warning("⚠️ OPENAI_DISABLE=1: omitido título.")
        return (title_hint or "").strip()

    client = _make_client()
    if client is None:
        return (title_hint or "").strip()

    start_ts = time.time()
    deadline_ts: Optional[float] = start_ts + _OPENAI_BUDGET_SECS if _OPENAI_BUDGET_SECS > 0 else None

    content_norm = _normalize_content(content or "")

    messages: List[Dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Eres un asistente que redacta títulos del BOE en español claro. "
                "SOLO texto plano; sin comillas; sin dos puntos; sin punto final; máximo 10 palabras; no inventes."
            ),
        },
        {
            "role": "user",
            "content": (
                "Resume el título oficial en ≤10 palabras, directo y comprensible. "
                "Sin dos puntos, sin comillas, sin punto final.\n\n"
                "<<<TÍTULO_OFICIAL>>>\n" + (title_hint or "") + "\n\n"
                "<<<CONTENIDO_PDF>>>\n" + content_norm
            ),
        },
    ]
    try:
        resp = _chat_completion_with_retry(
            client,
            messages=messages,
            model=_MODEL_TITLE,
            max_tokens=40,
            temperature=0.2,
            deadline_ts=deadline_ts,
            seed=7,
        )
        raw = (resp.choices[0].message.content or "").strip()
        return _grade_title(raw)
    except Exception as e:
        logging.warning(f"⚠️ OpenAI título: {e}. Uso título original.")
        return (title_hint or "").strip()


def generate_summary(*, content: str, title_hint: str = "") -> Dict[str, Any]:
    """
    Genera resumen boe_resumen (dict).
    """
    if _OPENAI_DISABLE:
        logging.warning("⚠️ OPENAI_DISABLE=1: omitido resumen.")
        return dict(_EMPTY_RESUMEN)

    client = _make_client()
    if client is None:
        return dict(_EMPTY_RESUMEN)

    start_ts = time.time()
    deadline_ts: Optional[float] = start_ts + _OPENAI_BUDGET_SECS if _OPENAI_BUDGET_SECS > 0 else None

    content_norm = _normalize_content(content or "")
    if not content_norm:
        return dict(_EMPTY_RESUMEN)

    hints = _extract_hints(content_norm)
    has_dates = _has_dates(content_norm, hints)
    resumen_schema = copy.deepcopy(_RESUMEN_JSON_SCHEMA_BASE)
    resumen_schema["schema"]["properties"]["key_dates_events"]["minItems"] = (1 if has_dates else 0)

    chunks = (
        [content_norm]
        if len(content_norm) <= _OPENAI_CHUNK_SIZE_CHARS
        else _split_chunks(content_norm, _OPENAI_CHUNK_SIZE_CHARS, _OPENAI_CHUNK_OVERLAP_CHARS)
    )
    if len(chunks) > 1:
        logging.info(f"✂️ Chunking contenido en {len(chunks)} trozos")

    parts: List[Dict[str, Any]] = []
    for idx, ch in enumerate(chunks, start=1):
        prompt = "\n".join(
            [
                "=== OBJECTIVE ===",
                "Devolver un resumen útil y accionable del BOE en JSON estricto (schema abajo).",
                "",
                "=== ROLE ===",
                "Asistente legal experto en normativa y convocatorias del BOE (España).",
                "",
                "=== SOURCE OF TRUTH (DURO) ===",
                "- SOLO usa CONTENIDO como fuente de verdad.",
                '- Si un dato no aparece en CONTENIDO, NO lo inventes (usa \"\" o []).',
                "",
                "=== OUTPUT FORMAT (JSON estricto) ===",
                "Campos:",
                "- summary: string (<= 600 chars).",
                "  Debe explicar SIEMPRE, de forma compacta:",
                "  - Tipo de acto (resolución, orden, anuncio, licitación…).",
                "  - Órgano que lo dicta.",
                "  - Destinatario(s) principal(es).",
                "  - Efecto principal: nombramiento, adjudicación, aprobación, modificación, convocatoria, etc.",
                "  - Si constan: plazos y posibilidad de recursos o impugnaciones.",
                "",
                "- key_changes: string[] (items <= 200 chars, máx 12).",
                "  - Cada elemento recoge UN cambio o decisión relevante.",
                "",
                "- key_dates_events: string[] (máx 10).",
                "  - Formato: \"DD de <mes> de YYYY HH:MM: Evento (Lugar)\" cuando sea posible.",
                "  - Incluye fechas de firma, publicación, entrada en vigor, plazos, recursos…",
                "",
                "- conclusion: string (<= 300 chars).",
                "  - Cierre con consecuencia práctica principal.",
                "",
                "Reglas:",
                "- Español claro y conciso. Frases cortas.",
                "- Deduplica fechas/horas/lugares. No inventes.",
                "- Cero markdown ni texto fuera del JSON.",
                "",
                "=== CONVOCATORIA ===",
                'Si detectas "convoca/convocatoria/Junta/Asamblea/Orden del día":',
                "- key_dates_events incluye TODAS las convocatorias (primera/segunda) con hora y lugar si constan.",
                "- key_changes lista el orden del día.",
                "",
                "=== PISTAS_AUTOMÁTICAS (no son verdad absoluta) ===",
                json.dumps(hints, ensure_ascii=False),
                "",
                f"=== CONTENIDO (FUENTE DE VERDAD) — PARTE {idx}/{len(chunks)} ===",
                ch,
            ]
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Eres un asistente legal experto en normativa española y BOE. "
                    "Responde SOLO con JSON válido conforme al schema. Nada fuera del JSON. "
                    "Usa SOLO el CONTENIDO; no inventes datos."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            r_obj = _json_schema_completion_with_retry(
                client,
                messages=messages,
                schema=resumen_schema,
                model=_MODEL_SUMMARY,
                max_tokens=900,
                temperature=0.1,
                deadline_ts=deadline_ts,
                seed=7,
                fallback_to_json_object_on_timeout=True,
            )
            parts.append(_ensure_resumen_shape(r_obj))
        except Exception as e:
            logging.warning(f"⚠️ OpenAI (resumen chunk {idx}) con fallback agotado: {e}")
            parts.append(dict(_EMPTY_RESUMEN))

    out = _merge_resumen_objs(parts) if parts else dict(_EMPTY_RESUMEN)

    # Señal de QA si sospechoso (texto largo pero respuesta vacía)
    if len(content_norm) > 1000 and (not out.get("summary") or out["summary"].strip() == ""):
        out["conclusion"] = "Revisión necesaria: el modelo devolvió un resumen vacío pese a haber contenido suficiente."

    return out


def generate_impact(*, content: str, title_hint: str = "") -> Dict[str, Any]:
    """
    Genera impacto boe_impacto (dict).
    """
    if _OPENAI_DISABLE:
        logging.warning("⚠️ OPENAI_DISABLE=1: omitido impacto.")
        return dict(_EMPTY_IMPACTO)

    client = _make_client()
    if client is None:
        return dict(_EMPTY_IMPACTO)

    start_ts = time.time()
    deadline_ts: Optional[float] = start_ts + _OPENAI_BUDGET_SECS if _OPENAI_BUDGET_SECS > 0 else None

    content_norm = _normalize_content(content or "")
    if not content_norm:
        return dict(_EMPTY_IMPACTO)

    hints = _extract_hints(content_norm)

    chunks = (
        [content_norm]
        if len(content_norm) <= _OPENAI_CHUNK_SIZE_CHARS
        else _split_chunks(content_norm, _OPENAI_CHUNK_SIZE_CHARS, _OPENAI_CHUNK_OVERLAP_CHARS)
    )
    if len(chunks) > 1:
        logging.info(f"✂️ Chunking contenido en {len(chunks)} trozos")

    parts: List[Dict[str, Any]] = []
    for idx, ch in enumerate(chunks, start=1):
        prompt = "\n".join(
            [
                "=== OBJECTIVE ===",
                "Analizar el impacto práctico de la disposición del BOE.",
                "",
                "=== ROLE ===",
                "Analista legislativo que traduce normas del BOE a implicaciones operativas.",
                "",
                "=== OUTPUT FORMAT (JSON estricto) ===",
                "Campos:",
                "- afectados: string[]",
                "- cambios_operativos: string[]",
                "- riesgos_potenciales: string[]",
                "- beneficios_previstos: string[]",
                "- recomendaciones: string[]",
                "",
                "Reglas:",
                "- Usa SOLO el contenido de la disposición; no inventes.",
                "- Listas por importancia. Frases cortas. Sin redundancias.",
                "- Si falta dato para un campo, usa [].",
                "",
                "=== PISTAS_AUTOMÁTICAS (no son verdad absoluta) ===",
                json.dumps(hints, ensure_ascii=False),
                "",
                f"=== CONTENIDO (FUENTE DE VERDAD) — PARTE {idx}/{len(chunks)} ===",
                ch,
            ]
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Eres un analista legislativo. Responde EXCLUSIVAMENTE en JSON "
                    "válido conforme al esquema. No añadas nada fuera del JSON. "
                    "No inventes. Usa SOLO el CONTENIDO de esta parte."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            i_obj = _json_schema_completion_with_retry(
                client,
                messages=messages,
                schema=_IMPACTO_JSON_SCHEMA,
                model=_MODEL_IMPACT,
                max_tokens=900,
                temperature=0.1,
                deadline_ts=deadline_ts,
                seed=7,
                fallback_to_json_object_on_timeout=True,
            )
            parts.append(_ensure_impacto_shape(i_obj))
        except Exception as e:
            logging.warning(f"⚠️ OpenAI (impacto chunk {idx}) con fallback agotado: {e}")
            parts.append(dict(_EMPTY_IMPACTO))

    return _merge_impacto_objs(parts) if parts else dict(_EMPTY_IMPACTO)
