# openai_service.py
import os
import json
import time
import logging
import random
import re
from typing import Optional, Dict, Any, Tuple

from utils.helpers import extract_section, clean_code_block

# --- Parámetros configurables por entorno (útiles en CI) ---
_OPENAI_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", "45"))           # segundos por request
_OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "3"))    # reintentos app
_OPENAI_BACKOFF_BASE = float(os.getenv("OPENAI_BACKOFF_BASE", "1.5"))
_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
_OPENAI_BUDGET_SECS = float(os.getenv("OPENAI_BUDGET_SECS", "120"))  # presupuesto total de la triple llamada
_OPENAI_DISABLE = os.getenv("OPENAI_DISABLE", "0") == "1"

# Modelos específicos por tarea (opcionales, mantienen compat)
_MODEL_TITLE = os.getenv("OPENAI_MODEL_TITLE", _OPENAI_MODEL)
_MODEL_SUMMARY = os.getenv("OPENAI_MODEL_SUMMARY", _OPENAI_MODEL)
_MODEL_IMPACT = os.getenv("OPENAI_MODEL_IMPACT", _OPENAI_MODEL)

# --- Esquemas esperados por el FE ---
_EMPTY_RESUMEN: Dict[str, Any] = {
    "context": "",
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

# --- Utilidades de backoff ---
def _sleep_with_retry_after(exc: Exception, attempt: int) -> None:
    retry_after = None
    try:
        retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After")
    except Exception:
        pass

    if retry_after:
        try:
            delay = float(retry_after)
        except ValueError:
            delay = (_OPENAI_BACKOFF_BASE ** attempt)
    else:
        delay = (_OPENAI_BACKOFF_BASE ** attempt)

    # Jitter suave para evitar sincronía entre workers
    delay = delay * (0.85 + 0.3 * random.random())
    delay = max(0.5, min(delay, 20.0))  # seguridad
    logging.warning(f"⏳ Backoff intento {attempt}: durmiendo {delay:.1f}s…")
    time.sleep(delay)

# --- Cliente OpenAI ---
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
        client = openai.OpenAI(
            api_key=api_key,
            timeout=_OPENAI_TIMEOUT,  # por-request
            max_retries=0,            # los reintentos los controlamos aquí
        )
        return client
    except Exception as e:
        logging.error(f"❌ Error inicializando cliente OpenAI: {e}")
        return None

# --- Llamada con reintentos (texto libre) ---
def _chat_completion_with_retry(
    client,
    *,
    messages,
    model: Optional[str] = None,
    max_tokens: int = 600,
    temperature: float = 0.2,
    deadline_ts: Optional[float] = None,
):
    try:
        from openai import APITimeoutError, APIConnectionError, RateLimitError
    except Exception:
        APITimeoutError = APIConnectionError = RateLimitError = tuple()  # type: ignore

    use_model = model or _OPENAI_MODEL
    last_err = None

    for attempt in range(_OPENAI_MAX_RETRIES + 1):
        if deadline_ts is not None and time.time() >= deadline_ts:
            logging.error("⏰ Presupuesto de tiempo agotado antes de invocar OpenAI (texto).")
            if last_err:
                raise last_err
            raise TimeoutError("Presupuesto de tiempo agotado")

        try:
            return client.chat.completions.create(
                model=use_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e:
            last_err = e
            code = getattr(getattr(e, "response", None), "status_code", None)
            msg = str(e).lower()
            is_timeout = isinstance(e, APITimeoutError) or ("timeout" in msg or "timed out" in msg)
            is_rate_or_5xx = code in (429, 500, 502, 503, 504)

            if attempt < _OPENAI_MAX_RETRIES and (is_rate_or_5xx or is_timeout):
                logging.warning(f"⚠️ OpenAI falló (texto) intento {attempt}/{_OPENAI_MAX_RETRIES} code={code}: {e}")
                _sleep_with_retry_after(e, attempt + 1)
                continue

            logging.error(f"❌ OpenAI error (texto, final): code={code} {e}")
            raise

    raise last_err  # pragma: no cover

# --- Llamada con reintentos (modo JSON estricto) ---
def _json_completion_with_retry(
    client,
    *,
    messages,
    model: Optional[str] = None,
    max_tokens: int = 900,
    temperature: float = 0.2,
    deadline_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Fuerza salida JSON con response_format={"type":"json_object"}.
    Devuelve el objeto dict parseado o levanta excepción si se agotan reintentos.
    """
    try:
        from openai import APITimeoutError, APIConnectionError, RateLimitError
    except Exception:
        APITimeoutError = APIConnectionError = RateLimitError = tuple()  # type: ignore

    use_model = model or _OPENAI_MODEL
    last_err = None

    for attempt in range(_OPENAI_MAX_RETRIES + 1):
        if deadline_ts is not None and time.time() >= deadline_ts:
            logging.error("⏰ Presupuesto de tiempo agotado antes de invocar OpenAI (JSON).")
            if last_err:
                raise last_err
            raise TimeoutError("Presupuesto de tiempo agotado")

        try:
            resp = client.chat.completions.create(
                model=use_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            content = (resp.choices[0].message.content or "").strip()
            # Intento directo
            try:
                return json.loads(content)
            except Exception:
                # Fallback suave: limpiar posibles fences erróneos
                try:
                    return json.loads(clean_code_block(content))
                except Exception as e2:
                    raise ValueError(f"Salida JSON inválida: {content[:200]}…") from e2

        except Exception as e:
            last_err = e
            code = getattr(getattr(e, "response", None), "status_code", None)
            msg = str(e).lower()
            is_timeout = isinstance(e, APITimeoutError) or ("timeout" in msg or "timed out" in msg)
            is_rate_or_5xx = code in (429, 500, 502, 503, 504)

            if attempt < _OPENAI_MAX_RETRIES and (is_rate_or_5xx or is_timeout):
                logging.warning(f"⚠️ OpenAI falló (JSON) intento {attempt}/{_OPENAI_MAX_RETRIES} code={code}: {e}")
                _sleep_with_retry_after(e, attempt + 1)
                continue

            logging.error(f"❌ OpenAI error (JSON, final): code={code} {e}")
            raise

    raise last_err  # pragma: no cover

# --- Normalización del contenido largo ---
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
def _normalize_content(content: str, hard_limit_chars: int = 28000) -> str:
    """
    Compacta espacios y corta el contenido si es muy largo (para no agotar tokens).
    Estrategia: primeras ~24k + últimas ~4k, que suele cubrir encabezados y disposiciones finales.
    """
    if not isinstance(content, str):
        return ""
    s = content.replace("\u00A0", " ")
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = _WHITESPACE_RE.sub(" ", s)
    s = s.strip()

    if len(s) <= hard_limit_chars:
        return s

    head = s[:24000]
    tail = s[-4000:]
    return f"{head}\n...\n{tail}"

# --- Limpieza/validación de estructuras ---
def _ensure_resumen_shape(obj: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(_EMPTY_RESUMEN)
    if isinstance(obj, dict):
        out["context"] = str(obj.get("context", "")).strip()
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

# --- API principal (misma firma) ---
def get_openai_responses(title: str, content: str) -> Tuple[str, str, str]:
    """
    Devuelve (titulo_resumen, resumen_json_str, impacto_json_str)
    • No rompe el pipeline: ante error devuelve JSONs vacíos.
    • Usa modo JSON para estabilidad y alineación con el FE.
    """
    if _OPENAI_DISABLE:
        logging.warning("⚠️ OPENAI_DISABLE=1: se omiten llamadas a OpenAI.")
        return "", json.dumps(_EMPTY_RESUMEN, ensure_ascii=False), json.dumps(_EMPTY_IMPACTO, ensure_ascii=False)

    client = _make_client()
    if client is None:
        return "", json.dumps(_EMPTY_RESUMEN, ensure_ascii=False), json.dumps(_EMPTY_IMPACTO, ensure_ascii=False)

    start_ts = time.time()
    deadline_ts = start_ts + _OPENAI_BUDGET_SECS if _OPENAI_BUDGET_SECS > 0 else None

    # Normaliza contenido para proteger tokens/timeout
    content_norm = _normalize_content(content or "")

    try:
        # 1) Título breve
        title_messages = [
            {
                "role": "system",
                "content": (
                    "Eres un asistente que resume títulos del BOE en español claro. "
                    "Devuelve SOLO el título resumido, sin comillas, sin punto final."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Resume este título oficial en ≤10 palabras, directo y comprensible para público general. "
                    "Evita tecnicismos y siglas poco comunes. Sin dos puntos, sin comillas, sin puntos finales.\n\n"
                    f"Título:\n{title}"
                ),
            },
        ]
        title_resp = _chat_completion_with_retry(
            client,
            messages=title_messages,
            model=_MODEL_TITLE,
            max_tokens=50,
            temperature=0.2,
            deadline_ts=deadline_ts,
        )
        titulo_resumen = (title_resp.choices[0].message.content or "").strip().strip(".").strip()

        # 2) Resumen estructurado (JSON)
        resumen_messages = [
            {
                "role": "system",
                "content": (
                    "Eres un asistente legal. Responde estrictamente en JSON UTF-8 válido. "
                    "No añadas explicaciones ni texto fuera del JSON. No inventes datos."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Lee el contenido del BOE y devuelve EXACTAMENTE este objeto JSON con claves fijas:\n"
                    "{\n"
                    '  "context": string,\n'
                    '  "key_changes": string[],\n'
                    '  "key_dates_events": string[],\n'
                    '  "conclusion": string\n'
                    "}\n\n"
                    "Reglas:\n"
                    "- Español claro, formal y neutral. Frases cortas. Sin adornos.\n"
                    "- Si falta algún dato, usa \"\" o [].\n"
                    "- En key_dates_events, usa formato \"1 de enero de 2025: Descripción\" cuando sea posible.\n"
                    "- No uses markdown, comillas extra, ni comentarios.\n\n"
                    f"Contenido:\n{content_norm}"
                ),
            },
        ]
        resumen_obj = _json_completion_with_retry(
            client,
            messages=resumen_messages,
            model=_MODEL_SUMMARY,
            max_tokens=900,
            temperature=0.1,
            deadline_ts=deadline_ts,
        )
        resumen_obj = _ensure_resumen_shape(resumen_obj)

        # 3) Impacto legislativo (JSON)
        impacto_messages = [
            {
                "role": "system",
                "content": (
                    "Eres un analista legislativo. Responde estrictamente en JSON UTF-8 válido. "
                    "No añadas nada fuera del JSON. No inventes datos."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Lee el contenido del BOE y devuelve EXACTAMENTE este objeto JSON con claves fijas:\n"
                    "{\n"
                    '  "afectados": string[],\n'
                    '  "cambios_operativos": string[],\n'
                    '  "riesgos_potenciales": string[],\n'
                    '  "beneficios_previstos": string[],\n'
                    '  "recomendaciones": string[]\n'
                    "}\n\n"
                    "Reglas:\n"
                    "- Español claro, formal y neutral. Frases cortas y concretas.\n"
                    "- Si falta info, usa [].\n"
                    "- Enumera elementos de mayor a menor relevancia.\n"
                    "- No uses markdown, comillas extra, ni comentarios.\n\n"
                    f"Contenido:\n{content_norm}"
                ),
            },
        ]
        impacto_obj = _json_completion_with_retry(
            client,
            messages=impacto_messages,
            model=_MODEL_IMPACT,
            max_tokens=900,
            temperature=0.1,
            deadline_ts=deadline_ts,
        )
        impacto_obj = _ensure_impacto_shape(impacto_obj)

        # Devuelve exactamente como antes: strings JSON
        return (
            titulo_resumen,
            json.dumps(resumen_obj, ensure_ascii=False),
            json.dumps(impacto_obj, ensure_ascii=False),
        )

    except Exception as e:
        # Fallback muy robusto: intenta parsear el formato “por secciones” si el modo JSON fallara en todos los reintentos
        logging.error(f"❌ Error con OpenAI (modo JSON): {e}. Intentando fallback por secciones…")
        try:
            # Resumen por secciones
            old_resumen_prompt = f"""
Actúa como un asistente legal. Devuelve SOLO texto plano con los encabezados:
Contexto:
Cambios clave:
Fechas clave:
Conclusión:

Contenido:
{content_norm}
""".strip()
            resumen_resp = _chat_completion_with_retry(
                client,
                messages=[
                    {"role": "system", "content": "Devolución en texto plano sin markdown ni símbolos extra."},
                    {"role": "user", "content": old_resumen_prompt},
                ],
                model=_MODEL_SUMMARY,
                max_tokens=900,
                temperature=0.2,
                deadline_ts=deadline_ts,
            )
            resumen_text = clean_code_block((resumen_resp.choices[0].message.content or "").strip())
            resumen_obj = {
                "context": extract_section(resumen_text, "Contexto") or "",
                "key_changes": [ln for ln in (extract_section(resumen_text, "Cambios clave") or "").split("\n") if ln.strip()],
                "key_dates_events": [ln for ln in (extract_section(resumen_text, "Fechas clave") or "").split("\n") if ln.strip()],
                "conclusion": extract_section(resumen_text, "Conclusión") or "",
            }
        except Exception:
            resumen_obj = _EMPTY_RESUMEN

        try:
            old_impacto_prompt = f"""
Analiza el contenido y devuelve SOLO texto plano con los encabezados:
Afectados:
Cambios operativos:
Riesgos potenciales:
Beneficios previstos:
Recomendaciones:

Contenido:
{content_norm}
""".strip()
            impacto_resp = _chat_completion_with_retry(
                client,
                messages=[
                    {"role": "system", "content": "Devolución en texto plano sin markdown."},
                    {"role": "user", "content": old_impacto_prompt},
                ],
                model=_MODEL_IMPACT,
                max_tokens=900,
                temperature=0.2,
                deadline_ts=deadline_ts,
            )
            impacto_text = clean_code_block((impacto_resp.choices[0].message.content or "").strip())
            impacto_obj = {
                "afectados": [ln for ln in (extract_section(impacto_text, "Afectados") or "").split("\n") if ln.strip()],
                "cambios_operativos": [ln for ln in (extract_section(impacto_text, "Cambios operativos") or "").split("\n") if ln.strip()],
                "riesgos_potenciales": [ln for ln in (extract_section(impacto_text, "Riesgos potenciales") or "").split("\n") if ln.strip()],
                "beneficios_previstos": [ln for ln in (extract_section(impacto_text, "Beneficios previstos") or "").split("\n") if ln.strip()],
                "recomendaciones": [ln for ln in (extract_section(impacto_text, "Recomendaciones") or "").split("\n") if ln.strip()],
            }
        except Exception:
            impacto_obj = _EMPTY_IMPACTO

        # Mantén el título aunque los JSON fallen
        try:
            titulo_resumen = title.strip().rstrip(".")
        except Exception:
            titulo_resumen = ""

        return (
            titulo_resumen,
            json.dumps(_ensure_resumen_shape(resumen_obj), ensure_ascii=False),
            json.dumps(_ensure_impacto_shape(impacto_obj), ensure_ascii=False),
        )
