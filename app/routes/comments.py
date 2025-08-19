# app/controllers/comments_controller.py
from __future__ import annotations

from typing import Any, Dict, List
from datetime import datetime

from app.services.postgres import get_db


def _iso(dt: Any) -> Any:
    """Convierte a ISO 8601 si es datetime; deja pasar el resto."""
    if isinstance(dt, datetime):
        return dt.isoformat()
    return dt


def get_comments_by_item(item_id: int) -> List[Dict[str, Any]]:
    """
    Devuelve la lista de comentarios de un item ordenados por fecha de creación (ASC).
    No levanta error si no hay comentarios; retorna lista vacía.
    """
    # Seguridad: fuerza tipo int para prevenir inyección vía bindings posicionales
    item_id = int(item_id)

    sql = """
        SELECT id, item_id, text, created_at
        FROM comments
        WHERE item_id = %s
        ORDER BY created_at ASC, id ASC
    """

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (item_id,))
            rows = cur.fetchall() or []

    # Map explícito para ser compatible con psycopg/psycopg2
    result = [
        {
            "id": r[0],
            "item_id": r[1],
            "text": r[2],
            "created_at": _iso(r[3]),
        }
        for r in rows
    ]
    return result


def add_comment(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Crea un comentario y devuelve el registro insertado.
    Espera: payload = {"item_id": int, "text": str}
    Lanza ValueError en validaciones de dominio.
    """
    if not isinstance(payload, dict):
        raise ValueError("Payload inválido")

    item_id = payload.get("item_id")
    text = (payload.get("text") or "").strip()

    # Validaciones mínimas (las rutas ya validan, pero reforzamos aquí)
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        raise ValueError("item_id debe ser numérico")

    if not text:
        raise ValueError("text es requerido")

    # Si necesitas validar que el item exista en DB, descomenta:
    # _ensure_item_exists(item_id)

    sql = """
        INSERT INTO comments (item_id, text)
        VALUES (%s, %s)
        RETURNING id, item_id, text, created_at
    """

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (item_id, text))
            row = cur.fetchone()
        conn.commit()

    created = {
        "id": row[0],
        "item_id": row[1],
        "text": row[2],
        "created_at": _iso(row[3]),
    }
    return created


# --- Opcional: valida existencia del item (útil si hay FK o quieres 400 si no existe) ---
def _ensure_item_exists(item_id: int) -> None:
    """
    Lanza ValueError si el item no existe. Úsalo desde add_comment
    si quieres forzar la existencia del item en BD.
    """
    sql = "SELECT 1 FROM items WHERE id = %s LIMIT 1"
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (item_id,))
            ok = cur.fetchone()
    if not ok:
        raise ValueError(f"El item {item_id} no existe")
