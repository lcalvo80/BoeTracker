# app/controllers/items_controller.py
from app.services.postgres import get_db
from datetime import datetime
import json
import base64
import gzip
import io

def dict_rows(cursor):
    cols = [col.name for col in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]

def decompress_field(data: str):
    try:
        if not data:
            return {}
        compressed = base64.b64decode(data)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as f:
            return json.loads(f.read().decode('utf-8'))
    except Exception:
        return "⚠️ Error al descomprimir"

def _parse_date(val):
    try:
        return datetime.strptime(val, "%Y-%m-%d").date()
    except Exception:
        return None

def get_filtered_items(filters, page, limit):
    """
    Filtros soportados (query params):
      - identificador: str (ILIKE)
      - control: str (ILIKE)
      - epigrafe: str (ILIKE)
      - seccion_codigo: str (exact)
      - seccion_nombre: str (ILIKE)
      - seccion: str (genérico: intenta código exacto O nombre ILIKE)
      - departamento_codigo: str (exact)
      - departamento_nombre: str (ILIKE)
      - departamento: str (genérico: intenta código exacto O nombre ILIKE)
      - fecha: YYYY-MM-DD (exacta; PRIORIDAD sobre rango)
      - fecha_desde: YYYY-MM-DD
      - fecha_hasta: YYYY-MM-DD

    Paginación/orden:
      - page (default 1), limit (default 10, máx 100)
      - sort_by in {fecha_publicacion, identificador, control, epigrafe, departamento, seccion}
      - sort_dir in {asc, desc} (default desc)
    """

    # SELECT con JOINs para enlazar códigos a nombres (tablas auxiliares)
    base_select = """
        SELECT
            i.*,
            d.nombre AS departamento_nombre,
            s.nombre AS seccion_nombre
        FROM items i
        LEFT JOIN departamentos d ON d.codigo = i.departamento_codigo
        LEFT JOIN secciones s     ON s.codigo = i.seccion_codigo
        WHERE 1=1
    """
    base_count = """
        SELECT COUNT(*)
        FROM items i
        LEFT JOIN departamentos d ON d.codigo = i.departamento_codigo
        LEFT JOIN secciones s     ON s.codigo = i.seccion_codigo
        WHERE 1=1
    """

    where = []
    params = []
    count_params = []

    def add_clause(clause: str, *vals):
        if clause:
            where.append(clause)
            params.extend(vals)
            count_params.extend(vals)

    # Búsquedas parciales
    identificador = filters.get("identificador")
    if identificador:
        add_clause("i.identificador ILIKE %s", f"%{identificador}%")

    control = filters.get("control")
    if control:
        add_clause("i.control ILIKE %s", f"%{control}%")

    epigrafe = filters.get("epigrafe")
    if epigrafe:
        add_clause("i.epigrafe ILIKE %s", f"%{epigrafe}%")

    # Sección: por código/nombre o parámetro genérico
    seccion = filters.get("seccion")
    seccion_codigo = filters.get("seccion_codigo")
    seccion_nombre = filters.get("seccion_nombre")

    if seccion:
        # código exacto O nombre parcial
        add_clause("(i.seccion_codigo = %s OR s.nombre ILIKE %s)", seccion, f"%{seccion}%")
    else:
        if seccion_codigo:
            add_clause("i.seccion_codigo = %s", seccion_codigo)
        if seccion_nombre:
            add_clause("s.nombre ILIKE %s", f"%{seccion_nombre}%")

    # Departamento: por código/nombre o parámetro genérico
    departamento = filters.get("departamento")
    departamento_codigo = filters.get("departamento_codigo")
    departamento_nombre = filters.get("departamento_nombre")

    if departamento:
        add_clause("(i.departamento_codigo = %s OR d.nombre ILIKE %s)", departamento, f"%{departamento}%")
    else:
        if departamento_codigo:
            add_clause("i.departamento_codigo = %s", departamento_codigo)
        if departamento_nombre:
            add_clause("d.nombre ILIKE %s", f"%{departamento_nombre}%")

    # Fecha exacta o rango
    fecha = filters.get("fecha")
    if fecha:
        f = _parse_date(fecha)
        if f:
            add_clause("i.fecha_publicacion = %s", f)
    else:
        fd = _parse_date(filters.get("fecha_desde")) if filters.get("fecha_desde") else None
        fh = _parse_date(filters.get("fecha_hasta")) if filters.get("fecha_hasta") else None
        if fd and fh:
            add_clause("i.fecha_publicacion BETWEEN %s AND %s", fd, fh)
        elif fd:
            add_clause("i.fecha_publicacion >= %s", fd)
        elif fh:
            add_clause("i.fecha_publicacion <= %s", fh)

    # Construcción final de WHERE
    where_sql = (" AND " + " AND ".join(where)) if where else ""

    # Orden y paginación
    sortable = {
        "fecha_publicacion": "i.fecha_publicacion",
        "identificador": "i.identificador",
        "control": "i.control",
        "epigrafe": "i.epigrafe",
        "departamento": "d.nombre",
        "seccion": "s.nombre",
    }
    sort_by = filters.get("sort_by", "fecha_publicacion")
    sort_by_sql = sortable.get(sort_by, "i.fecha_publicacion")
    sort_dir = "ASC" if str(filters.get("sort_dir", "desc")).lower() == "asc" else "DESC"

    try:
        page = max(int(filters.get("page", page)), 1)
    except Exception:
        page = 1
    try:
        limit = max(min(int(filters.get("limit", limit)), 100), 1)
    except Exception:
        limit = 10
    offset = (page - 1) * limit

    query = f"""{base_select} {where_sql} ORDER BY {sort_by_sql} {sort_dir} NULLS LAST LIMIT %s OFFSET %s"""
    count_query = f"""{base_count} {where_sql}"""

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(count_query, count_params)
        total = cur.fetchone()[0]

        cur.execute(query, [*params, limit, offset])
        items = dict_rows(cur)

    return {"items": items, "total": total, "page": page, "limit": limit}

