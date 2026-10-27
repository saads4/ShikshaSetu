#!/bin/sh
# Container entrypoint for hosted deploys (Render etc.). Picks the process from $SERVICE and binds to $PORT.
#   SERVICE=api    -> core API, plus the worker and the mock registry/SMS service in the same container
#   SERVICE=verify -> verify-api only
# docker-compose.yml overrides the command per service, so it does not use this script.
PORT="${PORT:-8000}"
case "${SERVICE:-api}" in
  verify)
    exec uvicorn verify_api.main:app --host 0.0.0.0 --port "$PORT"
    ;;
  *)
    uvicorn mock_services.main:app --host 127.0.0.1 --port 8002 &
    python -m app.cli worker &
    exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
    ;;
esac
