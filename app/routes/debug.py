from flask import Blueprint, jsonify, current_app, request

bp = Blueprint("debug", __name__)

@bp.get("/routes")
def list_routes():
    rules = []
    for r in current_app.url_map.iter_rules():
        rules.append({
            "endpoint": r.endpoint,
            "methods": sorted(m for m in r.methods if m in {"GET","POST","PUT","PATCH","DELETE","OPTIONS"}),
            "rule": str(r.rule),
        })
    return jsonify(sorted(rules, key=lambda x: x["rule"]))

@bp.route("/echo", methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"])
def echo():
    return jsonify({
        "method": request.method,
        "path": request.path,
        "headers": {k: v for k, v in request.headers.items()},
        "json": request.get_json(silent=True),
    })
