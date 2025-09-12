FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# (Opcional pero útil si alguna wheel necesita compilar)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# Forzamos/mostramos PORT para evitar "0.0.0.0:" y arrancamos con WebSocket worker
CMD ["sh", "-c", "PORT=${PORT:-8000}; echo PORT=$PORT; exec gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 -b 0.0.0.0:${PORT} 'app:create_app()'"]
