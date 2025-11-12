# app/services/html_enricher.py
from __future__ import annotations

import os
import re
import time
import logging
from typing import Optional, Tuple, List
from urllib.parse import urlparse, parse_qs

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Fallbacks de parsing PDF/HTML
try:
    from bs4 import BeautifulSoup  # beautifulsoup4
except Exception:
    BeautifulSoup = None  # type: ignore

try:
    from pdfminer.high_level import extract_text as pdf_extract_text  # pdfminer.six
except Exception:
    pdf_extract_text = None  # type: ignore

# ───────────────── Config via ENV (solo enriquecimiento) ─────────────────
_ENRICH_CONNECT_TIMEOUT = float(os.getenv("ENRICH_CONNECT_TIMEOUT", "12"))
_ENRICH_READ_TIMEOUT    = float(os.getenv("ENRICH_READ_TIMEOUT", "35"))
_ENRICH_TOTAL_RETRIES   = int(os.getenv("ENRICH_TOTAL_RETRIES", "4"))
_ENRICH_BACKOFF_FACTOR  = float(os.getenv("ENRICH_BACKOFF_FACTOR", "0.8"))
_ENRICH_USER_AGENT      = os.getenv("ENRICH_USER_AGENT", "boe-enricher/1.0 (+github actions)")

# Accept rules (bajadas para no perder “B” cortas)
_MIN_GAIN_CHARS         = int(os.getenv("ENRICH_MIN_GAIN_CHARS", "200"))
_MIN_ABS_CHARS          = int(os.getenv("ENRICH_MIN_ABS_CHARS", "600"))
_MIN_BASE_EMPTY_ACCEPT  = int(os.getenv("ENRICH_MIN_BASE_EMPTY_CHARS", "200"))

_MIN_SLEEP = float(os.getenv("ENRICH_MIN_SLEEP", "0.10"))
_MAX_SLEEP = float(os.getenv("ENRICH_MAX_SLEEP", "0.30"))

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "User-Agent": _ENRICH_USER_AGENT,
}

# cache ligera para evitar re-requests
_CACHE: dict[str, str] = {}

def _sleep_jitter():
    import random
    time.sleep(random.uniform(_MIN_SLEEP, _MAX_SLEEP))

