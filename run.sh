#!/usr/bin/env bash
# run.sh — wrapper called by cron every minute; runs the digest at most once per day
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# --- Once-per-day guard ---
STAMP_FILE="$DIR/.last_run_date"
TODAY=$(date '+%Y-%m-%d')

if [ -f "$STAMP_FILE" ] && [ "$(cat "$STAMP_FILE")" = "$TODAY" ]; then
    exit 0
fi

# Don't run before 7am — wait until laptop is awake during market-relevant hours
HOUR=$(date '+%H')
if [ "$HOUR" -lt 7 ]; then
    exit 0
fi

# Load .env so cron (which has a minimal env) picks up credentials
set -a
source "$DIR/.env"
set +a

# Ensure homebrew bin is in PATH (needed when launched from LaunchAgent)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# Activate venv
source "$DIR/.venv/bin/activate"

# Mark today as done BEFORE running to prevent overlapping cron invocations
# (main.py takes several minutes; without this, every cron tick spawns a new run)
# Delete .last_run_date to force a re-run if needed.
echo "$TODAY" > "$STAMP_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting Finviz Digest run..."
python "$DIR/main.py"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Run complete."
