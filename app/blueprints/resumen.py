# app/blueprints/resumen.py
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request, current_app, make_response

from app.services import daily_summary_svc


bp = Blueprint("resumen", __name__)


@bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)


def _parse_date(value: str):
    s = (value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def _cache_headers(resp, *, seconds: int = 300):
    resp.headers["Cache-Control"] = f"public, max-age={int(seconds)}, s-maxage={int(seconds)}"
    return resp


@bp.get("", strict_slashes=False)
def get_resumen_latest_or_date():
    """Devuelve el resumen diario (por secciones).

    - Si se pasa ?date=YYYY-MM-DD -> devuelve ese día.
    - Si no, devuelve el último día disponible en la tabla.
    """
    try:
        date_q = request.args.get("date") or request.args.get("fecha")
        d = _parse_date(date_q) if date_q else None

        if d is None:
            d = daily_summary_svc.get_latest_date()
            if d is None:
                resp = make_response(jsonify({"ok": True, "data": {"fecha_publicacion": None, "secciones": []}}), 200)
                return _cache_headers(resp, seconds=60)

        data = daily_summary_svc.get_daily_summary(fecha_publicacion=d)
        resp = make_response(jsonify({"ok": True, "data": data}), 200)
        return _cache_headers(resp, seconds=300)
    except Exception as e:
        current_app.logger.exception("resumen.get_resumen_latest_or_date failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/dates")
def list_dates():
    """Lista los días disponibles (para archivo)."""
    try:
        limit = int(request.args.get("limit", "30"))
        offset = int(request.args.get("offset", "0"))
        dates = daily_summary_svc.list_available_dates(limit=limit, offset=offset)
        resp = make_response(jsonify({"ok": True, "data": {"dates": dates}}), 200)
        return _cache_headers(resp, seconds=600)
    except Exception as e:
        current_app.logger.exception("resumen.list_dates failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/<fecha>")
def get_resumen_by_date(fecha: str):
    """Resumen de un día concreto."""
    try:
        d = _parse_date(fecha)
        if d is None:
            return jsonify({"ok": False, "error": "Fecha inválida"}), 400

        data = daily_summary_svc.get_daily_summary(fecha_publicacion=d)
        resp = make_response(jsonify({"ok": True, "data": data}), 200)
        return _cache_headers(resp, seconds=600)
    except Exception as e:
        current_app.logger.exception("resumen.get_resumen_by_date failed")
        return jsonify({"ok": False, "error": str(e)}), 500
