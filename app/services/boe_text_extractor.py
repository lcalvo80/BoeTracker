# app/services/boe_text_extractor.py
from __future__ import annotations

import logging
import os
import random
import re
import time
from typing import Optional, Tuple
from urllib.parse import urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_LOG = logging.getLogger(__name__)

# ───────────────── Config por entorno ─────────────────
# Timeouts separados: connect pequeño, read más grande (PDFs).
_PDF_CONNECT_TIMEOUT = float(os.getenv("BOE_PDF_CONNECT_TIMEOUT", "10"))
_PDF_READ_TIMEOUT = float(os.getenv("BOE_PDF_READ_TIMEOUT", "120"))

# Presupuesto total por PDF (evita bloquear el run).
_PDF_TOTAL_BUDGET_SECS = float(os.getenv("BOE_PDF_TOTAL_BUDGET_SECS", "60"))

# Reintentos (baratos) + backoff.
_PDF_RETRIES_TOTAL = int(os.getenv("BOE_PDF_RETRIES_TOTAL", "3"))
_PDF_RETRIES_CONNECT = int(os.getenv("BOE_PDF_RETRIES_CONNECT", str(_PDF_RETRIES_TOTAL)))
_PDF_RETRIES_READ = int(os.getenv("BOE_PDF_RETRIES_READ", str(_PDF_RETRIES_TOTAL)))
_PDF_BACKOFF_FACTOR = float(os.getenv("BOE_PDF_BACKOFF_FACTOR", "0.6"))

# Pool/Keep-alive
_PDF_POOL_CONNECTIONS = int(os.getenv("BOE_PDF_POOL_CONNECTIONS", "20"))
_PDF_POOL_MAXSIZE = int(os.getenv("BOE_PDF_POOL_MAXSIZE", "20"))

# Validaciones
_PDF_MIN_BYTES = int(os.getenv("BOE_PDF_MIN_BYTES", "5000"))
_PDF_MAX_BYTES = int(os.getenv("BOE_PDF_MAX_BYTES", str(25 * 1024 * 1024)))  # 25MB
_PDF_EXPECT_CONTENT_TYPE = os.getenv("BOE_PDF_EXPECT_CONTENT_TYPE", "application/pdf").lower()

# UA
_USER_AGENT = os.getenv("BOE_PDF_USER_AGENT", "boe-text-extractor/2.0 (+github actions)")

# Host fallback (www <-> sin www)
_ENABLE_HOST_FALLBACK = os.getenv("BOE_PDF_ENABLE_HOST_FALLBACK", "1") == "1"

# Forzar IPv4 (si tu runtime tiene problemas con IPv6/peering)
# Nota: requests no trae "force ipv4" nativo sin hacks. Lo dejamos como placeholder.
# Si hiciera falta, se aborda a nivel sistema/DNS o con resolver custom.
# _FORCE_IPV4 = os.getenv("BOE_PDF_FORCE_IPV4", "0") == "1"


# ───────────────── Sesión HTTP global ─────────────────
def _build_http_session() -> requests.Session:
    """
    Sesión global con pooling y reintentos.
    - Retries aquí cubren errores transitorios (429/5xx) + algunos de conexión.
    - Para connect timeout agresivo + presupuesto total usamos lógica adicional fuera.
    """
    s = requests.Session()

    retry = Retry(
        total=_PDF_RETRIES_TOTAL,
        connect=_PDF_RETRIES_CONNECT,
        read=_PDF_RETRIES_READ,
        backoff_factor=_PDF_BACKOFF_FACTOR,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=_PDF_POOL_CONNECTIONS,
        pool_maxsize=_PDF_POOL_MAXSIZE,
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)

    s.headers.update(
        {
            "User-Agent": _USER_AGENT,
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Connection": "keep-alive",
        }
    )
    return s


_SESSION = _build_http_session()


# ───────────────── Utilidades ─────────────────
def _clean_text(txt: str) -> str:
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\s*\n\s*", "\n", txt)
    return txt.strip()


def _flip_www(url: str) -> str:
    """
    https://www.boe.es/... <-> https://boe.es/...
    """
    try:
        p = urlparse(url)
        host = p.netloc.lower()
        if host.startswith("www."):
            new_host = host[4:]
        else:
            new_host = "www." + host
        return urlunparse((p.scheme, new_host, p.path, p.params, p.query, p.fragment))
    except Exception:
        return url


def _looks_like_pdf(resp: requests.Response, content_head: bytes) -> bool:
    ctype = (resp.headers.get("Content-Type") or "").lower()
    # A veces viene "application/pdf; charset=binary"
    if _PDF_EXPECT_CONTENT_TYPE and _PDF_EXPECT_CONTENT_TYPE in ctype:
        return True
    # Heurística: PDF empieza por %PDF
    if content_head.startswith(b"%PDF"):
        return True
    return False


def _bounded_sleep(attempt: int) -> None:
    """
    Backoff exponencial con jitter, acotado.
    Esto es adicional al backoff del Retry de urllib3: lo aplicamos cuando
    detectamos timeouts/connectivity para evitar martillar.
    """
    base = (1.8 ** attempt)  # 1.8, 3.24, 5.83...
    jitter = 0.85 + 0.3 * random.random()
    delay = max(0.6, min(base * jitter, 8.0))
    time.sleep(delay)


def _extract_pdf_pymupdf(pdf_bytes: bytes) -> str:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parts = []
    for page in doc:
        parts.append(page.get_text("text"))
    return _clean_text("\n".join(parts))


