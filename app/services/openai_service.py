# openai_service.py
import os
import json
import time
import logging
from typing import Optional

from utils.helpers import extract_section, clean_code_block

# --- Parámetros configurables por entorno (útiles en CI) ---
_OPENAI_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", "45"))        # segundos por request
_OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "3")) # reintentos de aplicación
_OPENAI_BACKOFF_BASE = float(os.getenv("OPENAI_BACKOFF_BASE", "1.5"))
_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")             # modelo por defecto


def _sleep_with_retry_after(exc: Exception, attempt: int) -> None:
    """
    Respeta Retry-After si existe (429) y si no aplica backoff exponencial.
    attempt es 1-based en los logs para legibilidad.
    """
    retry_after = None
    try:
        # SDK v1 expone response.headers si es APIStatusError
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

    delay = min(delay, 20.0)  # tapa superior razonable para CI
    logging.warning(f"⏳ Backoff intento {attempt}: durmiendo {delay:.1f}s...")
    time.sleep(delay)


def _chat_completion_with_retry(
    client,
    *,
    messages,
    model: Optional[str] = None,
    max_tokens: int = 600,
    temperature: float = 0.2,
):
    """
    Envoltorio robusto para una única llamada a chat.completions.create
    - Reintenta en 429/5xx y timeouts.
    - Respeta Retry-After si existe.
    - Propaga errores no recuperables.
    """
    # Intento de importar clases de error del SDK v1 (si existen)
    try:
        from openai import APITimeoutError, APIConnectionError, RateLimitError, APIStatusError
    except Exception:
        APITimeoutError = APIConnectionError = RateLimitError = APIStatusError = tuple()  # type: ignore

    use_model = model or _OPENAI_MODEL
    last_err = None

    for attempt in range(_OPENAI_MAX_RETRIES + 1):
        try:
            return client.chat.completions.create(
                model=use_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e:
            last_err = e
            # status_code si lo expone el SDK v1
            code = getattr(getattr(e, "response", None), "status_code", None)

            # Clasificar errores recuperables (timeout + 429/5xx)
            # Usamos heurística por texto además de clases del SDK.
            msg = str(e).lower()
            is_timeout = (
                isinstance(e, (APITimeoutError,)) or
                "timeout" in msg or "timed out" in msg
            )
            is_rate_or_5xx = (code in (429, 500, 502, 503, 504))

            if attempt < _OPENAI_MAX_RETRIES and (is_rate_or_5xx or is_timeout):
                logging.warning(
                    f"⚠️ OpenAI call falló (intento {attempt}/{_OPENAI_MAX_RETRIES}) "
                    f"code={code} model={use_model}: {e}"
                )
                _sleep_with_retry_after(e, attempt + 1)
                continue

            # No recuperable o sin intentos restantes
            logging.error(f"❌ OpenAI call error (final): code={code} model={use_model} {e}")
            raise

    # Si por alguna razón cae aquí, relanza el último error
    raise last_err  # pragma: no cover


def get_openai_responses(title: str, content: str):
    """
    Mantiene la firma original y las 3 llamadas:
    - título resumido
    - resumen estructurado (texto) -> postprocesado a JSON usando extract_section
    - impacto (texto) -> postprocesado a JSON usando extract_section

    Devuelve:
    (titulo_resumen: str, resumen_json: str, impacto_json: str)

    Comportamiento ante errores: captura excepciones y devuelve strings JSON vacíos,
    sin interrumpir el proceso (ideal para CI).
    """
    try:
        import openai
    except Exception as e:
        logging.error(f"❌ No se pudo importar el SDK de OpenAI: {e}")
        return "", json.dumps({}, ensure_ascii=False), json.dumps({}, ensure_ascii=False)

    # Cliente v1 con timeout; desactivamos reintentos internos para controlar nosotros
    try:
        client = openai.OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            timeout=_OPENAI_TIMEOUT,
            max_retries=0,
        )
    except Exception as e:
        logging.error(f"❌ No se pudo inicializar el cliente de OpenAI: {e}")
        return "", json.dumps({}, ensure_ascii=False), json.dumps({}, ensure_ascii=False)

    try:
        # -------------------------
        # 1) TÍTULO RESUMIDO
        # -------------------------
        title_prompt = (
            "Resume este título oficial en un máximo de 10 palabras, usando lenguaje claro y directo. "
            "Evita frases largas o lenguaje técnico. El resultado debe ser adecuado como título corto de una web informativa:\n\n"
            f"{title}"
        )
        title_messages = [
            {
                "role": "system",
                "content": (
                    "Resumes títulos del BOE de forma clara y accesible para el público general. "
                    "Devuelve solo el texto del título, sin comillas ni markdown."
                ),
            },
            {"role": "user", "content": title_prompt},
        ]

        title_resp = _chat_completion_with_retry(
            client,
            messages=title_messages,
            model=os.getenv("OPENAI_MODEL_TITLE", _OPENAI_MODEL),
            max_tokens=50,
            temperature=0.3,
        )
        titulo_resumen = (title_resp.choices[0].message.content or "").strip()
        titulo_resumen = titulo_resumen.rstrip(".").strip()

        # -------------------------
        # 2) RESUMEN ESTRUCTURADO
        # -------------------------
        resumen_prompt = f"""
Actúa como un experto asistente legal especializado en analizar publicaciones oficiales como el Boletín Oficial del Estado (BOE).

Tu tarea es leer el contenido proporcionado y generar un resumen estructurado, escrito en texto plano, con lenguaje claro, formal y neutral. El resultado debe seguir exactamente los siguientes encabezados, en el mismo orden, uno por línea, sin negritas, sin viñetas, sin guiones, sin emojis ni otros símbolos.

El resumen debe ser fiel al contenido, sin inventar información no presente en el texto original. Utiliza frases cortas, precisas y sin adornos. Sé directo, evita repeticiones o explicaciones sobre tu rol. No incluyas introducciones ni conclusiones adicionales fuera del formato requerido.

Encabezados requeridos:
Contexto:
Breve descripción del contexto legal o administrativo. Debe ocupar un solo párrafo.

Cambios clave:
Enumera los cambios más relevantes, uno por línea. No uses guiones ni símbolos.

Fechas clave:
Enumera fechas importantes con una breve descripción, una por línea. Ejemplo: 1 de enero de 2025: Entrada en vigor.

Conclusión:
Resumen final de implicaciones o próximos pasos relevantes. Debe ocupar un solo párrafo.

Contenido:
{content}
""".strip()

        resumen_messages = [
            {"role": "system", "content": "Eres un asistente legal. Devuelve solo texto plano sin markdown ni símbolos extra."},
            {"role": "user", "content": resumen_prompt},
        ]

        resumen_resp = _chat_completion_with_retry(
            client,
            messages=resumen_messages,
            model=os.getenv("OPENAI_MODEL_SUMMARY", _OPENAI_MODEL),
            max_tokens=900,
            temperature=0.2,
        )
        resumen_text = clean_code_block((resumen_resp.choices[0].message.content or "").strip())

        resumen_json = {
            "context": extract_section(resumen_text, "Contexto") or "",
            "key_changes": [
                line.strip()
                for line in (extract_section(resumen_text, "Cambios clave") or "").split("\n")
                if line.strip()
            ],
            "key_dates_events": [
                line.strip()
                for line in (extract_section(resumen_text, "Fechas clave") or "").split("\n")
                if line.strip()
            ],
            "conclusion": extract_section(resumen_text, "Conclusión") or "",
        }

        # -------------------------
        # 3) IMPACTO LEGISLATIVO
        # -------------------------
        impacto_prompt = f"""
Actúa como un analista legislativo con experiencia en la evaluación de normativas oficiales publicadas en el Boletín Oficial del Estado (BOE).

Tu tarea es analizar el contenido proporcionado y generar una evaluación del impacto en texto plano, usando exclusivamente los encabezados indicados, en el orden especificado. Cada ítem debe ir en una línea separada. No utilices guiones, viñetas, comillas, ni markdown. Sé concreto, preciso y objetivo. No incluyas introducciones ni explicaciones adicionales fuera del formato.

Encabezados requeridos:
Afectados:
¿Quiénes se ven impactados por esta normativa? Enuméralos, uno por línea.

Cambios operativos:
¿Qué cambios concretos introduce? Enuméralos, uno por línea.

Riesgos potenciales:
Riesgos o desafíos. Enuméralos, uno por línea.

Beneficios previstos:
Beneficios esperados. Enuméralos, uno por línea.

Recomendaciones:
Sugerencias para los afectados o entidades implicadas. Enuméralas, una por línea.

Contenido:
{content}
""".strip()

        impacto_messages = [
            {"role": "system", "content": "Eres un analista legislativo. Devuelve solo texto plano sin markdown."},
            {"role": "user", "content": impacto_prompt},
        ]

        impacto_resp = _chat_completion_with_retry(
            client,
            messages=impacto_messages,
            model=os.getenv("OPENAI_MODEL_IMPACT", _OPENAI_MODEL),
            max_tokens=900,
            temperature=0.2,
        )
        impacto_text = clean_code_block((impacto_resp.choices[0].message.content or "").strip())

        impacto_json = {
            "afectados": [
                line.strip()
                for line in (extract_section(impacto_text, "Afectados") or "").split("\n")
                if line.strip()
            ],
            "cambios_operativos": [
                line.strip()
                for line in (extract_section(impacto_text, "Cambios operativos") or "").split("\n")
                if line.strip()
            ],
            "riesgos_potenciales": [
                line.strip()
                for line in (extract_section(impacto_text, "Riesgos potenciales") or "").split("\n")
                if line.strip()
            ],
            "beneficios_previstos": [
                line.strip()
                for line in (extract_section(impacto_text, "Beneficios previstos") or "").split("\n")
                if line.strip()
            ],
            "recomendaciones": [
                line.strip()
                for line in (extract_section(impacto_text, "Recomendaciones") or "").split("\n")
                if line.strip()
            ],
        }

        # Devuelve exactamente como antes
        return (
            (titulo_resumen or "").strip(),
            json.dumps(resumen_json, ensure_ascii=False),
            json.dumps(impacto_json, ensure_ascii=False),
        )

    except Exception as e:
        # Cualquier error aquí no debe romper el pipeline
        logging.error(f"❌ Error con OpenAI: {e}")
        return "", json.dumps({}, ensure_ascii=False), json.dumps({}, ensure_ascii=False)
