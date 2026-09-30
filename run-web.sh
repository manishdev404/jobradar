#!/bin/bash
# Starts the web UI. Secrets come from .env via load-env.sh, shared with run.sh.
cd "$(dirname "$0")" || exit 1
. ./load-env.sh
export PYTHONIOENCODING=utf-8
UV=./.venv/bin/uvicorn; [ -x "$UV" ] || UV=./.venv/Scripts/uvicorn
exec "$UV" jobradar.web.server:app --host 127.0.0.1 --port 8765 "$@"
