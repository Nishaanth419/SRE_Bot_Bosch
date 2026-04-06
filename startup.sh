#!/bin/sh
# Azure App Service injects $PORT at runtime.
# Fall back to 8000 for local / non-App-Service environments.
exec gunicorn \
  --workers=2 \
  --threads=4 \
  --bind "0.0.0.0:${PORT:-8000}" \
  --timeout=120 \
  --access-logfile=- \
  --error-logfile=- \
  app:app
