# app/services/boe_text_extractor.py
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_LOG = logging.getLogger(__name__)

# ───────────────── Config por entorno ─────────────────
# Subimos un poco los timeouts y reintentos, pero los hacemos configurables.
_PDF_TIMEOUT_SECS = float(os.getenv("BOE_PDF_TIMEOUT_SECS", "90"))  # antes 30
_PDF_RETRIES_TOTAL = int(os.getenv("BOE_PDF_RETRIES_TOTAL", "5"))   # antes 3
_PDF_BACKOFF_FACTOR = float(os.getenv("BOE_PDF_BACKOFF_FACTOR", "1.0"))  # antes 0.8

# Si no se especifican connect/read, usamos el mismo valor que total
_PDF_RETRIES_CONNECT = int(os.getenv("BOE_PDF_RETRIES_CONNECT", str(_PDF_RETRIES_TOTAL)))
_PDF_RETRIES_READ = int(os.getenv("BOE_PDF_RETRIES_READ", str(_PDF_RETRIES_TOTAL)))


def _http_session() -> requests.Session:
    """
    Crea una sesión HTTP con reintentos para descargar PDFs del BOE.
    Ahora con más reintentos y backoff configurable.
    """
    s = requests.Session()
    retry = Retry(
        total=_PDF_RETRIES_TOTAL,
        connect=_PDF_RETRIES_CONNECT,
        read=_PDF_RETRIES_READ,
        backoff_factor=_PDF_BACKOFF_FACTOR,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "boe-text-extractor/1.1"})
    return s


def _clean_text(txt: str) -> str:
    """
    Normaliza el texto extraído del PDF: espacios y saltos de línea razonables.
    """
    # Normalizar espacios
    txt = re.sub(r"[ \t]+", " ", txt)
    # Normalizar saltos de línea
    txt = re.sub(r"\s*\n\s*", "\n", txt)
    return txt.strip()


def _extract_pdf_pymupdf(pdf_bytes: bytes) -> str:
    """
    Extracción de texto usando PyMuPDF (fitz).
    """
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parts = []
    for page in doc:
        parts.append(page.get_text("text"))
    return _clean_text("\n".join(parts))


def _extract_pdf_pdfminer(pdf_bytes: bytes) -> str:
    """
    Fallback: extracción de texto usando pdfminer.six.
    """
    from io import BytesIO
    from pdfminer.high_level import extract_text

    text = extract_text(BytesIO(pdf_bytes))
    return _clean_text(text)


def _fetch_pdf_bytes(url_pdf: str, timeout: Optional[float] = None) -> Optional[bytes]:
    """
    Descarga el PDF desde url_pdf y devuelve los bytes si parece válido.

    - Usa sesión con reintentos.
    - Captura errores de red (Timeout, ConnectionError, etc.) y devuelve None.
    """
    if not timeout or timeout <= 0:
        timeout = _PDF_TIMEOUT_SECS

    s = _http_session()
    try:
        resp = s.get(url_pdf, timeout=timeout)
    except requests.RequestException as e:
        _LOG.warning(
            "Error de red descargando PDF desde %s: %s",
            url_pdf,
            e,
        )
        return None

    if resp.ok and resp.content and len(resp.content) > 5000:
        return resp.content

    _LOG.warning(
        "PDF inválido o demasiado pequeño desde %s (status=%s, len=%s)",
        url_pdf,
        resp.status_code,
        len(resp.content) if resp.content else 0,
    )
    return None


def extract_boe_text(identificador: str, url_pdf: str) -> str:
    """
    Extrae SIEMPRE el texto del PDF del BOE.

    - identificador se usa solo para logs/tracking (BOE-A-..., BOE-B-...).
    - url_pdf es obligatorio y debe ser la URL directa al PDF del BOE.
    - Si no se consigue texto suficiente, devuelve "" para que la capa superior
      (openai_service) haga fallback a contenido mínimo (título / cuerpo_base).
    """
    if not url_pdf:
        _LOG.error(
            "extract_boe_text llamado sin url_pdf (identificador=%s)", identificador
        )
        return ""

    try:
        pdf_bytes = _fetch_pdf_bytes(url_pdf)
        if not pdf_bytes:
            # Ya se ha logado el motivo en _fetch_pdf_bytes
            return ""

        # 1) Intento con PyMuPDF
        try:
            text = _extract_pdf_pymupdf(pdf_bytes)
            if len(text) >= 50:
                return text
            _LOG.warning(
                "Texto demasiado corto con PyMuPDF para %s (%s chars)",
                identificador,
                len(text),
            )
        except Exception as e:
            _LOG.info("PyMuPDF falló para %s: %s", identificador, e)

        # 2) Fallback pdfminer.six
        try:
            text = _extract_pdf_pdfminer(pdf_bytes)
            if len(text) >= 50:
                return text
            _LOG.warning(
                "Texto demasiado corto con pdfminer para %s (%s chars)",
                identificador,
                len(text),
            )
        except Exception as e:
            _LOG.warning("pdfminer.six falló para %s: %s", identificador, e)

    except Exception as e:
        _LOG.error(
            "Error general descargando/extrayendo PDF (%s, %s): %s",
            identificador,
            url_pdf,
            e,
        )

    # Si llegamos aquí, no hemos conseguido texto útil
    return ""
