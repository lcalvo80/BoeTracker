# boe_fetcher.py
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from time import sleep

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

headers = {"Accept": "application/xml"}
#BASE_URL = "https://boe.es/datosabiertos/api/boe/sumario/{date}"
BASE_URL = "https://boe.es/datosabiertos/api/boe/sumario/20250909"

# Configura una sesión con reintentos a nivel de transporte (DNS/reset/5xx idempotentes)
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

def fetch_boe_xml(date_obj=None):
    """
    Mantiene la firma original. Devuelve un ElementTree root o None.
    Reintentos de transporte + manejo explícito de 404, timeouts y parseo.
    """
    if date_obj is None:
        date_obj = datetime.now()
    date_str = date_obj.strftime("%Y%m%d")
    url = BASE_URL.format(date=date_str)

    logging.info(f"🌐 Fetching BOE for {date_str} → {url}")

    try:
        # Timeout total por request (conexión + lectura)
        resp = _session.get(url, headers=headers, timeout=30)

        if resp.status_code == 404:
            logging.warning(f"📭 No BOE sumario available for {date_str}")
            return None

        if 400 <= resp.status_code < 600:
            # Si Retry no lo resolvió, dejamos constancia y salimos limpio
            logging.error(f"❌ HTTP {resp.status_code} fetching BOE {date_str}: {resp.text[:500]}")
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