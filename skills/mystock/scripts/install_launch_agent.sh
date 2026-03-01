#!/usr/bin/env bash
set -euo pipefail

LABEL="ai.openclaw.mystock.daily"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_SCRIPT="$ROOT_DIR/mystock/scripts/run_daily_dashboard.sh"
LOG_DIR="$ROOT_DIR/mystock/data"
STDOUT_LOG="$LOG_DIR/daily_dashboard.stdout.log"
STDERR_LOG="$LOG_DIR/daily_dashboard.stderr.log"

mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${RUN_SCRIPT}</string>
  </array>

  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>2</integer>
    <key>Minute</key>
    <integer>5</integer>
  </dict>

  <key>WorkingDirectory</key>
  <string>${ROOT_DIR}</string>

  <key>StandardOutPath</key>
  <string>${STDOUT_LOG}</string>

  <key>StandardErrorPath</key>
  <string>${STDERR_LOG}</string>

  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "installed: $PLIST"
launchctl list | rg "$LABEL" || true