def _extract_pdf_pdfminer(pdf_bytes: bytes) -> str:
    from io import BytesIO
    from pdfminer.high_level import extract_text

    text = extract_text(BytesIO(pdf_bytes))
    return _clean_text(text)


def _download_pdf_streaming(
    url_pdf: str,
    *,
    timeout: Tuple[float, float],
    budget_deadline_ts: float,
) -> Optional[bytes]:
    """
    Descarga en streaming y valida que sea PDF.
    Respeta un presupuesto total por PDF.
    """
    # Si ya estamos fuera de presupuesto, no intentamos.
    if time.time() >= budget_deadline_ts:
        return None

    try:
        # HEAD puede ahorrar tiempo si el servidor responde bien, pero no es fiable.
        # Lo dejamos como opcional implícito: vamos directo a GET streaming.
        with _SESSION.get(url_pdf, timeout=timeout, stream=True) as resp:
            if not resp.ok:
                _LOG.warning(
                    "PDF GET no OK %s (status=%s)",
                    url_pdf,
                    resp.status_code,
                )
                return None

            # Leemos un pequeño prefijo para validar PDF/ctype
            head = resp.raw.read(1024, decode_content=True) or b""
            if not _looks_like_pdf(resp, head):
                ctype = (resp.headers.get("Content-Type") or "").lower()
                _LOG.warning(
                    "Respuesta no parece PDF %s (Content-Type=%s, head=%s)",
                    url_pdf,
                    ctype,
                    head[:20],
                )
                return None

            # Acumulamos bytes con límite
            chunks = [head]
            total = len(head)

            # Continuamos leyendo el resto
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue

                total += len(chunk)
                if total > _PDF_MAX_BYTES:
                    _LOG.warning(
                        "PDF supera max bytes (%s > %s) %s",
                        total,
                        _PDF_MAX_BYTES,
                        url_pdf,
                    )
                    return None

                chunks.append(chunk)

                # presupuesto total (evita quedarnos bloqueados)
                if time.time() >= budget_deadline_ts:
                    _LOG.warning("Presupuesto PDF agotado durante descarga: %s", url_pdf)
                    return None

            if total < _PDF_MIN_BYTES:
                _LOG.warning(
                    "PDF demasiado pequeño (%s bytes) %s",
                    total,
                    url_pdf,
                )
                return None

            return b"".join(chunks)

    except requests.exceptions.Timeout as e:
        _LOG.warning("Timeout descargando PDF %s: %s", url_pdf, e)
        return None
    except requests.exceptions.RequestException as e:
        _LOG.warning("Error de red descargando PDF %s: %s", url_pdf, e)
        return None
    except Exception as e:
        _LOG.warning("Error inesperado descargando PDF %s: %s", url_pdf, e)
        return None


def _fetch_pdf_bytes(url_pdf: str) -> Optional[bytes]:
    """
    Descarga resiliente:
    - connect timeout bajo + read alto
    - presupuesto total por PDF
    - retries controlados y backoff con jitter
    - fallback host www <-> sin www
    """
    timeout = (_PDF_CONNECT_TIMEOUT, _PDF_READ_TIMEOUT)
    deadline = time.time() + max(1.0, _PDF_TOTAL_BUDGET_SECS)

    candidates = [url_pdf]
    if _ENABLE_HOST_FALLBACK:
        alt = _flip_www(url_pdf)
        if alt != url_pdf:
            candidates.append(alt)

    last_err = None
    attempt = 0

    # Intentamos alternando candidates; cada candidate puede probar varias veces,
    # pero el presupuesto corta en seco.
    while time.time() < deadline and attempt < 6:
        for u in candidates:
            if time.time() >= deadline:
                break

            pdf = _download_pdf_streaming(u, timeout=timeout, budget_deadline_ts=deadline)
            if pdf:
                if u != url_pdf:
                    _LOG.info("✅ PDF descargado usando host alternativo: %s", u)
                return pdf

            # falló: backoff pequeño antes de probar siguiente
            attempt += 1
            if time.time() >= deadline:
                break
            _bounded_sleep(attempt)

    return None


def extract_boe_text(identificador: str, url_pdf: str) -> str:
    """
    Extrae SIEMPRE el texto del PDF del BOE.

    - Si no se consigue texto suficiente, devuelve "" para que la capa superior decida
      (p.ej. reintentos offline / pending / etc.).
    """
    if not url_pdf:
        _LOG.error("extract_boe_text llamado sin url_pdf (identificador=%s)", identificador)
        return ""

    pdf_bytes = _fetch_pdf_bytes(url_pdf)
    if not pdf_bytes:
        return ""

    # 1) PyMuPDF
    try:
        text = _extract_pdf_pymupdf(pdf_bytes)
        if len(text) >= 200:
            return text
        _LOG.warning(
            "Texto demasiado corto con PyMuPDF para %s (%s chars)",
            identificador,
            len(text),
        )
    except Exception as e:
        _LOG.info("PyMuPDF falló para %s: %s", identificador, e)

    # 2) pdfminer fallback
    try:
        text = _extract_pdf_pdfminer(pdf_bytes)
        if len(text) >= 200:
            return text
        _LOG.warning(
            "Texto demasiado corto con pdfminer para %s (%s chars)",
            identificador,
            len(text),
        )
    except Exception as e:
        _LOG.warning("pdfminer.six falló para %s: %s", identificador, e)

    return ""