# app/routes/items.py
from flask import Blueprint, jsonify, request, current_app, make_response
from datetime import datetime

from app.controllers.items_controller import (
    get_filtered_items,
    get_item_by_id,
    get_item_resumen,
    get_item_impacto,
    like_item,
    dislike_item,
    list_departamentos,
    list_secciones,
    list_epigrafes,
)

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

# claves multi-valor
MULTI_KEYS_PLURALS = {"departamentos", "secciones", "epigrafes", "tags", "ids"}

# normalización de nombres
NORMALIZE_KEYS = {
    # multi
    "departamento": "departamentos", "departamentos": "departamentos",
    "seccion": "secciones", "secciones": "secciones",
    "epigrafe": "epigrafes", "epigrafes": "epigrafes",
    "tag": "tags", "tags": "tags",
    "id": "ids", "ids": "ids",
    # FE alternativo (mapeos del front/service)
    "seccion_codigo": "secciones",
    "departamento_codigo": "departamentos",
    # texto
    "q": "q", "query": "q", "search": "q", "q_adv": "q",
    # fechas
    "fecha": "fecha",
    "fecha_desde": "fecha_desde", "desde": "fecha_desde", "from": "fecha_desde", "date_from": "fecha_desde",
    "fecha_hasta": "fecha_hasta", "hasta": "fecha_hasta", "to": "fecha_hasta", "date_to": "fecha_hasta",
    "useRange": "useRange",
    # flags
    "has_resumen": "has_resumen",
    "has_impacto": "has_impacto",
    "has_comments": "has_comments",
    "favoritos": "favoritos",
    "destacado": "destacado",
    # paginación/orden
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
    """
    Parser robusto:
    - Recorre args en orden con items(multi=True): k=a&k=b preserva el orden.
    - Soporta k[]=a&k[]=b, k=a&k=b y k=a,b
    - Para claves multi, agrega y deduplica preservando orden.
    - Normaliza fechas, booleanos, paginación y ordenación.
    - Acepta 'fecha' (exacta) y 'useRange' para convertir a [fecha_desde, fecha_hasta].
    """
    data = {}

    # 1) recolecta y agrega
    for raw_key, raw_val in args.items(multi=True):
        key = raw_key[:-2] if raw_key.endswith("[]") else raw_key
        norm = NORMALIZE_KEYS.get(key, key)

        if norm in MULTI_KEYS_PLURALS:
            # split por comas para soportar k=a,b
            parts = [p.strip() for p in str(raw_val).split(",") if p.strip() != ""]
            prev = data.get(norm, [])
            data[norm] = prev + parts
        else:
            data[norm] = raw_val

    # 2) dedupe para multi
    for m in list(MULTI_KEYS_PLURALS):
        if m in data:
            data[m] = _dedupe_preserve_order(data[m])

    # 3) q string
    if "q" in data and data["q"] is not None:
        data["q"] = (str(data["q"]).strip() or None)

    # 4) flags bool
    #    incluye useRange (cómo interpretar 'fecha' exacta)
    for flag in ("has_resumen", "has_impacto", "has_comments", "favoritos", "destacado", "useRange"):
        if flag in data:
            data[flag] = _safe_bool(data[flag])

    # 5) fechas (ISO)
    #    normaliza fecha exacta y rango (fecha_desde/hasta)
    fecha_exacta = _safe_date(data.get("fecha"))
    fecha_desde = _safe_date(data.get("fecha_desde"))
    fecha_hasta = _safe_date(data.get("fecha_hasta"))

    use_range = data.get("useRange", None)
    if use_range is False and fecha_exacta:
        # modo fecha exacta: fuerza desde=hasta=fecha y limpia claves
        data["fecha_desde"] = fecha_exacta
        data["fecha_hasta"] = fecha_exacta
        data.pop("fecha", None)
    else:
        # si ya vinieron desde/hasta, usamos las normalizadas
        if fecha_desde:
            data["fecha_desde"] = fecha_desde
        else:
            data.pop("fecha_desde", None)
        if fecha_hasta:
            data["fecha_hasta"] = fecha_hasta
        else:
            data.pop("fecha_hasta", None)
        # si llega 'fecha' pero useRange True/None, ignórala (se usará el rango)
        data.pop("fecha", None)

    # 6) paginación
    data["page"]  = _safe_int(data.get("page", 1), 1, 1, 1_000_000)
    data["limit"] = _safe_int(data.get("limit", 12), 12, 1, 100)

    # 7) ordenación
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

# ===== Rutas =====

# Listado
@bp.route("", methods=["GET"])
def api_items():
    try:
        parsed = _parse_query_args(request.args)

        # Llamamos SIEMPRE al controller (para que los tests puedan monkeypatchear)
        result = get_filtered_items(parsed)

        # Asegura 'pages' si el controller no lo aporta
        if isinstance(result, dict):
            total = result.get("total", 0) or 0
            limit = result.get("limit", parsed.get("limit", 12)) or 12
            pages = result.get("pages")
            if pages is None and limit:
                pages = (total + limit - 1) // limit
                result["pages"] = pages

        # Debug opcional (con cabecera y flag en config)
        if request.headers.get("X-Debug-Filters") == "1" and current_app.config.get("DEBUG_FILTERS_ENABLED", False):
            debug = {
                "_debug": True,
                "raw_query": request.query_string.decode("utf-8", errors="ignore"),
                "parsed_filters": parsed,
            }
            if isinstance(result, dict):
                result = {**result, **debug}
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


# Detalle
@bp.route("/<identificador>", methods=["GET"])
def api_item_by_id(identificador):
    data = get_item_by_id(identificador)
    if not data:
        return jsonify({"detail": "Not found"}), 404
    return jsonify(data), 200

@bp.route("/<identificador>/resumen", methods=["GET"])
def api_resumen(identificador):
    return jsonify(get_item_resumen(identificador)), 200

@bp.route("/<identificador>/impacto", methods=["GET"])
def api_impacto(identificador):
    return jsonify(get_item_impacto(identificador)), 200

# Reacciones
@bp.route("/<identificador>/like", methods=["POST"])
def api_like(identificador):
    return jsonify(like_item(identificador)), 200

@bp.route("/<identificador>/dislike", methods=["POST"])
def api_dislike(identificador):
    return jsonify(dislike_item(identificador)), 200

# Catálogos (cache)
@bp.route("/departamentos", methods=["GET"])
def api_departamentos():
    try:
        data = list_departamentos()
        return _json_with_cache(data, 200, max_age=3600)
    except Exception:
        current_app.logger.exception("departamentos failed")
        return _json_with_cache([], 200, max_age=60)

@bp.route("/secciones", methods=["GET"])
def api_secciones():
    try:
        data = list_secciones()
        return _json_with_cache(data, 200, max_age=3600)
    except Exception:
        current_app.logger.exception("secciones failed")
        return _json_with_cache([], 200, max_age=60)

@bp.route("/epigrafes", methods=["GET"])
def api_epigrafes():
    try:
        data = list_epigrafes()
        return _json_with_cache(data, 200, max_age=3600)
    except Exception:
        current_app.logger.exception("epigrafes failed")
        return _json_with_cache([], 200, max_age=60)

# ===== Endpoint de eco/diagnóstico explícito (solo DEV) =====
@bp.route("/_debug/echo", methods=["GET"])
def api_items_echo():
    """
    /api/items/_debug/echo?departamentos=a&departamentos=b&secciones=I,II...
    Devuelve cómo interpreta el backend los filtros.
    Requiere DEBUG_FILTERS_ENABLED=True.
    """
    if not current_app.config.get("DEBUG_FILTERS_ENABLED", False):
        return jsonify({"detail": "Debug endpoint disabled"}), 404

    parsed = _parse_query_args(request.args)
    return jsonify({
        "_debug": True,
        "raw_query": request.query_string.decode("utf-8", errors="ignore"),
        "parsed_filters": parsed,
    }), 200
