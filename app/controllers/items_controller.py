from app.services.postgres import get_db
from app.utils.compression import decompress_json

def dict_rows(cursor):
    cols = [col.name for col in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]

def get_filtered_items(filters, page, limit):
    query = """
        SELECT i.*, d.nombre AS departamento_nombre, s.nombre AS seccion_nombre
        FROM items i
        LEFT JOIN departamentos d ON i.departamento_codigo = d.codigo
        LEFT JOIN secciones s ON i.seccion_codigo = s.codigo
        WHERE 1=1
    """
    count_query = "SELECT COUNT(*) FROM items i WHERE 1=1"
    query_params, count_params = [], []

    def append(condition, value, exact=True):
        if value:
            cond = f"{condition} = %s" if exact else f"{condition} ILIKE %s"
            val = value if exact else f"%{value}%"
            query_params.append(val)
            count_params.append(val)
            nonlocal query, count_query
            query += f" AND {condition} = %s"
            count_query += f" AND {condition} = %s"

    append("i.identificador", filters.get("identificador"), exact=False)
    append("i.control", filters.get("control"), exact=False)
    append("i.departamento_codigo", filters.get("departamento_codigo"))
    append("i.epigrafe", filters.get("epigrafe"))
    append("i.seccion_codigo", filters.get("seccion_codigo"))
    append("i.fecha_publicacion", filters.get("fecha"))

    offset = (page - 1) * limit
    query += " ORDER BY i.fecha_publicacion DESC LIMIT %s OFFSET %s"
    query_params += [limit, offset]

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(count_query, count_params)
        total = cur.fetchone()[0]
        cur.execute(query, query_params)
        items = dict_rows(cur)

    return {"items": items, "total": total}

def get_item_by_id(identificador):
    query = """
        SELECT i.*, d.nombre AS departamento_nombre, s.nombre AS seccion_nombre
        FROM items i
        LEFT JOIN departamentos d ON i.departamento_codigo = d.codigo
        LEFT JOIN secciones s ON i.seccion_codigo = s.codigo
        WHERE i.identificador = %s
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(query, (identificador,))
        row = cur.fetchone()
        if not row:
            return {}
        cols = [desc.name for desc in cur.description]
        item = dict(zip(cols, row))

        try:
            item["resumen"] = decompress_json(item["resumen"])
        except Exception:
            item["resumen"] = "⚠️ Error al descomprimir resumen"

        try:
            item["informe_impacto"] = decompress_json(item["informe_impacto"])
        except Exception:
            item["informe_impacto"] = "⚠️ Error al descomprimir informe"

        return item

def get_item_resumen(identificador):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT resumen FROM items WHERE identificador = %s", (identificador,))
        row = cur.fetchone()
        if not row:
            return {"error": "Not found"}
        try:
            return {"resumen": decompress_json(row[0])}
        except Exception:
            return {"error": "Error al descomprimir resumen"}

def get_item_impacto(identificador):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT informe_impacto FROM items WHERE identificador = %s", (identificador,))
        row = cur.fetchone()
        if not row:
            return {"error": "Not found"}
        try:
            return {"informe_impacto": decompress_json(row[0])}
        except Exception:
            return {"error": "Error al descomprimir informe_impacto"}

def like_item(identificador):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE items SET likes = likes + 1 WHERE identificador = %s RETURNING likes", (identificador,))
        result = cur.fetchone()
        conn.commit()
        return {"likes": result[0]} if result else {}

def dislike_item(identificador):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE items SET dislikes = dislikes + 1 WHERE identificador = %s RETURNING dislikes", (identificador,))
        result = cur.fetchone()
        conn.commit()
        return {"dislikes": result[0]} if result else {}

def list_departamentos():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT codigo, nombre FROM departamentos ORDER BY nombre")
        return [{"codigo": c, "nombre": n} for c, n in cur.fetchall()]

def list_secciones():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT codigo, nombre FROM secciones ORDER BY nombre")
        return [{"codigo": c, "nombre": n} for c, n in cur.fetchall()]

def list_epigrafes():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT epigrafe FROM items WHERE epigrafe IS NOT NULL AND epigrafe != '' ORDER BY epigrafe")
        return [row[0] for row in cur.fetchall()]
