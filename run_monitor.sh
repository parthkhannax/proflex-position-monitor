#!/bin/bash
# Proflex Position Monitor cron wrapper.
# Pulls latest (avoids non-fast-forward push), runs monitor, which fetches
# live prices, writes data/status.json, and auto commits+pushes to GitHub.
# GitHub Pages then redeploys the live dashboard automatically.
set -o pipefail

REPO_DIR="/Users/parthkhanna/Desktop/claude-work/proflex-monitor"
PYTHON="/opt/anaconda3/bin/python3"
LOG="$REPO_DIR/monitor_cron.log"

cd "$REPO_DIR" || exit 1

echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') =====" >> "$LOG"

# Ensure commits use Parth's identity (system identity breaks Vercel/Pages).
git config user.name  "parthkhannax"                      >> "$LOG" 2>&1
git config user.email "parthbusinessofficialid@gmail.com" >> "$LOG" 2>&1

# Sync before running so the monitor's push is a fast-forward.
git pull --rebase --autostash origin main >> "$LOG" 2>&1

"$PYTHON" monitor.py >> "$LOG" 2>&1

echo "----- exit $? -----" >> "$LOG"
