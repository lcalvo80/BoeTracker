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
    """
    Normaliza un parámetro de filtro:
    - quita espacios
    - descarta '', 'todos', 'all', 'null', 'none' (case-insensitive)
    """
    if val is None:
        return None
    s = str(val).strip()
    if s == "":
        return None
    if s.lower() in {"todos", "all", "null", "none"}:
        return None
    return s

# ----------------- Listado con filtros -----------------
def get_filtered_items(filters, page, limit):
    """
    Filtros soportados (query params):
      - identificador: str (ILIKE %texto%)
      - control: str (ILIKE %texto%)
      - epigrafe: str (ILIKE %texto%)
      - seccion / seccion_codigo / seccion_nombre
      - departamento / departamento_codigo / departamento_nombre
      - fecha (YYYY-MM-DD)  -> prioridad sobre rango
      - fecha_desde, fecha_hasta (YYYY-MM-DD)

    Orden/paginación:
      - sort_by in {fecha_publicacion, identificador, control, epigrafe, departamento, seccion}
      - sort_dir in {asc, desc}
      - page, limit (limit máx 100)
    """
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

    def add(clause: str, *vals):
        where.append(clause)
        params.extend(vals)
        count_params.extend(vals)

    # --- Texto parcial ---
    identificador = _norm(filters.get("identificador"))
    if identificador:
        add("i.identificador ILIKE %s", f"%{identificador}%")

    control = _norm(filters.get("control"))
    if control:
        add("i.control ILIKE %s", f"%{control}%")

    epigrafe = _norm(filters.get("epigrafe"))
    if epigrafe:
        add("i.epigrafe ILIKE %s", f"%{epigrafe}%")

    # --- Sección (código/nombre) ---
    # Aceptamos: seccion (genérico), seccion_codigo, seccion_nombre
    seccion = _norm(filters.get("seccion"))
    seccion_codigo = _norm(filters.get("seccion_codigo"))
    seccion_nombre = _norm(filters.get("seccion_nombre"))

    if seccion:  # código o nombre
        # ILIKE sin % para igualdad case-insensitive de código + nombre parcial
        add("(i.seccion_codigo ILIKE %s OR s.nombre ILIKE %s)", seccion, f"%{seccion}%")
    else:
        if seccion_codigo:
            add("i.seccion_codigo ILIKE %s", seccion_codigo)  # igualdad case-insensitive
        if seccion_nombre:
            add("s.nombre ILIKE %s", f"%{seccion_nombre}%")

    # --- Departamento (código/nombre) ---
    # Aceptamos: departamento (genérico), departamento_codigo, departamento_nombre
    departamento = _norm(filters.get("departamento"))
    departamento_codigo = _norm(filters.get("departamento_codigo"))
    departamento_nombre = _norm(filters.get("departamento_nombre"))

    if departamento:
        add("(i.departamento_codigo ILIKE %s OR d.nombre ILIKE %s)", departamento, f"%{departamento}%")
    else:
        if departamento_codigo:
            add("i.departamento_codigo ILIKE %s", departamento_codigo)
        if departamento_nombre:
            add("d.nombre ILIKE %s", f"%{departamento_nombre}%")

    # --- Fecha exacta o rango ---
    fecha = _norm(filters.get("fecha"))
    if fecha:
        f = _parse_date(fecha)
        if f:
            add("i.fecha_publicacion = %s", f)
    else:
        fd = _parse_date(_norm(filters.get("fecha_desde")))
        fh = _parse_date(_norm(filters.get("fecha_hasta")))
        if fd and fh:
            add("i.fecha_publicacion BETWEEN %s AND %s", fd, fh)
        elif fd:
            add("i.fecha_publicacion >= %s", fd)
        elif fh:
            add("i.fecha_publicacion <= %s", fh)

    # WHERE final
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
    sort_by = str(filters.get("sort_by", "fecha_publicacion"))
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

    query = f"""{base_select}{where_sql} ORDER BY {sort_by_sql} {sort_dir} NULLS LAST LIMIT %s OFFSET %s"""
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
    """
    Devuelve epígrafes distintos, ya recortados (sin blancos) y no vacíos.
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT TRIM(epigrafe) AS epigrafe
            FROM items
            WHERE TRIM(COALESCE(epigrafe, '')) <> ''
            ORDER BY epigrafe
        """)
        return [row[0] for row in cur.fetchall()]