def get_item_by_id(identificador):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT i.*, d.nombre AS departamento_nombre, s.nombre AS seccion_nombre
            FROM items i
            LEFT JOIN departamentos d ON d.codigo = i.departamento_codigo
            LEFT JOIN secciones s     ON s.codigo = i.seccion_codigo
            WHERE i.identificador = %s
        """, (identificador,))
        row = cur.fetchone()
        if not row:
            return {}
        cols = [desc.name for desc in cur.description]
        item = dict(zip(cols, row))

        item["resumen"] = decompress_field(item.get("resumen"))
        item["informe_impacto"] = decompress_field(item.get("informe_impacto"))
        return item

def get_item_resumen(identificador):
    item = get_item_by_id(identificador)
    return item.get("resumen") or {}

def get_item_impacto(identificador):
    item = get_item_by_id(identificador)
    return item.get("informe_impacto") or {}

def like_item(identificador):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE items SET likes = COALESCE(likes, 0) + 1 WHERE identificador = %s RETURNING likes",
            (identificador,),
        )
        result = cur.fetchone()
        conn.commit()
        return {"likes": result[0]} if result else {}

def dislike_item(identificador):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE items SET dislikes = COALESCE(dislikes, 0) + 1 WHERE identificador = %s RETURNING dislikes",
            (identificador,),
        )
        result = cur.fetchone()
        conn.commit()
        return {"dislikes": result[0]} if result else {}

def list_departamentos():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT codigo, nombre
            FROM departamentos
            WHERE nombre IS NOT NULL AND nombre != ''
            ORDER BY nombre
        """)
        return [{"codigo": row[0], "nombre": row[1]} for row in cur.fetchall()]

def list_epigrafes():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT epigrafe
            FROM items
            WHERE epigrafe IS NOT NULL AND epigrafe != ''
            ORDER BY epigrafe
        """)
        return [row[0] for row in cur.fetchall()]

def list_secciones():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT codigo, nombre
            FROM secciones
            WHERE nombre IS NOT NULL AND nombre != ''
            ORDER BY nombre
        """)
        return [{"codigo": row[0], "nombre": row[1]} for row in cur.fetchall()]
