# boe_fetcher.py
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, date
from typing import Optional, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re

headers = {
    "Accept": "application/xml",
    "User-Agent": "boe-updater/1.0 (+github actions)",
}
BASE_URL = "https://boe.es/datosabiertos/api/boe/sumario/{date}"  # date = YYYYMMDD


# ---------------------------
# Helpers
# ---------------------------
_DATE_ISO = "%Y-%m-%d"
_DATE_COMPACT = "%Y%m%d"

def _parse_date_like(date_like: Optional[Union[str, date, datetime]]) -> datetime:
    """
    Acepta:
      - None  -> ahora
      - datetime -> se usa tal cual
      - date -> a medianoche
      - str en 'YYYY-MM-DD' o 'YYYYMMDD'
    Devuelve datetime.
    """
    if date_like is None:
        return datetime.now()

    if isinstance(date_like, datetime):
        return date_like

    if isinstance(date_like, date):
        return datetime.combine(date_like, datetime.min.time())

    if isinstance(date_like, str):
        s = date_like.strip()
        # normalizar YYYYMMDD -> YYYY-MM-DD para el parse, si aplica
        if re.fullmatch(r"\d{8}", s):
            s = f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
        try:
            d = datetime.strptime(s, _DATE_ISO)
            return d
        except ValueError as e:
            raise ValueError(
                f"Formato de fecha inválido: '{date_like}'. Usa YYYY-MM-DD o YYYYMMDD."
            ) from e

    raise TypeError(f"Tipo de fecha no soportado: {type(date_like)!r}")


# ---------------------------
# HTTP session con reintentos
# ---------------------------
def _build_session(total_retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

_session = _build_session()


# ---------------------------
# Fetch principal
# ---------------------------
def fetch_boe_xml(date_obj: Optional[Union[str, date, datetime]] = None) -> Optional[ET.Element]:
    """
    Descarga el sumario del BOE para la fecha indicada y devuelve el Element root.
    - Maneja formatos de fecha flexibles (YYYY-MM-DD, YYYYMMDD, date, datetime).
    - Reintentos de transporte (DNS/reset/5xx/429) a nivel de sesión.
    - Manejo explícito de 404 como "no hay sumario ese día".
    - Timeouts separados (conexión, lectura).
    """
    dt = _parse_date_like(date_obj)
    date_str = dt.strftime(_DATE_COMPACT)
    url = BASE_URL.format(date=date_str)

    logging.info(f"🌐 Fetching BOE for {date_str} → {url}")

    try:
        # (connect_timeout, read_timeout)
        resp = _session.get(url, headers=headers, timeout=(10, 30))

        if resp.status_code == 404:
            logging.warning(f"📭 No BOE sumario available for {date_str}")
            return None

        if 400 <= resp.status_code < 600:
            logging.error(
                f"❌ HTTP {resp.status_code} fetching BOE {date_str}: {resp.text[:500]}"
            )
            return None

        # Validación de contenido
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "xml" not in ctype and not resp.content.strip().startswith(b"<"):
            logging.error(
                f"❌ Respuesta no XML (Content-Type='{ctype}') para {date_str}. "
                f"Tamaño={len(resp.content)} bytes"
            )
            return None

        if not resp.content or not resp.content.strip():
            logging.warning(f"⚠️ Respuesta vacía para {date_str}")
            return None

        try:
            return ET.fromstring(resp.content)
        except ET.ParseError as e:
            logging.error(f"❌ XML parsing error ({date_str}): {e}")
            return None

    except requests.exceptions.Timeout as e:
        logging.error(f"⏱️ Timeout fetching BOE ({date_str}): {e}")
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Request error fetching BOE ({date_str}): {e}")
    except Exception as e:
        logging.error(f"❌ Unexpected error fetching BOE ({date_str}): {e}")

    return None
