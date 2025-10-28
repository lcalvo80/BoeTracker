from __future__ import annotations
from flask import Blueprint, jsonify, request
from app.services import items_svc

bp = Blueprint("meta", __name__)

@bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)

@bp.get("/filters")
def filters():
    """
    Respuesta estable:
    {
      "ok": true,
      "data": {
        "sections":     [{ "codigo": "...", "nombre": "..." }, ...],
        "departments":  [{ "codigo": "...", "nombre": "..." }, ...],
        "epigraphs":    ["...", "..."],
        // compat:
        "secciones":    [...],
        "departamentos":[...],
        "epigrafes":    [...]
      }
    }
    """
    try:
        sections = items_svc.list_secciones()         # [{codigo, nombre}]
        departments = items_svc.list_departamentos()  # [{codigo, nombre}]
        epigraphs = items_svc.list_epigrafes()        # [str]

        data = {
            "sections": sections,
            "departments": departments,
            "epigraphs": epigraphs,
            # compat ES
            "secciones": sections,
            "departamentos": departments,
            "epigrafes": epigraphs,
        }
        return jsonify({"ok": True, "data": data}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
