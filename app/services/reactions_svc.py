# app/services/reactions_svc.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict

from flask import current_app

from app.services.postgres import get_db

LIKE = 1
DISLIKE = -1


@dataclass
class ReactionResult:
    item_id: str
    user_id: str
    previous: Optional[int]  # 1, -1 o None
    current: int             # 1 o -1
    changed: bool
    counts: Dict[str, int]


def set_reaction(item_id: str, *, user_id: str, reaction: int) -> ReactionResult:
    """
    Garantiza 1 reacción por (item_id, user_id) mediante UNIQUE constraint.

    Semántica:
    - Si no existía reacción -> inserta.
    - Si existía la misma -> idempotente (no cambia contadores).
    - Si existía la contraria -> actualiza.
    """
    if not item_id or not str(item_id).strip():
        raise ValueError("item_id is required")
    if not user_id or not str(user_id).strip():
        raise ValueError("user_id is required")
    if reaction not in (LIKE, DISLIKE):
        raise ValueError("reaction must be 1 (like) or -1 (dislike)")

    item_id = str(item_id).strip()
    user_id = str(user_id).strip()

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # 1) previous
                cur.execute(
                    """
                    SELECT reaction
                    FROM item_reactions
                    WHERE item_id = %s AND user_id = %s
                    """,
                    (item_id, user_id),
                )
                row = cur.fetchone()
                previous = int(row[0]) if row else None

                changed = False
                if previous != reaction:
                    cur.execute(
                        """
                        INSERT INTO item_reactions (item_id, user_id, reaction)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (item_id, user_id)
                        DO UPDATE SET reaction = EXCLUDED.reaction, updated_at = NOW()
                        """,
                        (item_id, user_id, reaction),
                    )
                    changed = True

                # 2) counts
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
                likes, dislikes = cur.fetchone() or (0, 0)
                counts = {"likes": int(likes), "dislikes": int(dislikes)}

        return ReactionResult(
            item_id=item_id,
            user_id=user_id,
            previous=previous,
            current=reaction,
            changed=changed,
            counts=counts,
        )
    except Exception:
        current_app.logger.exception("set_reaction failed")
        raise


def get_counts(item_id: str) -> Dict[str, int]:
    if not item_id or not str(item_id).strip():
        raise ValueError("item_id is required")
    item_id = str(item_id).strip()

    with get_db() as conn:
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
            likes, dislikes = cur.fetchone() or (0, 0)
            return {"likes": int(likes), "dislikes": int(dislikes)}


def sync_items_counters_if_present(item_id: str) -> None:
    """
    Opcional: si tu tabla items tiene columnas likes/dislikes, sincroniza.
    Si no existen, no rompe (capturamos error).
    """
    if not item_id or not str(item_id).strip():
        return
    item_id = str(item_id).strip()

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE items
                    SET
                      likes = (SELECT COUNT(*) FROM item_reactions r WHERE r.item_id = items.identificador AND r.reaction = 1),
                      dislikes = (SELECT COUNT(*) FROM item_reactions r WHERE r.item_id = items.identificador AND r.reaction = -1)
                    WHERE identificador = %s
                    """,
                    (item_id,),
                )
    except Exception:
        # Cache best-effort (no rompe requests)
        current_app.logger.debug("sync_items_counters_if_present skipped/failed", exc_info=True)
