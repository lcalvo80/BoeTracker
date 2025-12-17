# app/services/reactions_svc.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any

from app.services.postgres import get_db


@dataclass
class ReactionCounts:
    likes: int
    dislikes: int


def _ensure_schema(conn) -> None:
    """
    Crea la tabla si no existe.
    Es seguro llamarlo; en producción idealmente va como migración.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS item_reactions (
              item_id   TEXT NOT NULL,
              user_id   TEXT NOT NULL,
              reaction  SMALLINT NOT NULL CHECK (reaction IN (-1, 1)),
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              PRIMARY KEY (item_id, user_id)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_item_reactions_item
            ON item_reactions (item_id);
            """
        )


def _get_counts(conn, item_id: str) -> ReactionCounts:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN reaction = 1 THEN 1 ELSE 0 END), 0) AS likes,
              COALESCE(SUM(CASE WHEN reaction = -1 THEN 1 ELSE 0 END), 0) AS dislikes
            FROM item_reactions
            WHERE item_id = %s
            """,
            (item_id,),
        )
        row = cur.fetchone() or (0, 0)
        return ReactionCounts(likes=int(row[0] or 0), dislikes=int(row[1] or 0))


def _get_my_reaction(conn, item_id: str, user_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT reaction FROM item_reactions WHERE item_id=%s AND user_id=%s",
            (item_id, user_id),
        )
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0


def set_reaction(*, item_id: str, user_id: str, reaction: int) -> Dict[str, Any]:
    """
    reaction: 1 (like) o -1 (dislike)

    Regla:
      - Si el usuario ya tenía la misma reacción: se elimina (toggle off).
      - Si tenía la contraria: se actualiza.
      - Si no tenía: se inserta.
    """
    if reaction not in (1, -1):
        raise ValueError("reaction must be 1 or -1")
    item_id = (item_id or "").strip()
    user_id = (user_id or "").strip()
    if not item_id:
        raise ValueError("item_id requerido")
    if not user_id:
        raise ValueError("user_id requerido")

    with get_db() as conn:
        _ensure_schema(conn)

        prev = _get_my_reaction(conn, item_id, user_id)

        with conn.cursor() as cur:
            if prev == reaction:
                # toggle off
                cur.execute(
                    "DELETE FROM item_reactions WHERE item_id=%s AND user_id=%s",
                    (item_id, user_id),
                )
                my_reaction = 0
            elif prev == 0:
                # insert
                cur.execute(
                    """
                    INSERT INTO item_reactions (item_id, user_id, reaction)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (item_id, user_id)
                    DO UPDATE SET reaction=EXCLUDED.reaction, updated_at=NOW()
                    """,
                    (item_id, user_id, reaction),
                )
                my_reaction = reaction
            else:
                # switch
                cur.execute(
                    """
                    UPDATE item_reactions
                    SET reaction=%s, updated_at=NOW()
                    WHERE item_id=%s AND user_id=%s
                    """,
                    (reaction, item_id, user_id),
                )
                my_reaction = reaction

        counts = _get_counts(conn, item_id)

        return {
            "ok": True,
            "item_id": item_id,
            "likes": counts.likes,
            "dislikes": counts.dislikes,
            "my_reaction": my_reaction,  # 1, -1 o 0
        }
