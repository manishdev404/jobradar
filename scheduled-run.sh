#!/bin/bash
# Entry point for the Windows scheduled task "JobRadar - scan and email": scan,
# then email only openings that are new since the last email (at most
# digest.max_jobs of them, best-scored first).
# The task runs every 2 hours from 09:00 to 17:00, plus 22:00 and 06:00.
# Those times live in the task's triggers, so there are
# no quiet hours here.
cd "$(dirname "$0")" || exit 1
echo "$(date '+%F %T') scheduled run" >> jobradar.log
exec ./run.sh run --new-only
