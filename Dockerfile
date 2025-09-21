FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencias del sistema mínimas
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Requisitos Python
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copiar app
COPY . .

# Usuario no-root
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

# Puerto "documentativo"
EXPOSE 8000

# Variables de concurrencia con defaults sensatos
#   - WEB_CONCURRENCY: workers de Gunicorn
#   - PORT: puerto; fallback a 8000 si no viene inyectado (e.g. local)
ENV WEB_CONCURRENCY=2

# Importante:
#  - Usa expansión de shell para fallback de puerto: ${PORT:-8000}
#  - Carga la factory directamente: "app:create_app()"
#  - Worker WebSocket: geventwebsocket.gunicorn.workers.GeventWebSocketWorker
CMD gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
  --workers ${WEB_CONCURRENCY:-2} \
  --bind 0.0.0.0:${PORT:-8000} \
  --timeout 120 \
  --access-logfile - \
  --error-logfile - \
  "app:create_app()"
