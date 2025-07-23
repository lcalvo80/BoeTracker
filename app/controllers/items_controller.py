from services.postgres import get_db
import json

def dict_rows(cursor):
    cols = [col.name for col in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]

def get_filtered_items(filters, page, limit):
    query = "SELECT * FROM items WHERE 1=1"
    count_query = "SELECT COUNT(*) FROM items WHERE 1=1"
    query_params, count_params = [], []

    def append(condition, value, exact=True):
        if value:
            cond = f"{condition} = %s" if exact else f"{condition} ILIKE %s"
            query_params.append(value if exact else f"%{value}%")
            count_params.append(value if exact else f"%{value}%")
            nonlocal query, count_query
            query += f" AND {cond}"
            count_query += f" AND {cond}"

    append("identificador", filters.get("identificador"), exact=False)
    append("control", filters.get("control"), exact=False)
    append("departamento_nombre", filters.get("departamento_nombre"))
    append("epigrafe", filters.get("epigrafe"))
    append("seccion_nombre", filters.get("seccion_nombre"))
    append("fecha_publicacion", filters.get("fecha"))

    offset = (page - 1) * limit
    query += " ORDER BY fecha_publicacion DESC LIMIT %s OFFSET %s"
    query_params += [limit, offset]

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(count_query, count_params)
        total = cur.fetchone()[0]
        cur.execute(query, query_params)
        items = dict_rows(cur)

    return {"items": items, "total": total}

def get_item_by_id(identificador):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM items WHERE identificador = %s", (identificador,))
        row = cur.fetchone()
        if not row:
            return {}
        cols = [desc.name for desc in cur.description]
        return dict(zip(cols, row))

def get_item_resumen(identificador):
    item = get_item_by_id(identificador)
    return item.get("resumen") or {}

def get_item_impacto(identificador):
    item = get_item_by_id(identificador)
    return item.get("informe_impacto") or {}

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
        cur.execute("SELECT DISTINCT departamento_nombre FROM items WHERE departamento_nombre IS NOT NULL AND departamento_nombre != '' ORDER BY departamento_nombre")
        return [row[0] for row in cur.fetchall()]

def list_epigrafes():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT epigrafe FROM items WHERE epigrafe IS NOT NULL AND epigrafe != '' ORDER BY epigrafe")
        return [row[0] for row in cur.fetchall()]

def list_secciones():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT seccion_nombre FROM items WHERE seccion_nombre IS NOT NULL AND seccion_nombre != '' ORDER BY seccion_nombre")
        return [row[0] for row in cur.fetchall()]
