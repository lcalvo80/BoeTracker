# app/services/boe_fetcher.py
from __future__ import annotations

import os
import re
import logging
from datetime import datetime, date, time as dtime
from typing import Optional, Union

# Seguridad en parseo XML si está disponible
try:
    from defusedxml import ElementTree as ET  # pip install defusedxml
except Exception:  # fallback
    import xml.etree.ElementTree as ET  # type: ignore

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ───────────────── Config por entorno ─────────────────
_BOE_BASE_URL = os.getenv(
    "BOE_BASE_URL",
    "https://boe.es/datosabiertos/api/boe/sumario/{date}",  # date = YYYYMMDD
)
_BOE_ACCEPT = os.getenv("BOE_ACCEPT", "application/xml")
_BOE_USER_AGENT = os.getenv("BOE_USER_AGENT", "boe-updater/1.0 (+github actions)")

# Timeouts
_BOE_CONNECT_TIMEOUT = float(os.getenv("BOE_CONNECT_TIMEOUT", "10"))
_BOE_READ_TIMEOUT = float(os.getenv("BOE_READ_TIMEOUT", "30"))

# Reintentos
_BOE_TOTAL_RETRIES = int(os.getenv("BOE_TOTAL_RETRIES", "3"))
_BOE_BACKOFF_FACTOR = float(os.getenv("BOE_BACKOFF_FACTOR", "0.5"))

# Zona horaria (por defecto Europe/Madrid)
_DEFAULT_TZ_NAME = os.getenv("BOE_TZ", "Europe/Madrid")
try:
    from zoneinfo import ZoneInfo  # Python >= 3.9
    _DEFAULT_TZ = ZoneInfo(_DEFAULT_TZ_NAME)
except Exception:
    _DEFAULT_TZ = None  # naive

headers = {
    "Accept": _BOE_ACCEPT,
    "User-Agent": _BOE_USER_AGENT,
}

_DATE_ISO = "%Y-%m-%d"
_DATE_COMPACT = "%Y%m%d"

def _to_local_midnight(dt: datetime) -> datetime:
    if _DEFAULT_TZ is None:
        return datetime.combine(dt.date(), dtime.min)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_DEFAULT_TZ)
    else:
        dt = dt.astimezone(_DEFAULT_TZ)
    return datetime.combine(dt.date(), dtime.min, tzinfo=_DEFAULT_TZ)

def _parse_date_like(date_like: Optional[Union[str, date, datetime]]) -> datetime:
    if date_like is None:
        now = datetime.now(_DEFAULT_TZ) if _DEFAULT_TZ else datetime.now()
        return _to_local_midnight(now)

    if isinstance(date_like, datetime):
        return _to_local_midnight(date_like)

    if isinstance(date_like, date):
        base = datetime.combine(date_like, dtime.min)
        return base.replace(tzinfo=_DEFAULT_TZ) if _DEFAULT_TZ else base

    if isinstance(date_like, str):
        s = date_like.strip()
        if re.fullmatch(r"\d{8}", s):
            s = f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
        try:
            d = datetime.strptime(s, _DATE_ISO)
        except ValueError as e:
            raise ValueError(
                f"Formato de fecha inválido: '{date_like}'. Usa YYYY-MM-DD o YYYYMMDD."
            ) from e
        return d.replace(tzinfo=_DEFAULT_TZ) if _DEFAULT_TZ else d

    raise TypeError(f"Tipo de fecha no soportado: {type(date_like)!r}")

def _build_session(
    total_retries: int = _BOE_TOTAL_RETRIES,
    backoff_factor: float = _BOE_BACKOFF_FACTOR,
) -> requests.Session:
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

def fetch_boe_xml(date_obj: Optional[Union[str, date, datetime]] = None) -> Optional[ET.Element]:
    """
    Descarga el sumario del BOE (YYYYMMDD) y devuelve el Element root.
    - TZ por defecto Europe/Madrid.
    - 404 se considera 'no hay sumario' → None (el caller decide).
    """
    dt = _parse_date_like(date_obj)
    dt_local_midnight = _to_local_midnight(dt)
    date_str = dt_local_midnight.strftime(_DATE_COMPACT)
    url = _BOE_BASE_URL.format(date=date_str)

    logging.info(f"🌐 Fetching BOE for {date_str} → {url}")

    try:
        resp = _session.get(
            url, headers=headers, timeout=(_BOE_CONNECT_TIMEOUT, _BOE_READ_TIMEOUT)
        )

        if resp.status_code == 404:
            logging.info(f"📭 BOE sin sumario para {date_str}")
            return None

        if 400 <= resp.status_code < 600:
            body_preview = (resp.text or "")[:500]
            logging.error(f"❌ HTTP {resp.status_code} al obtener {date_str}: {body_preview}")
            return None

        ctype = (resp.headers.get("Content-Type") or "").lower()
        content = resp.content or b""
        size = len(content)

        if size == 0 or not content.strip():
            logging.warning(f"⚠️ Respuesta vacía para {date_str}")
            return None

        looks_like_xml = content.strip().startswith(b"<")
        if "xml" not in ctype and not looks_like_xml:
            logging.error(
                f"❌ Respuesta no XML (Content-Type='{ctype}') para {date_str}. Tamaño={size} bytes"
            )
            return None

        try:
            return ET.fromstring(content)
        except Exception as e:
            logging.error(f"❌ Error de parseo XML ({date_str}): {e}")
            return None

    except requests.exceptions.Timeout as e:
        logging.error(f"⏱️ Timeout al obtener BOE ({date_str}): {e}")
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Error de red al obtener BOE ({date_str}): {e}")
    except Exception as e:
        logging.error(f"❌ Error inesperado al obtener BOE ({date_str}): {e}")

    return None
