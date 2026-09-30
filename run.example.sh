#!/bin/bash
# Copy to run.sh. Secrets go in .env (see .env.example), not here.
cd "$(dirname "$0")" || exit 1
. ./load-env.sh
export PYTHONIOENCODING=utf-8   # log files on Windows default to cp1252 and choke on ⚠ / —

PY=./.venv/bin/python; [ -x "$PY" ] || PY=./.venv/Scripts/python
exec "$PY" -m jobradar.main "${@:-run}" >> jobradar.log 2>&1
