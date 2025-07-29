@bp.route("/items", methods=["GET"])
def list_items():
    filters = {
        "identificador": request.args.get("identificador"),
        "control": request.args.get("control"),
        "departamento_codigo": request.args.get("departamento_codigo"),
        "epigrafe": request.args.get("epigrafe"),
        "seccion_codigo": request.args.get("seccion_codigo"),
        "fecha": request.args.get("fecha"),
    }
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 12))
    return jsonify(get_filtered_items(filters, page, limit))


@bp.route("/departamentos", methods=["GET"])
def get_departamentos():
    return jsonify(list_departamentos())

@bp.route("/secciones", methods=["GET"])
def get_secciones():
    return jsonify(list_secciones())
