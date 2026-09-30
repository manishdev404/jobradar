#!/bin/bash
# Compare candidate models on YOUR resume and a fixed set of jobs.
# Prints accuracy against known-correct answers, plus wall time per batch,
# so the model choice is made on evidence rather than vibes.
cd "$(dirname "$0")" || exit 1
. ./load-env.sh
PY=./.venv/bin/python; [ -x "$PY" ] || PY=./.venv/Scripts/python
exec "$PY" -m jobradar.bench "$@"
