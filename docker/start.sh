#!/bin/sh
set -eu

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"

cd /app/backend
uvicorn app.main:app --host "${BACKEND_HOST}" --port "${BACKEND_PORT}" &
BACKEND_PID=$!

cleanup() {
  if kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
    wait "${BACKEND_PID}" 2>/dev/null || true
  fi
}

trap cleanup INT TERM EXIT

i=0
until curl -fsS "http://${BACKEND_HOST}:${BACKEND_PORT}/health" >/dev/null; do
  i=$((i + 1))
  if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
    wait "${BACKEND_PID}"
    exit 1
  fi
  if [ "${i}" -ge 60 ]; then
    echo "Backend did not become healthy in time." >&2
    exit 1
  fi
  sleep 1
done

exec nginx -g "daemon off;"
