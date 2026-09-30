#!/bin/bash
# Entry point for the Windows scheduled task (see OVERVIEW / README): scan, then
# email only openings that are new since the last email.
# Quiet hours are enforced here, not just by the trigger times: a run missed
# while the laptop slept fires when it wakes, and that must not be at night.
QUIET_FROM=22   # no runs from 22:00 ...
QUIET_TO=8      # ... until 08:00
cd "$(dirname "$0")" || exit 1
h=$((10#$(date +%H)))
if [ "$h" -ge "$QUIET_FROM" ] || [ "$h" -lt "$QUIET_TO" ]; then
  echo "$(date '+%F %T') scheduled run skipped: quiet hours" >> jobradar.log
  exit 0
fi
echo "$(date '+%F %T') scheduled run" >> jobradar.log
exec ./run.sh run --new-only
