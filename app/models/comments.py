import sqlite3
from services.database import DB_COMMENTS

def get_comments_by_item(identificador):
    with sqlite3.connect(DB_COMMENTS) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM comments WHERE item_identificador = ? ORDER BY created_at DESC", (identificador,))
        rows = cursor.fetchall()
        return [dict(zip([col[0] for col in cursor.description], row)) for row in rows]

def add_comment(item_identificador, user_name, comment_text):
    with sqlite3.connect(DB_COMMENTS) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO comments (item_identificador, user_name, comment) VALUES (?, ?, ?)",
            (item_identificador, user_name, comment_text)
        )
        conn.commit()
