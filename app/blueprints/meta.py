from __future__ import annotations

from flask import Blueprint, jsonify, request
from app.services import items_svc
from app.services.lookup import (
    list_secciones_lookup,
    list_departamentos_lookup,
)

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

@bp.get("/lookups")
def lookups():
    """
    Devuelve los *diccionarios canónicos* desde tablas lookup para que el FE
    pueda resolver códigos -> nombres con fiabilidad. Incluye arrays y mapas.

    Respuesta:
    {
      "ok": true,
      "data": {
        "secciones_lookup":     [{codigo,nombre}, ...],
        "departamentos_lookup": [{codigo,nombre}, ...],
        "secDict": { "<codigo>": "nombre", ... },
        "depDict": { "<codigo>": "nombre", ... }
      }
    }
    """
    try:
        secciones = list_secciones_lookup()       # [{codigo,nombre}]
        departamentos = list_departamentos_lookup()

        secDict = {row["codigo"]: row["nombre"] for row in secciones if row.get("codigo")}
        depDict = {row["codigo"]: row["nombre"] for row in departamentos if row.get("codigo")}

        data = {
            "secciones_lookup": secciones,
            "departamentos_lookup": departamentos,
            "secDict": secDict,
            "depDict": depDict,
        }
        return jsonify({"ok": True, "data": data}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
