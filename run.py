# run.py
import os
from app import create_app

app = create_app()

if __name__ == "__main__":
    # Uso local / desarrollo:
    #   python run.py
    # Producción (Railway): ARRANCAR CON HYPERCORN (ver abajo)
    port = int(os.environ.get("PORT", 8080))
    # Debug opcional en desarrollo
    app.run(host="0.0.0.0", port=port, debug=True)
