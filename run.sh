#!/usr/bin/env bash
# Start DocuSight: FastAPI backend (:8000) + Next.js frontend (:3000).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c "import fastapi, fastembed, duckdb, pymupdf, sentence_transformers" 2>/dev/null; then
  echo "Installing backend dependencies…"
  .venv/bin/pip install -q -r backend/requirements.txt
  echo "Tip: for ColPali visual retrieval also run: .venv/bin/pip install -r backend/requirements-colpali.txt"
fi
if [ ! -d frontend/node_modules ]; then
  echo "Installing frontend dependencies…"
  (cd frontend && npm install)
fi

trap 'kill 0' EXIT
(cd backend && ../.venv/bin/python -X faulthandler -m uvicorn app.main:app --host 127.0.0.1 --port 8000) &
(cd frontend && npm run dev -- --port 3000) &
echo "DocuSight → http://localhost:3000   (API: http://localhost:8000/docs)"
wait
