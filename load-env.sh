#!/bin/bash
# Sourced by run.sh, run-web.sh and bench-models.sh: exports each KEY=value in .env.
# Parsed, not sourced, so values with spaces (Gmail App Passwords) need no quoting,
# and CRs are stripped so a .env saved on Windows still loads.
if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]] || continue
    key="${BASH_REMATCH[2]}"; val="${BASH_REMATCH[3]}"
    val="${val%"${val##*[![:space:]]}"}"          # trailing whitespace
    if [[ "$val" =~ ^\"(.*)\"$ || "$val" =~ ^\'(.*)\'$ ]]; then
      val="${BASH_REMATCH[1]}"
    else
      val="${val%%[[:space:]]#*}"                 # trailing " # comment"
      val="${val%"${val##*[![:space:]]}"}"        # trailing whitespace
    fi
    export "$key=$val"
  done < .env
fi
# GROK_API is accepted as an alias; the key's prefix says which service it is.
case "${GROK_API:-}" in
  xai-*) export XAI_API_KEY="${XAI_API_KEY:-$GROK_API}" ;;
  gsk_*) export GROQ_API_KEY="${GROQ_API_KEY:-$GROK_API}" ;;
esac
