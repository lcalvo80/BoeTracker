# app/controllers/items_controller.py
from app.services.postgres import get_db
from datetime import datetime
import json
import base64
import gzip
import io

# ----------------- Utilidades -----------------
def _dict_rows(cursor):
    cols = [col.name for col in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]

def _decompress_field(data: str):
    try:
        if not data:
            return {}
        compressed = base64.b64decode(data)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as f:
            return json.loads(f.read().decode("utf-8"))
    except Exception:
        return "⚠️ Error al descomprimir"

def _parse_date(val):
    try:
        return datetime.strptime(val, "%Y-%m-%d").date()
    except Exception:
        return None

def _norm(val):
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() in {"todos", "all", "null", "none"}:
        return None
    return s

# ----------------- Listado con filtros -----------------
def get_filtered_items(filters, page, limit):
    """
    Filtros soportados (query params):
      - identificador (ILIKE)
      - control (ILIKE)
      - epigrafe (ILIKE)
      - seccion / seccion_codigo / seccion_nombre
      - departamento / departamento_codigo / departamento_nombre
      - fecha         (YYYY-MM-DD)  -> exacta sobre created_at::date (PRIORIDAD)
      - fecha_desde   (YYYY-MM-DD)  -> rango sobre created_at::date
      - fecha_hasta   (YYYY-MM-DD)

    Orden/paginación:
      - sort_by in {created_at, identificador, control, epigrafe, departamento, seccion}
      - sort_dir in {asc, desc}
      - page, limit (limit máx 100)
    """
    # Seleccionamos created_at y también lo exponemos como created_at_date para el FE
    base_select = """
        SELECT
            i.*,
            i.created_at::date AS created_at_date,
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

    where, params, count_params = [], [], []

    def add(clause, *vals):
        where.append(clause)
        params.extend(vals)
        count_params.extend(vals)

    # Texto parcial
    v = _norm(filters.get("identificador"))
    if v: add("i.identificador ILIKE %s", f"%{v}%")
    v = _norm(filters.get("control"))
    if v: add("i.control ILIKE %s", f"%{v}%")
    v = _norm(filters.get("epigrafe"))
    if v: add("i.epigrafe ILIKE %s", f"%{v}%")

    # Sección (código o nombre)
    seccion = _norm(filters.get("seccion"))
    if seccion:
        add("(i.seccion_codigo = %s OR s.nombre ILIKE %s)", seccion, f"%{seccion}%")
    else:
        v = _norm(filters.get("seccion_codigo"))
        if v: add("i.seccion_codigo = %s", v)
        v = _norm(filters.get("seccion_nombre"))
        if v: add("s.nombre ILIKE %s", f"%{v}%")

    # Departamento (código o nombre)
    departamento = _norm(filters.get("departamento"))
    if departamento:
        add("(i.departamento_codigo = %s OR d.nombre ILIKE %s)", departamento, f"%{departamento}%")
    else:
        v = _norm(filters.get("departamento_codigo"))
        if v: add("i.departamento_codigo = %s", v)
        v = _norm(filters.get("departamento_nombre"))
        if v: add("d.nombre ILIKE %s", f"%{v}%")

    # --------- Fecha sobre created_at::date ---------
    # Exacta tiene prioridad; si no hay exacta, evaluamos rango
    f = _parse_date(_norm(filters.get("fecha")))
    if f:
        add("i.created_at::date = %s", f)
    else:
        fd = _parse_date(_norm(filters.get("fecha_desde")))
        fh = _parse_date(_norm(filters.get("fecha_hasta")))
        if fd and fh:
            add("i.created_at::date BETWEEN %s AND %s", fd, fh)
        elif fd:
            add("i.created_at::date >= %s", fd)
        elif fh:
            add("i.created_at::date <= %s", fh)

    where_sql = (" AND " + " AND ".join(where)) if where else ""

    # Orden y paginación
    sortable = {
        "created_at": "i.created_at",
        "identificador": "i.identificador",
        "control": "i.control",
        "epigrafe": "i.epigrafe",
        "departamento": "d.nombre",
        "seccion": "s.nombre",
    }
    sort_by = sortable.get(str(filters.get("sort_by", "created_at")), "i.created_at")
    sort_dir = "ASC" if str(filters.get("sort_dir", "desc")).lower() == "asc" else "DESC"

    try: page = max(int(filters.get("page", page)), 1)
    except: page = 1
    try: limit = max(min(int(filters.get("limit", limit)), 100), 1)
    except: limit = 10
    offset = (page - 1) * limit

    query = f"""{base_select}{where_sql} ORDER BY {sort_by} {sort_dir} NULLS LAST LIMIT %s OFFSET %s"""
    count_query = f"""{base_count}{where_sql}"""

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(count_query, count_params)
        total = cur.fetchone()[0]
        cur.execute(query, [*params, limit, offset])
        items = _dict_rows(cur)

    return {"items": items, "total": total, "page": page, "limit": limit}

# ----------------- Detalle y campos comprimidos -----------------
def get_item_by_id(identificador):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                i.*,
                i.created_at::date AS created_at_date,
                d.nombre AS departamento_nombre,
                s.nombre AS seccion_nombre
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
        item["resumen"] = _decompress_field(item.get("resumen"))
        item["informe_impacto"] = _decompress_field(item.get("informe_impacto"))
        return item

def get_item_resumen(identificador):
    item = get_item_by_id(identificador)
    return item.get("resumen") or {}

def get_item_impacto(identificador):
    item = get_item_by_id(identificador)
    return item.get("informe_impacto") or {}

# ----------------- Likes / Dislikes -----------------
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

# ----------------- Listados auxiliares -----------------
def list_departamentos():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT codigo, nombre
            FROM departamentos
            WHERE codigo IS NOT NULL
              AND TRIM(COALESCE(nombre, '')) <> ''
            ORDER BY nombre
        """)
        return [{"codigo": row[0], "nombre": row[1]} for row in cur.fetchall()]

def list_secciones():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT codigo, nombre
            FROM secciones
            WHERE codigo IS NOT NULL
              AND TRIM(COALESCE(nombre, '')) <> ''
            ORDER BY nombre
        """)
        return [{"codigo": row[0], "nombre": row[1]} for row in cur.fetchall()]

def list_epigrafes():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT TRIM(epigrafe) AS epigrafe
            FROM items
            WHERE TRIM(COALESCE(epigrafe, '')) <> ''
            ORDER BY epigrafe
        """)
        return [row[0] for row in cur.fetchall()]
