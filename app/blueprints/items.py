# app/blueprints/items.py
from __future__ import annotations
from flask import Blueprint, jsonify, request, current_app, make_response
from datetime import datetime

from app.services import items_svc

bp = Blueprint("items", __name__)

# ===== Helpers =====
def _safe_int(v, d, mi=1, ma=100):
    try:
        n = int(v)
        if n < mi: n = mi
        if n > ma: n = ma
        return n
    except Exception:
        return d

def _safe_bool(v, default=None):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"1", "true", "t", "yes", "y", "on"}:  return True
    if s in {"0", "false", "f", "no", "n", "off"}: return False
    return default

def _safe_date(v, default=None):
    if not v:
        return default
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return default

def _dedupe_preserve_order(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

MULTI_KEYS_PLURALS = {"departamentos", "secciones", "epigrafes", "tags", "ids"}
NORMALIZE_KEYS = {
    "departamento": "departamentos", "departamentos": "departamentos",
    "seccion": "secciones", "secciones": "secciones",
    "epigrafe": "epigrafes", "epigrafes": "epigrafes",
    "tag": "tags", "tags": "tags",
    "id": "ids", "ids": "ids",
    "seccion_codigo": "secciones",
    "departamento_codigo": "departamentos",
    "q": "q", "query": "q", "search": "q", "q_adv": "q",
    "fecha": "fecha",
    "fecha_desde": "fecha_desde", "desde": "fecha_desde", "from": "fecha_desde", "date_from": "fecha_desde",
    "fecha_hasta": "fecha_hasta", "hasta": "fecha_hasta", "to": "fecha_hasta", "date_to": "fecha_hasta",
    "useRange": "useRange",
    "has_resumen": "has_resumen",
    "has_impacto": "has_impacto",
    "has_comments": "has_comments",
    "favoritos": "favoritos",
    "destacado": "destacado",
    "page": "page", "limit": "limit",
    "sort_by": "sort_by", "sort_dir": "sort_dir",
}
ALLOWED_SORT_BY = {
    "created_at": "created_at",
    "fecha": "fecha",
    "updated_at": "updated_at",
    "relevancia": "relevancia",
    "relevance": "relevancia",
    "titulo": "titulo",
}
ALLOWED_SORT_DIR = {"asc", "desc"}

def _parse_query_args(args):
    data = {}
    for raw_key, raw_val in args.items(multi=True):
        key = raw_key[:-2] if raw_key.endswith("[]") else raw_key
        norm = NORMALIZE_KEYS.get(key, key)

        if norm in MULTI_KEYS_PLURALS:
            parts = [p.strip() for p in str(raw_val).split(",") if p.strip() != ""]
            prev = data.get(norm, [])
            data[norm] = prev + parts
        else:
            data[norm] = raw_val

    for m in list(MULTI_KEYS_PLURALS):
        if m in data:
            data[m] = _dedupe_preserve_order(data[m])

    if "q" in data and data["q"] is not None:
        data["q"] = (str(data["q"]).strip() or None)

    for flag in ("has_resumen", "has_impacto", "has_comments", "favoritos", "destacado", "useRange"):
        if flag in data:
            data[flag] = _safe_bool(data[flag])

    fecha_exacta = _safe_date(data.get("fecha"))
    fecha_desde = _safe_date(data.get("fecha_desde"))
    fecha_hasta = _safe_date(data.get("fecha_hasta"))

    use_range = _safe_bool(data.get("useRange"), False)
    if use_range is False and fecha_exacta:
        data["fecha_desde"] = fecha_exacta
        data["fecha_hasta"] = fecha_exacta
        data.pop("fecha", None)
    else:
        if fecha_desde: data["fecha_desde"] = fecha_desde
        else:           data.pop("fecha_desde", None)
        if fecha_hasta: data["fecha_hasta"] = fecha_hasta
        else:           data.pop("fecha_hasta", None)
        data.pop("fecha", None)

    data["page"]  = _safe_int(data.get("page", 1), 1, 1, 1_000_000)
    data["limit"] = _safe_int(data.get("limit", 12), 12, 1, 100)

    raw_sort_by  = str(data.get("sort_by", "created_at") or "created_at").strip().lower()
    raw_sort_dir = str(data.get("sort_dir", "desc") or "desc").strip().lower()
    data["sort_by"]  = ALLOWED_SORT_BY.get(raw_sort_by, "created_at")
    data["sort_dir"] = raw_sort_dir if raw_sort_dir in ALLOWED_SORT_DIR else "desc"
    return data

def _json_with_cache(payload, status=200, max_age=3600):
    resp = make_response(jsonify(payload), status)
    if max_age and status == 200:
        resp.headers["Cache-Control"] = f"public, max-age={max_age}"
    return resp

@bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)

