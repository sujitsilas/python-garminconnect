#!/bin/bash
# Incrementally refresh the local dashboard data (run by launchd each morning).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO/.venv/bin/activate"
cd "$REPO/dashboard"
echo "=== $(date) refreshing ==="
python fetch_data.py --refresh-today
echo "=== done ==="
