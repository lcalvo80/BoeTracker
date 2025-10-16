from __future__ import annotations

import os
import requests
from flask import Blueprint, jsonify, g, current_app
from app.auth import require_clerk_auth
from app.services.postgres import get_db

bp = Blueprint("account", __name__, url_prefix="/api")

def _headers_json():
    sk = current_app.config.get("CLERK_SECRET_KEY") or os.getenv("CLERK_SECRET_KEY")
    if not sk:
        raise RuntimeError("Missing CLERK_SECRET_KEY")
    return {"Authorization": f"Bearer {sk}", "Content-Type": "application/json"}

@bp.delete("/me")
@require_clerk_auth
def delete_me():
    """Borra datos propios y opcionalmente el usuario en Clerk (ALLOW_CLERK_USER_DELETE=1)."""
    user_id = getattr(g, "clerk", {}).get("user_id")
    if not user_id:
        return jsonify(error="unauthorized"), 401

    deleted = 0
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM comments WHERE user_id = %s", (user_id,))
            deleted = cur.rowcount or 0
            conn.commit()
    except Exception as e:
        current_app.logger.exception("[account] delete comments failed: %s", e)

    allow = (os.getenv("ALLOW_CLERK_USER_DELETE", "") or "").strip().lower() in ("1","true","yes")
    if allow:
        try:
            url = f"https://api.clerk.com/v1/users/{user_id}"
            res = requests.delete(url, headers=_headers_json(), timeout=10)
            res.raise_for_status()
        except Exception as e:
            current_app.logger.warning("[account] clerk delete user skipped/failed: %s", e)

    return jsonify({"ok": True, "deleted_comments": deleted}), 200
