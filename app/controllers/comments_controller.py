from app.services.postgres import get_db

def get_comments_by_item(item_identificador):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, item_identificador, user_name, comment, created_at FROM comments WHERE item_identificador = %s ORDER BY created_at DESC", (item_identificador,))
        rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "item_identificador": row[1],
                "user_name": row[2],
                "comment": row[3],
                "created_at": row[4].isoformat()
            }
            for row in rows
        ]

def add_comment(item_identificador, user_name, comment):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO comments (item_identificador, user_name, comment) VALUES (%s, %s, %s) RETURNING id, created_at", (item_identificador, user_name, comment))
        result = cursor.fetchone()
        conn.commit()
        return {
            "id": result[0],
            "item_identificador": item_identificador,
            "user_name": user_name,
            "comment": comment,
            "created_at": result[1].isoformat()
        }