# ===== Rutas =====

# Listado
@bp.get("")
def list_items():
    try:
        parsed = _parse_query_args(request.args)
        result = items_svc.search_items(parsed)

        # Asegura 'pages' si el service no lo aportara (lo aporta)
        if isinstance(result, dict):
            total = result.get("total", 0) or 0
            limit = result.get("limit", parsed.get("limit", 12)) or 12
            pages = result.get("pages")
            if pages is None and limit:
                pages = (total + limit - 1) // limit
                result["pages"] = pages

        if request.headers.get("X-Debug-Filters") == "1" and current_app.config.get("DEBUG_FILTERS_ENABLED", False):
            debug = {
                "_debug": True,
                "raw_query": request.query_string.decode("utf-8", errors="ignore"),
                "parsed_filters": parsed,
            }
            if isinstance(result, dict):
                result = {**result, **(debug)}
            else:
                result = {"data": result, **debug}

        return jsonify(result), 200

    except Exception:
        current_app.logger.exception("items list failed")
        page  = _safe_int(request.args.get("page", 1), 1, 1, 1_000_000)
        limit = _safe_int(request.args.get("limit", 12), 12, 1, 100)
        raw_sort_by  = (request.args.get("sort_by") or "created_at").strip().lower()
        raw_sort_dir = (request.args.get("sort_dir") or "desc").strip().lower()
        sort_by  = ALLOWED_SORT_BY.get(raw_sort_by, "created_at")
        sort_dir = raw_sort_dir if raw_sort_dir in ALLOWED_SORT_DIR else "desc"

        return jsonify({
            "items": [],
            "page": page,
            "limit": limit,
            "total": 0,
            "pages": 0,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        }), 200

# Detalle y derivados
@bp.get("/<identificador>")
def get_item(identificador):
    data = items_svc.get_item_by_id(identificador)
    if not data:
        return jsonify({"detail": "Not found"}), 404
    return jsonify(data), 200

@bp.get("/<identificador>/resumen")
def get_resumen(identificador):
    return jsonify(items_svc.get_item_resumen(identificador)), 200

@bp.get("/<identificador>/impacto")
def get_impacto(identificador):
    return jsonify(items_svc.get_item_impacto(identificador)), 200

# Reacciones
@bp.post("/<identificador>/like")
def like(identificador):
    return jsonify(items_svc.like_item(identificador)), 200

@bp.post("/<identificador>/dislike")
def dislike(identificador):
    return jsonify(items_svc.dislike_item(identificador)), 200

# Catálogos (cache)
@bp.get("/departamentos")
def departamentos():
    try:
        data = items_svc.list_departamentos()
        return _json_with_cache(data, 200, max_age=3600)
    except Exception:
        current_app.logger.exception("departamentos failed")
        return _json_with_cache([], 200, max_age=60)

@bp.get("/secciones")
def secciones():
    try:
        data = items_svc.list_secciones()
        return _json_with_cache(data, 200, max_age=3600)
    except Exception:
        current_app.logger.exception("secciones failed")
        return _json_with_cache([], 200, max_age=60)

@bp.get("/epigrafes")
def epigrafes():
    try:
        data = items_svc.list_epigrafes()
        return _json_with_cache(data, 200, max_age=3600)
    except Exception:
        current_app.logger.exception("epigrafes failed")
        return _json_with_cache([], 200, max_age=60)

# Debug (solo si está habilitado)
@bp.get("/_debug/echo")
def echo():
    if not current_app.config.get("DEBUG_FILTERS_ENABLED", False):
        return jsonify({"detail": "Debug endpoint disabled"}), 404
    parsed = _parse_query_args(request.args)
    return jsonify({
        "_debug": True,
        "raw_query": request.query_string.decode("utf-8", errors="ignore"),
        "parsed_filters": parsed,
    }), 200
