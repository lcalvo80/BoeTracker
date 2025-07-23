import sqlite3
import json
from services.database import DB_ITEMS

def dict_rows(cursor, rows):
    return [dict(zip([col[0] for col in cursor.description], row)) for row in rows]

def get_filtered_items(filters, page, limit):
    query = "SELECT * FROM items WHERE 1=1"
    count_query = "SELECT COUNT(*) FROM items WHERE 1=1"
    query_params = []
    count_params = []

    def append_filter(condition, value, exact_match=True):
        nonlocal query, count_query
        if value:
            if exact_match:
                query += f" AND {condition} = ?"
                count_query += f" AND {condition} = ?"
                query_params.append(value)
                count_params.append(value)
            else:
                query += f" AND {condition} LIKE ?"
                count_query += f" AND {condition} LIKE ?"
                query_params.append(f"%{value}%")
                count_params.append(f"%{value}%")

    append_filter("identificador", filters.get("identificador"), exact_match=False)
    append_filter("control", filters.get("control"), exact_match=False)
    append_filter("departamento_nombre", filters.get("departamento_nombre"))
    append_filter("epigrafe", filters.get("epigrafe"))
    append_filter("seccion_nombre", filters.get("seccion_nombre"))
    append_filter("fecha_publicacion", filters.get("fecha"))

    query += " ORDER BY fecha_publicacion DESC LIMIT ? OFFSET ?"
    query_params.extend([limit, (page - 1) * limit])

    with sqlite3.connect(DB_ITEMS) as conn:
        cursor = conn.cursor()
        cursor.execute(count_query, count_params)
        total = cursor.fetchone()[0]

        cursor.execute(query, query_params)
        items = dict_rows(cursor, cursor.fetchall())

    return {"items": items, "total": total}

def get_item_by_id(identificador):
    with sqlite3.connect(DB_ITEMS) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM items WHERE identificador = ?", (identificador,))
        row = cursor.fetchone()
        return dict(zip([col[0] for col in cursor.description], row)) if row else {}

def get_item_resumen(identificador):
    item = get_item_by_id(identificador)
    return json.loads(item.get("resumen", "{}")) if item else {}

def get_item_impacto(identificador):
    item = get_item_by_id(identificador)
    return json.loads(item.get("informe_impacto", "{}")) if item else {}

def like_item(identificador):
    with sqlite3.connect(DB_ITEMS) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE items SET likes = likes + 1 WHERE identificador = ?", (identificador,))
        conn.commit()
        cursor.execute("SELECT likes FROM items WHERE identificador = ?", (identificador,))
        row = cursor.fetchone()
        return {"likes": row[0]} if row else {}

def dislike_item(identificador):
    with sqlite3.connect(DB_ITEMS) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE items SET dislikes = dislikes + 1 WHERE identificador = ?", (identificador,))
        conn.commit()
        cursor.execute("SELECT dislikes FROM items WHERE identificador = ?", (identificador,))
        row = cursor.fetchone()
        return {"dislikes": row[0]} if row else {}

def list_clase_items():
    with sqlite3.connect(DB_ITEMS) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT clase_item FROM items WHERE clase_item IS NOT NULL AND clase_item != ''")
        return [row[0] for row in cursor.fetchall()]

def list_departamentos():
    with sqlite3.connect(DB_ITEMS) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT departamento_nombre 
            FROM items 
            WHERE departamento_nombre IS NOT NULL AND departamento_nombre != ''
            ORDER BY departamento_nombre
        """)
        return [row[0] for row in cursor.fetchall() if row[0]]

def list_epigrafes():
    with sqlite3.connect(DB_ITEMS) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT epigrafe 
            FROM items 
            WHERE epigrafe IS NOT NULL AND epigrafe != ''
            ORDER BY epigrafe
        """)
        return [row[0] for row in cursor.fetchall() if row[0]]

def list_secciones():
    with sqlite3.connect(DB_ITEMS) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT seccion_nombre 
            FROM items 
            WHERE seccion_nombre IS NOT NULL AND seccion_nombre != ''
            ORDER BY seccion_nombre
        """)
        return [row[0] for row in cursor.fetchall() if row[0]]
