#!/bin/bash
# run.sh

# Cargar variables de entorno del .env
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# Arrancar Flask en modo desarrollo
export FLASK_APP=run.py
export FLASK_ENV=development
flask run --port 8000
