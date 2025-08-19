# app/controllers/items_controller.py
from app.services.postgres import get_db
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

# ---- Helpers ----

def _norm(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = s.strip()
    if not s or s.lower() in {"todos", "all", "null", "none"}:
        return None
    return s

def _to_date(s: Optional[str]):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None

def _rows(cur) -> List[Dict[str, Any]]:
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

# ---- Queries ----

def get_filtered_items(params: Dict[str, str]) -> Dict[str, Any]:
    """
    Soporta:
    - page, limit (paginación)
    - sort_by, sort_dir (created_at por defecto; asc/desc)
    - fecha (exacta) o fecha_desde (>=)
    - filtros (opcionales): departamento_codigo, seccion_codigo, epigrafe
    """
    page  = max(int(params.get("page", 1)), 1)
    limit = min(max(int(params.get("limit", 12)), 1), 100)

    sort_by  = params.get("sort_by", "created_at").lower()
    sort_dir = params.get("sort_dir", "desc").lower()
    if sort_by not in {"created_at", "titulo", "id"}:
        sort_by = "created_at"
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "desc"

    fecha_eq     = _to_date(_norm(params.get("fecha")))
    fecha_desde  = _to_date(_norm(params.get("fecha_desde")))
    dep_cod      = _norm(params.get("departamento_codigo"))
    sec_cod      = _norm(params.get("seccion_codigo"))
    epigrafe     = _norm(params.get("epigrafe"))

    where = []
    args: List[Any] = []

    if fecha_eq:
        where.append("DATE(created_at) = %s")
        args.append(fecha_eq)
    if fecha_desde:
        where.append("DATE(created_at) >= %s")
        args.append(fecha_desde)
    if dep_cod:
        where.append("departamento_codigo = %s")
        args.append(dep_cod)
    if sec_cod:
        where.append("seccion_codigo = %s")
        args.append(sec_cod)
    if epigrafe:
        where.append("TRIM(COALESCE(epigrafe,'')) = %s")
        args.append(epigrafe)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    offset = (page - 1) * limit

    base_select = f"""
        SELECT id, identificador, titulo, resumen, impacto,
               departamento_codigo, departamento_nombre,
               seccion_codigo, seccion_nombre,
               epigrafe, created_at, likes, dislikes
        FROM items
        {where_sql}
        ORDER BY {sort_by} {sort_dir}
        LIMIT %s OFFSET %s
    """

    count_sql = f"SELECT COUNT(*) FROM items {where_sql}"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(count_sql, args)
            total = cur.fetchone()[0]
            cur.execute(base_select, args + [limit, offset])
            data = _rows(cur)

    return {
        "items": data,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
    }

def get_item_by_id(identificador: str) -> Optional[Dict[str, Any]]:
    sql = """
      SELECT id, identificador, titulo, contenido, resumen, impacto,
             departamento_codigo, departamento_nombre,
             seccion_codigo, seccion_nombre,
             epigrafe, created_at, likes, dislikes
      FROM items
      WHERE identificador = %s
      LIMIT 1
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (identificador,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [c.name for c in cur.description]
            return dict(zip(cols, row))

def get_item_resumen(identificador: str) -> Dict[str, Any]:
    sql = "SELECT resumen FROM items WHERE identificador = %s LIMIT 1"
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (identificador,))
            row = cur.fetchone()
    return {"identificador": identificador, "resumen": row[0] if row else None}

def get_item_impacto(identificador: str) -> Dict[str, Any]:
    sql = "SELECT impacto FROM items WHERE identificador = %s LIMIT 1"
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (identificador,))
            row = cur.fetchone()
    return {"identificador": identificador, "impacto": row[0] if row else None}

def like_item(identificador: str) -> Dict[str, Any]:
    sql = "UPDATE items SET likes = COALESCE(likes,0) + 1 WHERE identificador = %s RETURNING likes"
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (identificador,))
            row = cur.fetchone()
        conn.commit()
    return {"identificador": identificador, "likes": row[0] if row else 0}

def dislike_item(identificador: str) -> Dict[str, Any]:
    sql = "UPDATE items SET dislikes = COALESCE(dislikes,0) + 1 WHERE identificador = %s RETURNING dislikes"
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (identificador,))
            row = cur.fetchone()
        conn.commit()
    return {"identificador": identificador, "dislikes": row[0] if row else 0}

def list_departamentos() -> List[Dict[str, Any]]:
    sql = """
        SELECT DISTINCT departamento_codigo AS codigo, departamento_nombre AS nombre
        FROM items
        WHERE TRIM(COALESCE(departamento_codigo,'')) <> ''
        ORDER BY nombre
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [{"codigo": r[0], "nombre": r[1]} for r in cur.fetchall()]

def list_secciones() -> List[Dict[str, Any]]:
    sql = """
        SELECT DISTINCT seccion_codigo AS codigo, seccion_nombre AS nombre
        FROM items
        WHERE TRIM(COALESCE(seccion_codigo,'')) <> ''
        ORDER BY nombre
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [{"codigo": r[0], "nombre": r[1]} for r in cur.fetchall()]

def list_epigrafes() -> List[str]:
    sql = """
        SELECT DISTINCT TRIM(epigrafe) AS epigrafe
        FROM items
        WHERE TRIM(COALESCE(epigrafe,'')) <> ''
        ORDER BY epigrafe
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [r[0] for r in cur.fetchall()]
