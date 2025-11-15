# app/services/openai_client.py
from __future__ import annotations

import os

from openai import OpenAI

"""
Cliente centralizado de OpenAI y configuración de modelos
para las distintas APIs (título, resumen, impacto).

Variables de entorno usadas:

- OPENAI_API_KEY           (obligatoria)
- OPENAI_MODEL             (modelo por defecto, ej: gpt-4o)
- OPENAI_MODEL_TITLE       (opcional, por defecto = OPENAI_MODEL)
- OPENAI_MODEL_SUMMARY     (opcional, por defecto = OPENAI_MODEL)
- OPENAI_MODEL_IMPACT      (opcional, por defecto = OPENAI_MODEL)
- OPENAI_TIMEOUT           (segundos, por defecto 45)
- OPENAI_SEED              (semilla, por defecto 7)
"""

# ───────────────── Config modelos ─────────────────

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

OPENAI_MODEL_TITLE = os.getenv("OPENAI_MODEL_TITLE", OPENAI_MODEL)
OPENAI_MODEL_SUMMARY = os.getenv("OPENAI_MODEL_SUMMARY", OPENAI_MODEL)
OPENAI_MODEL_IMPACT = os.getenv("OPENAI_MODEL_IMPACT", OPENAI_MODEL)

OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "45"))
OPENAI_SEED = int(os.getenv("OPENAI_SEED", "7"))

# ───────────────── Cliente ─────────────────

api_key = os.getenv("OPENAI_API_KEY")
# No explotamos aquí si falta; update_boe.py ya hace check estricto.
client = OpenAI(api_key=api_key) if api_key else OpenAI()
