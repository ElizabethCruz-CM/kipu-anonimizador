#!/usr/bin/env bash
# Levanta el servicio del anonimizador. Railway/Render ponen PORT; en local usa 9000.
set -e
cd "$(dirname "$0")"
pip install -q -r requirements.txt 2>/dev/null || true
exec python3 -m uvicorn app.server:app --host 0.0.0.0 --port "${PORT:-9000}"
