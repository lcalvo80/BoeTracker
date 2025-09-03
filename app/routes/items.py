# app/routes/items.py
from flask import Blueprint, jsonify, request, current_app, make_response

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

# -------- helpers ----------

def _safe_int(v, d, mi=1, ma=100):
    """Convierte a int dentro de [mi, ma]; si falla retorna d."""
    try:
        n = int(v)
        if n < mi:
            n = mi
        if n > ma:
            n = ma
        return n
    except Exception:
        return d


# Claves que deben interpretarse SIEMPRE como listas
MULTI_KEYS_PLURALS = {"departamentos", "secciones", "epigrafes", "tags"}
# Mapa para normalizar claves equivalentes (aceptamos singular, plural y [] )
NORMALIZE_KEYS = {
    "departamento": "departamentos",
    "departamentos": "departamentos",
    "seccion": "secciones",
    "secciones": "secciones",
    "epigrafe": "epigrafes",
    "epigrafes": "epigrafes",
    "tag": "tags",
    "tags": "tags",
}

# Campos de ordenación permitidos (ajusta según tu dominio/controlador)
ALLOWED_SORT_BY = {
    # nombre_entrada -> nombre_normalizado_que_espera_el_controller
    "created_at": "created_at",
    "fecha": "fecha",
    "relevancia": "relevancia",
    "relevance": "relevancia",
    "updated_at": "updated_at",
}
ALLOWED_SORT_DIR = {"asc", "desc"}


def _dedupe_preserve_order(items):
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _parse_query_args(args):
    """
    Convierte query params en un dict listo para el controller:
    - Soporta k[]=a&k[]=b, k=a&k=b y k=a,b
    - Para claves multi, devuelve SIEMPRE lista (aunque sea de 1)
    - Normaliza nombres a plural coherente con el controller
    - Sanea paginación y ordenación
    """
    data = {}

    # 1) parseo base
    for raw_key in args.keys():
        values = args.getlist(raw_key)  # conserva repetidos
        key = raw_key[:-2] if raw_key.endswith("[]") else raw_key
        norm = NORMALIZE_KEYS.get(key, key)

        # Unificamos tratamiento multi
        if norm in MULTI_KEYS_PLURALS:
            # Unimos repetidos y permitimos coma-separado
            joined = ",".join(values)
            parts = [x.strip() for x in joined.split(",")]
            parts = [x for x in parts if x != ""]
            data[norm] = _dedupe_preserve_order(parts)
        else:
            # último valor para claves escalares
            data[norm] = values[-1] if values else None

    # 2) saneo de page/limit
    data["page"] = _safe_int(args.get("page", data.get("page", 1)), 1, 1, 1_000_000)
    data["limit"] = _safe_int(args.get("limit", data.get("limit", 12)), 12, 1, 100)

    # 3) saneo de ordenación
    raw_sort_by = (args.get("sort_by") or data.get("sort_by") or "created_at").strip().lower()
    data["sort_by"] = ALLOWED_SORT_BY.get(raw_sort_by, "created_at")

    raw_sort_dir = (args.get("sort_dir") or data.get("sort_dir") or "desc").strip().lower()
    data["sort_dir"] = raw_sort_dir if raw_sort_dir in ALLOWED_SORT_DIR else "desc"

    return data


def _json_with_cache(payload, status=200, max_age=3600):
    """
    Respuesta JSON con Cache-Control opcional (útil para catálogos).
    """
    resp = make_response(jsonify(payload), status)
    if max_age and status == 200:
        resp.headers["Cache-Control"] = f"public, max-age={max_age}"
    return resp


# -------- Rutas ----------

# Listado
@bp.route("", methods=["GET"])
def api_items():
    try:
        parsed = _parse_query_args(request.args)
        result = get_filtered_items(parsed)
        # Se asume que el controller ya devuelve page/limit/total/pages/items coherentes
        return jsonify(result), 200
    except Exception:
        current_app.logger.exception("items list failed")
        # Respuesta consistente para no romper la UI
        page = _safe_int(request.args.get("page", 1), 1, 1, 1_000_000)
        limit = _safe_int(request.args.get("limit", 12), 12, 1, 100)
        sort_by = (request.args.get("sort_by") or "created_at").strip().lower()
        sort_dir = (request.args.get("sort_dir") or "desc").strip().lower()
        sort_by = ALLOWED_SORT_BY.get(sort_by, "created_at")
        sort_dir = sort_dir if sort_dir in ALLOWED_SORT_DIR else "desc"

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


# Catálogos (con cache-control para mejorar rendimiento de la UI)
@bp.route("/departamentos", methods=["GET"])
def api_departamentos():
    try:
        data = list_departamentos()
        return _json_with_cache(data, 200, max_age=3600)
    except Exception:
        current_app.logger.exception("departamentos failed")
        return _json_with_cache([], 200, max_age=60)  # fallback con cache corta


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
