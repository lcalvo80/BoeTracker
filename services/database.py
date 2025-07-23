import sqlite3

DB_ITEMS = "database.db"
DB_COMMENTS = "comments.db"

def create_databases():
    with sqlite3.connect(DB_ITEMS) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clase_item TEXT,
                identificador TEXT UNIQUE,
                titulo TEXT,
                titulo_resumen TEXT,
                resumen TEXT,
                informe_impacto TEXT,
                url_pdf TEXT,
                url_html TEXT,
                url_xml TEXT,
                seccion_codigo TEXT,
                seccion_nombre TEXT,
                departamento_codigo TEXT,
                departamento_nombre TEXT,
                epigrafe TEXT,
                control TEXT,
                fecha_publicacion TEXT,
                likes INTEGER DEFAULT 0,
                dislikes INTEGER DEFAULT 0
            )
        ''')

    with sqlite3.connect(DB_COMMENTS) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_identificador TEXT,
                user_name TEXT,
                comment TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
