# app/scripts/collect_pagespeed.py
from __future__ import annotations

import os
import json
import datetime as dt
from typing import Any, Dict, Optional, Tuple, List

import requests
import psycopg2
from psycopg2.extras import Json


PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

DEFAULT_ROUTES = ["/", "/boe", "/resumen"]
DEFAULT_INCLUDE_LAST_DAYS = 7  # añade /resumen/YYYY-MM-DD últimos N días


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _base_url() -> str:
    base = _env("SITE_URL", "https://www.boetracker.com").rstrip("/")
    return base


def _routes() -> List[str]:
    raw = _env("PAGESPEED_ROUTES", "")
    if raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return parts
    return DEFAULT_ROUTES[:]


def _include_last_days() -> int:
    try:
        return int(_env("PAGESPEED_INCLUDE_LAST_DAYS", str(DEFAULT_INCLUDE_LAST_DAYS)) or "0")
    except Exception:
        return DEFAULT_INCLUDE_LAST_DAYS


def _build_urls() -> List[str]:
    base = _base_url()
    urls = []

    for r in _routes():
        r = "/" + r.lstrip("/")
        urls.append(f"{base}{r}")

    n = _include_last_days()
    if n > 0:
        today = dt.date.today()
        for i in range(n):
            d = today - dt.timedelta(days=i)
            urls.append(f"{base}/resumen/{d.isoformat()}")

    # dedup manteniendo orden
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _psi_call(url: str, strategy: str, api_key: str, timeout: int = 60) -> Dict[str, Any]:
    params = {
        "url": url,
        "strategy": strategy,
        "key": api_key,
        # Puedes añadir category=performance (default) o más categorías si quieres
    }
    r = requests.get(PSI_ENDPOINT, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _get_audit_numeric(lhr: Dict[str, Any], audit_id: str) -> Optional[float]:
    audits = (lhr or {}).get("audits") or {}
    a = audits.get(audit_id) or {}
    v = a.get("numericValue")
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _parse_metrics(payload: Dict[str, Any]) -> Tuple[Optional[float], Optional[int], Optional[int], Optional[float], Optional[int], Optional[int], Optional[int]]:
    lhr = (payload or {}).get("lighthouseResult") or {}
    cats = (lhr.get("categories") or {}).get("performance") or {}
    score = cats.get("score")
    try:
        lighthouse_score = float(score) * 100.0 if score is not None else None
    except Exception:
        lighthouse_score = None

    lcp = _get_audit_numeric(lhr, "largest-contentful-paint")  # ms
    cls = _get_audit_numeric(lhr, "cumulative-layout-shift")   # unitless

    # INP: audit suele ser "interaction-to-next-paint" (ms) en versiones recientes
    inp = _get_audit_numeric(lhr, "interaction-to-next-paint")
    if inp is None:
        # fallback: max-potential-fid (no es INP, pero mejor que nada)
        inp = _get_audit_numeric(lhr, "max-potential-fid")

    ttfb = _get_audit_numeric(lhr, "server-response-time")  # ms
    fcp = _get_audit_numeric(lhr, "first-contentful-paint")  # ms
    tbt = _get_audit_numeric(lhr, "total-blocking-time")     # ms

    def to_int(x: Optional[float]) -> Optional[int]:
        if x is None:
            return None
        return int(round(x))

    return (
        lighthouse_score,
        to_int(lcp),
        to_int(inp),
        float(cls) if cls is not None else None,
        to_int(ttfb),
        to_int(fcp),
        to_int(tbt),
    )


def _db_conn():
    db_url = _env("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL is required")
    return psycopg2.connect(db_url)


def insert_snapshot(conn, url: str, strategy: str, payload: Dict[str, Any]) -> None:
    (score, lcp_ms, inp_ms, cls, ttfb_ms, fcp_ms, tbt_ms) = _parse_metrics(payload)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO perf_snapshots
              (url, strategy, lighthouse_score, lcp_ms, inp_ms, cls, ttfb_ms, fcp_ms, tbt_ms, payload)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                url,
                strategy,
                score,
                lcp_ms,
                inp_ms,
                cls,
                ttfb_ms,
                fcp_ms,
                tbt_ms,
                json.dumps(payload),
            ),
        )


def main() -> int:
    api_key = _env("PAGESPEED_API_KEY", "")
    if not api_key:
        raise RuntimeError("PAGESPEED_API_KEY is required")

    urls = _build_urls()
    strategies = ["mobile", "desktop"]

    conn = _db_conn()
    try:
        for u in urls:
            for s in strategies:
                payload = _psi_call(u, s, api_key)
                insert_snapshot(conn, u, s, payload)
                conn.commit()
                print(f"[pagespeed] stored: {s} {u}")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
