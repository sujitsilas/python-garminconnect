#!/bin/bash
# Install the launchd job that refreshes the dashboard data daily at 06:00.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.garmin.dashboard.refresh"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$SCRIPT_DIR/refresh.log"

chmod +x "$SCRIPT_DIR/refresh_dashboard.sh"
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s#__SCRIPT__#$SCRIPT_DIR/refresh_dashboard.sh#g" \
    -e "s#__LOG__#$LOG#g" \
    "$SCRIPT_DIR/$LABEL.plist" > "$DEST"

launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"
echo "Installed $LABEL — runs daily at 06:00."
echo "Log: $LOG"
echo "Run now to test:  launchctl start $LABEL"
echo "Uninstall:        launchctl unload $DEST && rm $DEST"
