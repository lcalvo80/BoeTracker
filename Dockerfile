FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

# Railway usará esta variable automáticamente
ENV PORT=5000

# Ejecutar el backend Flask
CMD ["python", "run.py"]
