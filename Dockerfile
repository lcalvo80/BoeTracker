FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# (Opcional pero recomendable para wheels que a veces compilan)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["sh", "-c", "PORT=${PORT:-8000}; echo PORT=$PORT; exec python -m hypercorn --bind 0.0.0.0:${PORT} --workers 1 app.asgi:app"]
