# app/controllers/items_controller.py
from app.services.postgres import get_db
from datetime import datetime
import json
import base64
import gzip
import io
import re

# =========================
# Utilidades generales
# =========================

def _dict_rows(cursor):
    cols = [col.name for col in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]

def _decompress_field(data: str):
    """Campos JSON comprimidos en base64+gzip (resumen / informe_impacto)."""
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

def _csv_list(filters, key):
    """
    Lee listas desde querystring: soporta valores repetidos (?k=a&k=b) y CSV (?k=a,b).
    Devuelve lista limpia y deduplicada.
    """
    out = []
    try:
        if hasattr(filters, "getlist"):
            for v in filters.getlist(key):
                if v is not None:
                    out.extend(str(v).split(","))
    except Exception:
        pass
    # También admite .get único con CSV
    v = filters.get(key) if hasattr(filters, "get") else None
    if v:
        out.extend(str(v).split(","))
    # limpiar/deduplicar
    seen, clean = set(), []
    for x in out:
        s = x.strip()
        if s and s not in seen:
            seen.add(s)
            clean.append(s)
    return clean

def _in_clause(column: str, values: list):
    """column IN (%s,...%s) y params (limpia/dedup)."""
    clean = []
    for v in values or []:
        s = (v or "").strip()
        if s and s not in clean:
            clean.append(s)
    if not clean:
        return "", []
    placeholders = ", ".join(["%s"] * len(clean))
    return f"{column} IN ({placeholders})", clean

def _like_any_clause(column: str, values: list):
    """(col ILIKE %s OR col ILIKE %s ...) y params con %valor% (limpia/dedup)."""
    clean = []
    for v in values or []:
        s = (v or "").strip()
        if s and f"%{s}%" not in clean:
            clean.append(f"%{s}%")
    if not clean:
        return "", []
    group = " OR ".join([f"{column} ILIKE %s"] * len(clean))
    return f"({group})", clean

# =========================
# Búsqueda avanzada (q_adv)
# =========================

# Importante: por petición del usuario, la búsqueda libre NO incluye epígrafe/departamento/sección.
DEFAULT_SEARCH_COLUMNS = [
    "i.titulo_resumen",
    "i.titulo",
    "i.identificador",
    "i.control",
]

ADV_FIELDS = {
    # Qualifiers soportados (además de seccion/departamento con tratamiento especial):
    "epigrafe": "i.epigrafe",
    "identificador": "i.identificador",
    "control": "i.control",
    "titulo": "i.titulo",
    "resumen": "i.titulo_resumen",
}

TOKEN_RE = re.compile(r'"([^"]+)"|(\S+)')

def _build_advanced_search(q_adv: str):
    """
    Devuelve (sql_fragment, params) para inyectar en WHERE.
    Soporta:
      - "frase exacta"
      - -excluir
      - campo:valor  (epigrafe, identificador, control, titulo, resumen, seccion*, departamento*)
    Para seccion/departamento: código exacto (i.x_codigo = %s) o nombre ILIKE en join (s/d).
    """
    if not q_adv:
        return "", []

    tokens = []
    for m in TOKEN_RE.finditer(q_adv):
        phrase = m.group(1)
        token = phrase if phrase is not None else m.group(2)
        if token:
            tokens.append(token.strip())

    if not tokens:
        return "", []

    positive, negative, params = [], [], []

    for tok in tokens:
        is_neg = tok.startswith("-")
        t = tok[1:].strip() if is_neg else tok

        if ":" in t:
            field, value = t.split(":", 1)
            field, value = field.lower().strip(), value.strip()
            if not value:
                continue

            if field in ("seccion", "departamento"):
                tbl = "s" if field == "seccion" else "d"
                clause = f"(i.{field}_codigo = %s OR {tbl}.nombre ILIKE %s)"
                (negative if is_neg else positive).append(("NOT " + clause) if is_neg else clause)
                params.extend([value, f"%{value}%"])

            elif field in ADV_FIELDS:
                col = ADV_FIELDS[field]
                clause = f"({col} ILIKE %s)"
                (negative if is_neg else positive).append(("NOT " + clause) if is_neg else clause)
                params.append(f"%{value}%")

            else:
                # Campo desconocido -> trata como libre (solo DEFAULT_SEARCH_COLUMNS)
                group = " OR ".join([f"{c} ILIKE %s" for c in DEFAULT_SEARCH_COLUMNS])
                clause = f"({group})"
                (negative if is_neg else positive).append(("NOT " + clause) if is_neg else clause)
                params.extend([f"%{t}%"] * len(DEFAULT_SEARCH_COLUMNS))

        else:
            # Token libre
            group = " OR ".join([f"{c} ILIKE %s" for c in DEFAULT_SEARCH_COLUMNS])
            clause = f"({group})"
            (negative if is_neg else positive).append(("NOT " + clause) if is_neg else clause)
            params.extend([f"%{t}%"] * len(DEFAULT_SEARCH_COLUMNS))

    clauses = [*positive, *negative]
    if not clauses:
        return "", []

    return "(" + " AND ".join(clauses) + ")", params

# =========================
# Listado con filtros
# =========================

