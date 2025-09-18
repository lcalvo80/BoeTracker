FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencias del sistema:
# - libpq5 si usas psycopg2-binary no hace falta compilar; si usas psycopg2 normal, añade libpq-dev y gcc
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Si usas psycopg2 (no binary), descomenta:
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     build-essential \
#     libpq-dev \
#     && rm -rf /var/lib/apt/lists/*

# Requisitos
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# App
COPY . .

# (Opcional) usuario no-root
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

# Documentativo
EXPOSE 8000

# Railway inyecta PORT; mantenemos fallback a 8000 para local
# Gunicorn con gevent-websocket
CMD gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker 'app:create_app()' --bind 0.0.0.0:$PORT --timeout 120 --access-logfile - --error-logfile -
