#!/usr/bin/env bash
# uninstall.sh — removes the Calendar Post-It launchd service
set -euo pipefail

PLIST_LABEL="com.user.calendar-postit"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

echo ""
echo "Uninstalling Calendar Post-It..."

if [[ -f "$PLIST_DST" ]]; then
  launchctl unload "$PLIST_DST" 2>/dev/null && echo "✓  Service stopped" || true
  rm -f "$PLIST_DST" && echo "✓  Plist removed"
else
  echo "ℹ️   Service plist not found (already removed?)"
fi

# Kill any running instance
pkill -f "calendar_postit.py" 2>/dev/null && echo "✓  Process killed" || true

echo ""
echo "✅  Calendar Post-It uninstalled."
echo "    (credentials.json and token.json are left in place — delete them manually if wanted)"
echo ""