def get_filtered_items(filters, page, limit):
    """
    Filtros soportados (query params):
      - q_adv (búsqueda avanzada; libre NO toca epígrafe/departamento/sección)
      - identificador (ILIKE), control (ILIKE)
      - seccion / secciones (CSV / repetidos)  -> código exacto o nombre ILIKE
      - departamento / departamentos           -> código exacto o nombre ILIKE
      - epigrafe / epigrafes                   -> ILIKE (tolerante)
      - fecha (YYYY-MM-DD) sobre i.created_at::date (exacta)
      - fecha_desde / fecha_hasta              sobre i.created_at::date (rango)
      - sort_by in {created_at, identificador, control, epigrafe, departamento, seccion}
      - sort_dir in {asc, desc}
      - page, limit (limit máx 100)
    """
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

    # ---- Búsqueda avanzada
    q_adv = _norm(filters.get("q_adv"))
    if q_adv:
        adv_sql, adv_params = _build_advanced_search(q_adv)
        if adv_sql:
            add(adv_sql, *adv_params)

    # ---- Filtros textuales simples
    v = _norm(filters.get("identificador"))
    if v:
        add("i.identificador ILIKE %s", f"%{v}%")

    v = _norm(filters.get("control"))
    if v:
        add("i.control ILIKE %s", f"%{v}%")

    # ---- MULTI: SECCIÓN (por código exacto o nombre aproximado)
    secciones = _csv_list(filters, "seccion") or _csv_list(filters, "secciones")
    if secciones:
        by_code_sql, by_code_params = _in_clause("i.seccion_codigo", secciones)
        by_name_sql, by_name_params = _like_any_clause("TRIM(s.nombre)", secciones)
        if by_code_sql and by_name_sql:
            add(f"(({by_code_sql}) OR ({by_name_sql}))", *(by_code_params + by_name_params))
        elif by_code_sql:
            add(by_code_sql, *by_code_params)
        elif by_name_sql:
            add(by_name_sql, *by_name_params)

    # ---- MULTI: DEPARTAMENTO (por código exacto o nombre aproximado)
    departamentos = _csv_list(filters, "departamento") or _csv_list(filters, "departamentos")
    if departamentos:
        by_code_sql, by_code_params = _in_clause("i.departamento_codigo", departamentos)
        by_name_sql, by_name_params = _like_any_clause("TRIM(d.nombre)", departamentos)
        if by_code_sql and by_name_sql:
            add(f"(({by_code_sql}) OR ({by_name_sql}))", *(by_code_params + by_name_params))
        elif by_code_sql:
            add(by_code_sql, *by_code_params)
        elif by_name_sql:
            add(by_name_sql, *by_name_params)

    # ---- MULTI: EPÍGRAFE (ILIKE tolerante)
    epigrafes = _csv_list(filters, "epigrafe") or _csv_list(filters, "epigrafes")
    if epigrafes:
        like_sql, like_params = _like_any_clause("TRIM(i.epigrafe)", epigrafes)
        if like_sql:
            add(like_sql, *like_params)
    else:
        # fallback para un único epígrafe textual (si alguien lo envía así)
        v = _norm(filters.get("epigrafe"))
        if v:
            add("TRIM(i.epigrafe) ILIKE %s", f"%{v}%")

    # ---- Fechas sobre created_at::date
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

    # ---- WHERE final
    where_sql = (" AND " + " AND ".join(where)) if where else ""

    # ---- Orden y paginación
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

    try:
        page = max(int(filters.get("page", page)), 1)
    except Exception:
        page = 1
    try:
        limit = max(min(int(filters.get("limit", limit)), 100), 1)
    except Exception:
        limit = 10
    offset = (page - 1) * limit

    query = f"""{base_select}{where_sql}
                 ORDER BY {sort_by} {sort_dir} NULLS LAST
                 LIMIT %s OFFSET %s"""
    count_query = f"""{base_count}{where_sql}"""

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(count_query, count_params)
        total = cur.fetchone()[0]
        cur.execute(query, [*params, limit, offset])
        items = _dict_rows(cur)

    return {"items": items, "total": total, "page": page, "limit": limit}

# =========================
# Detalle / Resumen / Impacto
# =========================

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

# =========================
# Likes / Dislikes
# =========================

def like_item(identificador):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE items SET likes = COALESCE(likes,0)+1 WHERE identificador = %s RETURNING likes",
            (identificador,),
        )
        row = cur.fetchone()
        conn.commit()
        return {"likes": row[0]} if row else {}

def dislike_item(identificador):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE items SET dislikes = COALESCE(dislikes,0)+1 WHERE identificador = %s RETURNING dislikes",
            (identificador,),
        )
        row = cur.fetchone()
        conn.commit()
        return {"dislikes": row[0]} if row else {}

# =========================
# Lookups auxiliares
# =========================

def list_departamentos():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT codigo, nombre
            FROM departamentos
            WHERE codigo IS NOT NULL AND TRIM(COALESCE(nombre,'')) <> ''
            ORDER BY nombre
        """)
        return [{"codigo": r[0], "nombre": r[1]} for r in cur.fetchall()]

def list_secciones():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT codigo, nombre
            FROM secciones
            WHERE codigo IS NOT NULL AND TRIM(COALESCE(nombre,'')) <> ''
            ORDER BY nombre
        """)
        return [{"codigo": r[0], "nombre": r[1]} for r in cur.fetchall()]

def list_epigrafes():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT TRIM(epigrafe) AS epigrafe
            FROM items
            WHERE TRIM(COALESCE(epigrafe,'')) <> ''
            ORDER BY epigrafe
        """)
        return [r[0] for r in cur.fetchall()]
