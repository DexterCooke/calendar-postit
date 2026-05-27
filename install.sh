#!/usr/bin/env bash
# install.sh — sets up Calendar Post-It as a macOS LaunchAgent
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.calendar-postit"          # ← outside Desktop (no sandbox block)
PLIST_LABEL="com.user.calendar-postit"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
LOG_OUT="/tmp/calendar-postit-stdout.log"
LOG_ERR="/tmp/calendar-postit-stderr.log"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     Calendar Post-It  Installer      ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── 1. Check for credentials.json ──────────────────────────────────────────────
if [[ ! -f "$SCRIPT_DIR/credentials.json" ]]; then
  echo "❌  credentials.json not found in:"
  echo "      $SCRIPT_DIR"
  echo ""
  echo "    Steps to get it:"
  echo "      1. Go to https://console.cloud.google.com"
  echo "      2. Create (or select) a project"
  echo "      3. Enable the Google Calendar API"
  echo "      4. Go to APIs & Services → Credentials"
  echo "      5. Create OAuth 2.0 Client ID (Desktop app)"
  echo "      6. Download JSON → rename to credentials.json"
  echo "      7. Place it in: $SCRIPT_DIR"
  echo ""
  exit 1
fi
echo "✓  credentials.json found"

# ── 2. Find Python 3 ───────────────────────────────────────────────────────────
PYTHON_BIN=""
for candidate in python3 python3.12 python3.11 python3.10 python3.9; do
  if command -v "$candidate" &>/dev/null; then
    PYTHON_BIN="$(command -v "$candidate")"
    break
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  echo "❌  python3 not found. Install Python 3.10+ from https://python.org"
  exit 1
fi
echo "✓  Python: $PYTHON_BIN  ($(${PYTHON_BIN} --version 2>&1))"

# ── 3. Install Python dependencies ─────────────────────────────────────────────
echo ""
echo "📦  Installing Python dependencies..."
"$PYTHON_BIN" -m pip install --quiet --upgrade \
  google-auth-oauthlib \
  google-auth-httplib2 \
  google-api-python-client \
  PyQt6
echo "✓  Dependencies installed"

# ── 4. Copy app to ~/.calendar-postit (avoids macOS Desktop sandbox block) ─────
echo ""
echo "📂  Installing app to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/calendar_postit.py" "$INSTALL_DIR/calendar_postit.py"
cp "$SCRIPT_DIR/credentials.json"   "$INSTALL_DIR/credentials.json"
# Copy existing token if already authorised
[[ -f "$SCRIPT_DIR/token.json" ]] && cp "$SCRIPT_DIR/token.json" "$INSTALL_DIR/token.json"
echo "✓  App files copied"

# ── 5. First-time OAuth (opens browser) ────────────────────────────────────────
echo ""
echo "🌐  Authorising Google Calendar access..."
echo "    (A browser window will open — sign in and click Allow)"
echo ""
cd "$INSTALL_DIR"
"$PYTHON_BIN" - <<PYEOF
import sys
sys.path.insert(0, "$INSTALL_DIR")
from calendar_postit import get_calendar_service
get_calendar_service()
print("✓  Authorisation saved to token.json")
PYEOF

# ── 6. Write launchd plist ─────────────────────────────────────────────────────
echo ""
echo "📋  Installing launchd service..."
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_DST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${PLIST_LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>${INSTALL_DIR}/calendar_postit.py</string>
  </array>

  <!-- Start automatically on login -->
  <key>RunAtLoad</key>
  <true/>

  <!-- Restart if it crashes -->
  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>${LOG_OUT}</string>
  <key>StandardErrorPath</key>
  <string>${LOG_ERR}</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key>
    <string>${HOME}</string>
  </dict>
</dict>
</plist>
PLIST

# ── 7. Load the service ────────────────────────────────────────────────────────
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load   "$PLIST_DST"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  ✅  Calendar Post-It is running!                    ║"
echo "║                                                      ║"
echo "║  • A sticky-note icon appears in your menu bar       ║"
echo "║  • Popups appear 10 min before each meeting          ║"
echo "║  • Drag a note to reposition it                      ║"
echo "║  • Click \"Got it ✓\" to dismiss                       ║"
echo "║                                                      ║"
echo "║  App lives at: ~/.calendar-postit/                   ║"
echo "║  Logs: $LOG_OUT"
echo "║                                                      ║"
echo "║  To uninstall:  bash uninstall.sh                    ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
