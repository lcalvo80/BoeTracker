# app/blueprints/resumen.py
from __future__ import annotations

from datetime import datetime, date as date_type
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request, current_app, make_response

from app.services import daily_summary_svc

bp = Blueprint("resumen", __name__)


@bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)


def _parse_date(value: str) -> Optional[date_type]:
    """
    Acepta:
      - YYYY-MM-DD
      - YYYY/MM/DD
      - DD-MM-YYYY
      - YYYYMMDD  (✅ necesario para /resumen/20260128)
    """
    s = (value or "").strip()
    if not s:
        return None

    # YYYYMMDD
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except Exception:
            return None

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass

    return None


def _to_iso(d: Optional[date_type]) -> Optional[str]:
    if d is None:
        return None
    return d.isoformat()


def _to_yyyymmdd(d: Optional[date_type]) -> str:
    if d is None:
        return ""
    return d.strftime("%Y%m%d")


def _cache_headers(resp, *, seconds: int = 300):
    resp.headers["Cache-Control"] = f"public, max-age={int(seconds)}, s-maxage={int(seconds)}"
    return resp


def _ok(data: Dict[str, Any], *, seconds: int = 300, status: int = 200):
    resp = make_response(jsonify({"ok": True, "data": data}), status)
    return _cache_headers(resp, seconds=seconds)


def _err(msg: str, *, status: int = 400):
    return jsonify({"ok": False, "error": msg}), status


@bp.get("", strict_slashes=False)
def get_resumen_latest_or_date():
    """Devuelve el resumen diario (por secciones).

    - Si se pasa ?date=YYYY-MM-DD o YYYYMMDD -> devuelve ese día.
    - Si no, devuelve el último día disponible en la tabla.
    """
    try:
        date_q = request.args.get("date") or request.args.get("fecha")
        d = _parse_date(date_q) if date_q else None

        if d is None:
            d = daily_summary_svc.get_latest_date()
            if d is None:
                return _ok({"fecha_publicacion": None, "secciones": []}, seconds=60)

        data = daily_summary_svc.get_daily_summary(fecha_publicacion=d)
        return _ok(data, seconds=300)
    except Exception as e:
        current_app.logger.exception("resumen.get_resumen_latest_or_date failed")
        return _err(str(e), status=500)


@bp.get("/index")
def list_index():
    """
    ✅ Endpoint que te faltaba:
    GET /api/resumen/index?limit=120&offset=0

    Devuelve:
      { ok:true, data:{ days:[{fecha_publicacion, yyyymmdd, title, meta_description, total_entradas, updated_at}] } }

    Nota:
    - No hacemos N queries (no cargamos cada día).
    - Construimos title/desc base para SEO desde la fecha.
    """
    try:
        limit = int(request.args.get("limit", "30"))
        offset = int(request.args.get("offset", "0"))
    except Exception:
        return _err("Parámetros inválidos", status=400)

    # límites razonables
    limit = max(1, min(limit, 365))
    offset = max(0, offset)

    try:
        dates = daily_summary_svc.list_available_dates(limit=limit, offset=offset)
        # dates puede venir como [date] o ["YYYY-MM-DD"]
        norm_dates: List[date_type] = []
        for x in (dates or []):
            if isinstance(x, date_type):
                norm_dates.append(x)
            else:
                d = _parse_date(str(x))
                if d is not None:
                    norm_dates.append(d)

        # Construimos "days" sin consultar el contenido
        days = []
        for d in norm_dates:
            iso = _to_iso(d)
            ymd = _to_yyyymmdd(d)

            # Title/desc SEO base (si luego quieres, los guardas en DB y los devolvemos aquí)
            title = f"Resumen BOE — {iso}"
            meta_description = (
                "Lo más relevante del BOE del día, resumido por secciones para empresa y compliance."
            )

            days.append(
                {
                    "fecha_publicacion": iso,
                    "yyyymmdd": ymd,
                    "title": title,
                    "meta_description": meta_description,
                    "total_entradas": None,
                    "updated_at": None,
                }
            )

        return _ok({"days": days}, seconds=600)
    except Exception as e:
        current_app.logger.exception("resumen.list_index failed")
        return _err(str(e), status=500)


@bp.get("/dates")
def list_dates():
    """Lista los días disponibles (para archivo) — compat legacy."""
    try:
        limit = int(request.args.get("limit", "30"))
        offset = int(request.args.get("offset", "0"))
        dates = daily_summary_svc.list_available_dates(limit=limit, offset=offset)

        # Aseguramos serialización: ["YYYY-MM-DD", ...]
        out = []
        for x in (dates or []):
            if isinstance(x, date_type):
                out.append(x.isoformat())
            else:
                d = _parse_date(str(x))
                out.append(d.isoformat() if d else str(x))

        return _ok({"dates": out}, seconds=600)
    except Exception as e:
        current_app.logger.exception("resumen.list_dates failed")
        return _err(str(e), status=500)


@bp.get("/<fecha>")
def get_resumen_by_date(fecha: str):
    """Resumen de un día concreto.
    Acepta:
      - /api/resumen/2026-01-28
      - /api/resumen/20260128
    """
    try:
        d = _parse_date(fecha)
        if d is None:
            return _err("Fecha inválida", status=400)

        data = daily_summary_svc.get_daily_summary(fecha_publicacion=d)
        return _ok(data, seconds=600)
    except Exception as e:
        current_app.logger.exception("resumen.get_resumen_by_date failed")
        return _err(str(e), status=500)