def _build_session() -> requests.Session:
    retry = Retry(
        total=_ENRICH_TOTAL_RETRIES,
        connect=_ENRICH_TOTAL_RETRIES,
        read=_ENRICH_TOTAL_RETRIES,
        status=_ENRICH_TOTAL_RETRIES,
        backoff_factor=_ENRICH_BACKOFF_FACTOR,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s = requests.Session()
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

_session = _build_session()

def _extract_id_from_url(url: str) -> Optional[str]:
    try:
        q = parse_qs(urlparse(url).query)
        if "id" in q and q["id"]:
            return q["id"][0]
    except Exception:
        pass
    return None

def _normalize_text(s: str) -> str:
    s = (s or "").replace("\u00A0", " ")
    s = re.sub(r"\r\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t\f\v]+", " ", s)
    return s.strip()

def _fetch_text_url(url: str) -> Optional[str]:
    _sleep_jitter()
    resp = _session.get(url, headers=_HEADERS, timeout=(_ENRICH_CONNECT_TIMEOUT, _ENRICH_READ_TIMEOUT))
    if 400 <= resp.status_code < 600:
        logging.info(f"enricher: HTTP {resp.status_code} en {url}")
        return None
    ct = (resp.headers.get("Content-Type") or "").lower()
    body = resp.content or b""
    if not body.strip():
        return None

    text = None
    if "text/plain" in ct or url.endswith("txt.php"):
        try:
            text = body.decode(resp.encoding or "utf-8", errors="replace")
        except Exception:
            text = body.decode("utf-8", errors="replace")
    else:
        if BeautifulSoup is None:
            return None
        try:
            soup = BeautifulSoup(body, "lxml") if "lxml" in str(BeautifulSoup) else BeautifulSoup(body, "html.parser")
            for sel in ["header", "nav", "footer", ".navbar", ".nav", ".footer", "script", "style", "noscript"]:
                for tag in soup.select(sel):
                    tag.decompose()
            text = soup.get_text(separator="\n")
        except Exception as e:
            logging.info(f"enricher: fallo parseando HTML {url}: {e}")
            return None

    return _normalize_text(text or "")

def _pdf_to_text(url_pdf: str) -> Optional[str]:
    if not url_pdf or pdf_extract_text is None:
        return None
    _sleep_jitter()
    try:
        with _session.get(url_pdf, headers={"User-Agent": _ENRICH_USER_AGENT}, timeout=(_ENRICH_CONNECT_TIMEOUT, _ENRICH_READ_TIMEOUT), stream=True) as r:
            if 400 <= r.status_code < 600:
                logging.info(f"enricher: PDF HTTP {r.status_code} en {url_pdf}")
                return None
            content = r.content
        import io
        txt = pdf_extract_text(io.BytesIO(content)) or ""
        return _normalize_text(txt)
    except Exception as e:
        logging.info(f"enricher: fallo extrayendo PDF {url_pdf}: {e}")
        return None

def _should_accept(base_len: int, cand_len: int) -> bool:
    gain = cand_len - base_len
    if gain >= _MIN_GAIN_CHARS:
        return True
    if cand_len >= _MIN_ABS_CHARS:
        return True
    if base_len < 80 and cand_len >= _MIN_BASE_EMPTY_ACCEPT:
        return True
    return False

def enrich_boe_text(
    identificador: str,
    url_html: Optional[str],
    url_txt_candidate: Optional[str],
    url_pdf: Optional[str],
    base_text: str,
    min_gain_chars: int = _MIN_GAIN_CHARS,
) -> Tuple[str, bool]:
    """
    Devuelve (texto_enriquecido, enriched_bool).
    Estrategia:
      1) TXT (txt.php?id=...)
      2) DOC/HTML (doc.php?id=... o url_html)
      3) PDF
    Acepta candidatos “útiles” aunque la ganancia sea pequeña.
    """
    try:
        if not identificador:
            return base_text, False

        if identificador in _CACHE:
            enriched = _CACHE[identificador]
            if len(enriched) > len(base_text):
                return enriched, True
            return base_text, False

        bid = None
        if url_html:
            bid = _extract_id_from_url(url_html)
        if not bid and url_txt_candidate:
            bid = _extract_id_from_url(url_txt_candidate)
        if not bid and identificador:
            bid = identificador

        candidates: List[str] = []
        if bid:
            candidates.append(f"https://www.boe.es/diario_boe/txt.php?id={bid}")
            candidates.append(f"https://www.boe.es/buscar/doc.php?id={bid}")
            candidates.append(f"https://www.boe.es/diario_boe/mostrar_datos.php?id={bid}")
        if url_html and url_html not in candidates:
            candidates.append(url_html)

        base_len = len(base_text or "")
        best = base_text or ""

        for url in candidates:
            try:
                t = _fetch_text_url(url)
            except requests.exceptions.RequestException as e:
                logging.info(f"enricher: error de red en {url}: {e}")
                t = None

            cand_len = len(t or "")
            if t and _should_accept(base_len, cand_len):
                logging.info(f"🧩 Enriquecido {identificador} con {url} (base={base_len} cand={cand_len} gain={cand_len-base_len})")
                _CACHE[identificador] = t
                return t, True
            else:
                logging.info(f"enricher: descartado {url} (base={base_len} cand={cand_len} gain={cand_len-base_len})")

        if url_pdf:
            pdf_text = _pdf_to_text(url_pdf)
            cand_len = len(pdf_text or "")
            if pdf_text and _should_accept(base_len, cand_len):
                logging.info(f"🧩 Enriquecido {identificador} desde PDF (base={base_len} cand={cand_len})")
                _CACHE[identificador] = pdf_text
                return pdf_text, True

        return best, best != (base_text or "")

    except Exception as e:
        logging.info(f"enricher: fallo general {identificador}: {e}")
        return base_text, False
