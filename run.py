# run.py
import os
from app import create_app

app = create_app()

if __name__ == "__main__":
    # ⚡ En local: python run.py
    # 🚀 En Railway: usar Hypercorn (ver Procfile)
    port = int(os.environ.get("PORT", 8000))  # 8000 fallback en local
    debug = os.getenv("DEBUG", "true").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
