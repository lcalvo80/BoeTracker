# app/blueprints/contact.py
from __future__ import annotations

import os
import re
from flask import Blueprint, jsonify, request, current_app

bp = Blueprint("contact", __name__, url_prefix="/api")

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")


def _send_resend_email(to_email: str, subject: str, text: str) -> None:
    import requests

    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing RESEND_API_KEY")

    sender = os.environ.get("RESEND_FROM", "BOEtracker <onboarding@resend.dev>").strip()

    payload = {
        "from": sender,
        "to": [to_email],
        "subject": subject,
        "text": text,
    }

    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=12,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Resend error {r.status_code}: {r.text}")


@bp.post("/contact")
def contact_send():
    data = request.get_json(silent=True) or {}

    email = str(data.get("email", "")).strip()
    subject = str(data.get("subject", "")).strip() or "Mensaje desde BOEtracker"
    role = str(data.get("role", "")).strip()
    source = str(data.get("source", "")).strip()
    message = str(data.get("message", "")).strip()

    if not EMAIL_RE.match(email):
        return jsonify({"error": "Invalid email"}), 400
    if len(message) < 10:
        return jsonify({"error": "Message too short"}), 400

    to_email = os.environ.get("CONTACT_TO_EMAIL", "").strip()
    if not to_email:
        return jsonify({"error": "Missing CONTACT_TO_EMAIL"}), 500

    text = "\n".join(
        [
            f"Email: {email}",
            f"Perfil: {role or 'unknown'}",
            f"Source: {source or 'direct'}",
            "",
            "Mensaje:",
            message,
        ]
    )

    try:
        _send_resend_email(to_email, subject, text)
    except Exception:
        current_app.logger.exception("contact_send_failed")
        return jsonify({"error": "Failed to send"}), 502

    return jsonify({"ok": True}), 200